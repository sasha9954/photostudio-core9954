import React, { useEffect, useMemo, useRef, useState } from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";
import { fetchJson } from "../../../services/api";
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
function inferWorldHint(text = "") {
  const t = String(text || "").toLowerCase();
  if (t.includes("поезд")) return "train";
  if (t.includes("клуб")) return "club";
  if (t.includes("улиц") || t.includes("город")) return "city";
  return "generic";
}

function buildDirectorContext(data) {
  return {
    mode: "clip",
    content_type: "music_video",
    user_input: data?.text || data?.directorNote || "",
    audio: {
      has_audio: Boolean(data?.audioUrl || data?.resolvedSource?.mode === "AUDIO"),
      duration_sec: Number(data?.audioDuration || 0),
      has_vocals: true,
    },
    world_hint: inferWorldHint(data?.text || data?.directorNote || ""),
    characters: [
      {
        role: "character_1",
        type: "unknown",
        description: "main character",
      },
    ],
    constraints: {
      max_questions: 3,
    },
  };
}

function buildDirectorConfigFromAnswers(answers) {
  const safeAnswers = answers && typeof answers === "object" ? answers : {};
  if (Object.keys(safeAnswers).length === 0) {
    return {};
  }
  const config = {};

  if (safeAnswers.performance_density === "balanced") {
    config.ia2v_ratio = 0.5;
  }
  if (safeAnswers.performance_density === "atmospheric") {
    config.ia2v_ratio = 0.2;
  }
  if (safeAnswers.performance_density === "performance_heavy") {
    config.ia2v_ratio = 0.8;
  }

  const worldMode = String(safeAnswers.world_mode || "").trim().toLowerCase();
  if (worldMode === "train_only") {
    config.ia2v_locations = ["train"];
    config.i2v_locations = ["train"];
  } else if (worldMode === "train_plus_city") {
    config.ia2v_locations = ["train"];
    config.i2v_locations = ["city"];
  } else if (worldMode === "city_memory_dominant") {
    config.ia2v_locations = ["train"];
    config.i2v_locations = ["city"];
    config.memory_intercut = true;
  } else if (worldMode === "club_dancefloor") {
    config.ia2v_locations = ["club_dancefloor"];
    config.i2v_locations = ["club"];
  } else if (worldMode === "club_bar_backstage") {
    config.ia2v_locations = ["club_bar", "club_backstage"];
    config.i2v_locations = ["club"];
  } else if (worldMode === "club_mixed") {
    config.ia2v_locations = ["club"];
    config.i2v_locations = ["club"];
  }

  const introMode = String(safeAnswers.intro_mode || "").trim().toLowerCase();
  if (introMode === "intro_environment") {
    config.intro_scenes = ["environment_opening"];
  } else if (introMode === "intro_character") {
    config.intro_scenes = ["hero_closeup"];
  } else if (introMode === "intro_action") {
    config.intro_scenes = ["action_start"];
  }

  if (typeof config.ia2v_ratio === "number") {
    config.i2v_ratio = Number((1 - config.ia2v_ratio).toFixed(2));
  }
  return config;
}

function stableJson(value) {
  try {
    return JSON.stringify(value || {});
  } catch {
    return "{}";
  }
}

export default function ComfyNarrativeNode({ id, data }) {
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState("");
  const [directorQuestion, setDirectorQuestion] = useState(null);
  const [directorDone, setDirectorDone] = useState(false);
  const [answers, setAnswers] = useState(data?.directorAnswers && typeof data.directorAnswers === "object" ? data.directorAnswers : {});
  const directorInputSignature = `${String(data?.directorNote || "")}|||${String(data?.text || "")}`;
  const prevDirectorInputSignatureRef = useRef(directorInputSignature);
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

  useEffect(() => {
    if (prevDirectorInputSignatureRef.current === directorInputSignature) {
      return;
    }

    prevDirectorInputSignatureRef.current = directorInputSignature;

    const hasLocalAnswers = Object.keys(answers || {}).length > 0;
    const hasLocalQuestion = !!directorQuestion;
    const hasPersistedAnswers =
      data?.directorAnswers &&
      typeof data.directorAnswers === "object" &&
      Object.keys(data.directorAnswers).length > 0;
    const hasPersistedConfig =
      data?.director_config &&
      typeof data.director_config === "object" &&
      Object.keys(data.director_config).length > 0;

    if (hasLocalQuestion) {
      setDirectorQuestion(null);
    }

    if (hasLocalAnswers) {
      setAnswers({});
    }
    setDirectorDone(false);

    if (hasPersistedAnswers || hasPersistedConfig) {
      data?.onFieldChange?.(id, {
        directorAnswers: {},
        director_config: {},
      });
    }
  }, [directorInputSignature, id, data?.onFieldChange, data?.directorAnswers, data?.director_config]);

  useEffect(() => {
    const persistedAnswers = data?.directorAnswers && typeof data.directorAnswers === "object" ? data.directorAnswers : {};
    setAnswers((prev) => {
      if (JSON.stringify(prev) === JSON.stringify(persistedAnswers)) return prev;
      return persistedAnswers;
    });
  }, [data?.directorAnswers]);

  useEffect(() => {
    const safeAnswers = answers && typeof answers === "object" ? answers : {};
    const mapped = buildDirectorConfigFromAnswers(safeAnswers);

    const currentAnswers =
      data?.directorAnswers && typeof data.directorAnswers === "object"
        ? data.directorAnswers
        : {};

    const currentConfig =
      data?.director_config && typeof data.director_config === "object"
        ? data.director_config
        : {};

    const nextConfig = {
      ...currentConfig,
      ...mapped,
    };

    if (
      stableJson(currentAnswers) === stableJson(safeAnswers)
      && stableJson(currentConfig) === stableJson(nextConfig)
    ) {
      return;
    }

    data?.onFieldChange?.(id, {
      directorAnswers: safeAnswers,
      director_config: nextConfig,
    });
  }, [answers, id, data?.onFieldChange, data?.directorAnswers, data?.director_config]);


  const clipModeByContentType = safeContentType === "music_video";
  const clipModeByDirectorMode = String(data?.directorMode || data?.director_mode || "").trim().toLowerCase() === "clip";
  const clipModeByMode = String(data?.mode || "").trim().toLowerCase() === "clip";
  const isClipMode = clipModeByContentType || clipModeByDirectorMode || clipModeByMode;
  const routeStrategyMode = String(data?.routeStrategyMode || "auto").trim().toLowerCase();
  const safeRouteStrategyMode = ["auto", "preset", "custom_counts"].includes(routeStrategyMode) ? routeStrategyMode : "auto";
  const routeTargetsPerBlock = data?.routeTargetsPerBlock && typeof data.routeTargetsPerBlock === "object"
    ? data.routeTargetsPerBlock
    : { i2v: 4, ia2v: 2, first_last: 2 };
  const selectedPreset =
    ROUTE_STRATEGY_PRESETS.find((x) => x.key === data?.routeStrategyPreset)
    || ROUTE_STRATEGY_PRESETS[0];
  const customI2v = Number(routeTargetsPerBlock?.i2v || 0);
  const customIa2v = Number(routeTargetsPerBlock?.ia2v || 0);
  const customFirstLast = Number(routeTargetsPerBlock?.first_last || 0);
  const customTotal = customI2v + customIa2v + customFirstLast;
  const baseSceneCount = Number(data?.baseSceneCount || 8) || 8;
  const hasRouteTotalWarning = safeRouteStrategyMode === "custom_counts" && customTotal !== baseSceneCount;
  const hasAudioSource = !!connectedContext.sourceByHandle?.audio_in;
  const aiRefs = useMemo(() => ({
    character_1: connectedContext.characterCount > 0,
    location: !!connectedContext.hasLocation,
    props: !!connectedContext.hasProps,
  }), [connectedContext.characterCount, connectedContext.hasLocation, connectedContext.hasProps]);
  const selectedStrategySummary = safeRouteStrategyMode === "auto"
    ? {
      title: "Выбрано: Авто",
      lines: [
        "Gemini сам выбирает i2v / ia2v / первый-последний кадр по аудио, вокалу и драматургии.",
        "Базовая авто-логика сохраняет безопасный баланс и не навязывает ручные targets.",
      ],
    }
    : safeRouteStrategyMode === "preset"
      ? {
        title: `Выбрано: ${selectedPreset.label}`,
        lines: [
          selectedPreset.description,
          "Для 9+ сцен: лишние сцены добавляются как i2v.",
          "Для длинных клипов: стратегия повторяется блоками примерно по 30 секунд.",
          "Targets мягкие: если нет вокала, ia2v не ставится насильно и безопасно заменяется на i2v.",
        ],
      }
      : {
        title: "Выбрано: Ручной режим",
        lines: [
          `На 8 сцен: ${customI2v} i2v / ${customIa2v} ia2v / ${customFirstLast} первый-последний`,
          `Итого: ${customTotal} / 8 сцен`,
          "Для 9+ сцен: лишние сцены добавляются как i2v.",
          "Для длинных клипов: ручной расклад повторяется блоками примерно по 30 секунд.",
          "Targets мягкие: если нет вокала, ia2v не ставится насильно и безопасно заменяется на i2v.",
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

  const applyAIResult = (response) => {
    const mode = String(response?.mode || "").trim().toLowerCase();
    const mappedContentType = mode === "story" ? "story" : "music_video";
    const mappedDirectorMode = mode === "story" ? "story" : "clip";
    const structure = String(response?.structure || "").trim().toLowerCase();
    const i2vUsage = String(response?.i2v_usage || "").trim();
    const ia2vUsage = String(response?.ia2v_usage || "").trim();
    const nextCreativeConfig = {
      ...(data?.creative_config && typeof data.creative_config === "object" ? data.creative_config : {}),
      ai_director: {
        lip_sync: !!response?.lip_sync,
        structure,
        routes: Array.isArray(response?.routes) ? response.routes : [],
        i2v_usage: i2vUsage,
        ia2v_usage: ia2vUsage,
        world: String(response?.world || "").trim(),
      },
    };
    data?.onFieldChange?.(id, {
      directorNote: data?.directorNote,
      aiNarrative: response?.narrative_note,
      contentType: mappedContentType,
      mode: mappedDirectorMode,
      directorMode: mappedDirectorMode,
      ...(String(response?.format || "").trim() ? { format: String(response.format).trim() } : {}),
      creative_config: nextCreativeConfig,
      aiDirectorConfig: response?.director_config || {},
      director_config: response?.director_config || (data?.director_config && typeof data.director_config === "object" ? data.director_config : {}),
      lip_sync: !!response?.lip_sync,
      structure,
      routes: Array.isArray(response?.routes) ? response.routes : [],
    });
  };

  const runDirectorAIWithAnswers = async (nextAnswers) => {
    if (aiLoading) return;
    setAiError("");
    setAiLoading(true);
    try {
      const context = buildDirectorContext(data);
      const json = await fetchJson("/api/director/questions", {
        method: "POST",
        body: {
          context,
          answers_so_far: nextAnswers && typeof nextAnswers === "object" ? nextAnswers : {},
        },
      });
      if (json?.done) {
        setDirectorDone(true);
        setDirectorQuestion(null);
        return;
      }
      if (json?.question && Array.isArray(json.question.options)) {
        setDirectorQuestion(json.question);
        setDirectorDone(false);
      } else {
        setDirectorQuestion(null);
      }
    } catch (error) {
      setAiError(String(error?.message || "AI Director failed"));
      setDirectorQuestion(null);
    } finally {
      setAiLoading(false);
    }
  };

  const handleAnswer = (questionId, value) => {
    const nextAnswers = {
      ...(answers && typeof answers === "object" ? answers : {}),
      [questionId]: value,
    };
    setAnswers(nextAnswers);
    setDirectorQuestion(null);
    requestAnimationFrame(() => {
      runDirectorAIWithAnswers(nextAnswers);
    });
  };

  const runDirectorAI = async () => {
    if (aiLoading || directorDone) return;
    await runDirectorAIWithAnswers(answers);
  };

  const handleAiInterpret = async () => {
    const text = String(data?.directorNote || "").trim();
    if (!text || aiLoading) return;
    setAiError("");
    setAiLoading(true);
    try {
      const response = await fetchJson("/api/director/interpret", {
        method: "POST",
        body: {
          text,
          hasAudio: hasAudioSource,
          refs: aiRefs,
        },
      });
      if (response?.needs_clarification) {
        setAiError("AI clarification is disabled in this node. Используйте блок вопросов ниже.");
        return;
      }
      applyAIResult(response);
    } catch (error) {
      setAiError(String(error?.message || "AI interpret failed"));
    } finally {
      setAiLoading(false);
    }
  };

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
                onChange={(e) => data?.onFieldChange?.(id, {
                  directorNote: e.target.value,
                  directorAnswers: {},
                  director_config: {},
                })}
                placeholder="Например: добавь экшена, сделай мрачнее, усиль конфликт"
                rows={3}
              />
            </label>
            <button
              type="button"
              className="clipSB_btn clipSB_btnPrimary"
              onClick={handleAiInterpret}
              disabled={aiLoading || !String(data?.directorNote || "").trim()}
            >
              {aiLoading ? "AI анализ…" : "🎬 Сформировать через AI"}
            </button>
            {aiError ? <div className="clipSB_narrativeEmptyHint" role="alert">{aiError}</div> : null}
            <section className="clipSB_narrativeSection">
              <div className="clipSB_brainLabel">AI режиссёр</div>
              {!directorQuestion && !directorDone ? (
                <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={runDirectorAI} disabled={aiLoading}>
                  🎬 Начать режиссуру
                </button>
              ) : null}
              {directorQuestion ? (
                <div className="director-question">
                  <div className="question-title">Вопрос {Math.min(Object.keys(answers || {}).length + 1, 3)} из 3</div>
                  <div className="question-title">{directorQuestion.text}</div>
                  <div className="ai-question-options">
                    {(directorQuestion.options || []).map((opt) => {
                      const label = String(opt?.label || opt?.value || "").slice(0, 48);
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          className={`clipSB_btn ai-option-btn ${answers[directorQuestion.id] === opt.value ? "selected" : ""}`.trim()}
                          onClick={() => handleAnswer(directorQuestion.id, opt.value)}
                          title={label}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
              {directorDone ? (
                <div className="director-question">
                  <div className="question-title">Режиссура собрана</div>
                  <div className="clipSB_narrativeContextChips">
                    {Object.entries(answers || {}).map(([key, value]) => (
                      <span key={key} className="clipSB_narrativeContextChip isReady">{key}: {String(value)}</span>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="clipSB_btn clipSB_btnSecondary"
                    onClick={() => {
                      setAnswers({});
                      setDirectorQuestion(null);
                      setDirectorDone(false);
                      data?.onFieldChange?.(id, {
                        directorAnswers: {},
                        director_config: {},
                      });
                    }}
                  >
                    Пересобрать
                  </button>
                </div>
              ) : null}
            </section>

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

            {false && isClipMode ? (
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
                        if (item.key === "auto") {
                          data?.onFieldChange?.(id, { routeStrategyMode: "auto" });
                          return;
                        }
                        if (item.key === "preset") {
                          const preset = selectedPreset || ROUTE_STRATEGY_PRESETS[0];
                          data?.onFieldChange?.(id, {
                            routeStrategyMode: "preset",
                            routeStrategyPreset: preset.key,
                            routeTargetsPerBlock: preset.targets,
                            maxConsecutiveIa2v: preset.maxConsecutiveIa2v,
                          });
                          return;
                        }
                        data?.onFieldChange?.(id, { routeStrategyMode: "custom_counts" });
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
                      {ROUTE_STRATEGY_PRESETS.map((preset) => {
                        const presetActive = safeRouteStrategyMode === "preset" && data?.routeStrategyPreset === preset.key;
                        return (
                          <button
                            key={preset.key}
                            type="button"
                            className={`clipSB_btn clipSB_routeStrategyPresetBtn ${presetActive ? "clipSB_btnPrimary clipSB_routeStrategyPresetBtn--active" : "clipSB_btnSecondary"}`.trim()}
                            title={preset.description}
                            onClick={() => data?.onFieldChange?.(id, {
                              routeStrategyMode: "preset",
                              routeStrategyPreset: preset.key,
                              routeTargetsPerBlock: preset.targets,
                              maxConsecutiveIa2v: preset.maxConsecutiveIa2v,
                            })}
                          >
                            <div>{presetActive ? "✓ " : ""}{preset.label}</div>
                            <small>{preset.description}</small>
                          </button>
                        );
                      })}
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
