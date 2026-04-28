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
  buildScenarioDirectorRequestPayload,
  buildScenarioInputSignatureFromState,
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
const DIRECTOR_ANSWER_LABELS = {};

function normalizeDirectorQuestion(raw, index = 0) {
  const q = raw && typeof raw === "object" ? raw : {};
  const id = String(q.id || q.question_id || q.key || `question_${index + 1}`).trim();
  const label = String(q.label || q.question_text || q.text || q.prompt || "").trim();
  const type = String(q.type || q.question_type || "free_text").trim();
  const options = Array.isArray(q.options) ? q.options : [];
  return {
    ...q,
    id,
    label,
    type,
    options,
    required: q.required !== false,
    expected_answer_type: q.expected_answer_type || q.expectedAnswerType || "",
    min_value: q.min_value ?? q.minValue ?? null,
    max_value: q.max_value ?? q.maxValue ?? null,
  };
}

function stripDirectorDiagnostics(diagnostics) {
  if (!diagnostics || typeof diagnostics !== "object") return {};
  return Object.fromEntries(
    Object.entries(diagnostics).filter(([key]) => !String(key || "").toLowerCase().includes("director"))
  );
}

function stripDirectorRuntimeFromNodeData(nodeData) {
  const safe = nodeData && typeof nodeData === "object" ? { ...nodeData } : {};
  delete safe.directorAnswers;
  delete safe.director_config;
  delete safe.director_contract;
  delete safe.director_package;
  delete safe.director_created_for_signature;
  delete safe.director_summary;
  delete safe.directorSummary;
  delete safe.director_summary_preview;
  delete safe.director_story_understanding;
  delete safe.directorOutput;
  delete safe.storyboardOut;
  delete safe.storyboardPackage;
  delete safe.stageStatuses;
  delete safe.diagnostics;
  return safe;
}

function buildDirectorContractFromConfig(directorConfig) {
  const cfg = directorConfig && typeof directorConfig === "object" ? directorConfig : {};
  const ia2vLocations = Array.isArray(cfg.ia2v_locations) ? cfg.ia2v_locations : [];
  const i2vLocations = Array.isArray(cfg.i2v_locations) ? cfg.i2v_locations : [];
  const hasExplicitWorldSplit = ia2vLocations.length > 0 || i2vLocations.length > 0;
  return {
    source: "ai_director_chat",
    hard_location_binding: hasExplicitWorldSplit,
    world_roles: {
      performance_world: {
        label: String(cfg.performance_world_label || cfg.performance_world || "").trim(),
        allowed_zones: ia2vLocations,
      },
      memory_world: {
        label: String(cfg.memory_world_label || cfg.memory_world || "").trim(),
        allowed_zones: i2vLocations,
      },
    },
    route_location_rules: {
      ia2v: {
        world_role: "performance_world",
        performer_visibility: "required",
        singer_visibility: "required",
        lip_sync_framing: "required",
      },
      i2v: {
        world_role: "memory_world",
        performer_visibility: "optional_or_absent",
        singer_visibility: "offscreen_or_non_dominant",
      },
    },
  };
}

export default function ComfyNarrativeNode({ id, data }) {
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState("");
  const [assistantMessage, setAssistantMessage] = useState("");
  const [dynamicQuestions, setDynamicQuestions] = useState([]);
  const [directorChatDone, setDirectorChatDone] = useState(false);
  const [answers, setAnswers] = useState(data?.directorAnswers && typeof data.directorAnswers === "object" ? data.directorAnswers : {});
  const [staleDirectorState, setStaleDirectorState] = useState(Boolean(data?.directorStale));
  const directorInputSignature = buildScenarioInputSignatureFromState(data || {});
  const storedDirectorSignature = String(
    data?.director_created_for_signature
    || data?.director_contract?.created_for_signature
    || data?.director_package?.created_for_signature
    || data?.storyboardPackage?.diagnostics?.director_created_for_signature
    || ""
  ).trim();
  const directorSignatureMatchesCurrent = !storedDirectorSignature || storedDirectorSignature === directorInputSignature;
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
  const baseDiagnostics = useMemo(() => stripDirectorDiagnostics(data?.diagnostics), [data?.diagnostics]);
  const normalizedQuestions = useMemo(
    () => (Array.isArray(dynamicQuestions) ? dynamicQuestions.map(normalizeDirectorQuestion) : []),
    [dynamicQuestions]
  );
  const requiredQuestions = useMemo(
    () => normalizedQuestions.filter((q) => q.required && q.id),
    [normalizedQuestions]
  );

  const isQuestionAnswered = (question, candidateAnswers) => {
    const q = question && typeof question === "object" ? question : {};
    const type = String(q.type || "free_text").trim().toLowerCase();
    const value = candidateAnswers?.[q.id];
    if (type === "single_choice") return Boolean(String(value || "").trim());
    if (type === "multiple_choice" || type === "multi_choice") return Array.isArray(value) && value.length > 0;
    return typeof value === "string" ? Boolean(value.trim()) : Boolean(value);
  };
  const requiredAnsweredCount = requiredQuestions.filter((q) => isQuestionAnswered(q, answers)).length;
  const unansweredRequiredQuestions = requiredQuestions.filter((q) => !isQuestionAnswered(q, answers));
  const hasUnansweredRequiredQuestions = unansweredRequiredQuestions.length > 0;

  const buildDirectorResetPatch = ({ clearScenarioText = false } = {}) => ({
    assistantMessage: "",
    dynamicQuestions: [],
    directorStale: true,
    director_signature_matches_current: false,
    director_stale_reason: "manual_director_reset",
    directorAnswers: {},
    director_config: {},
    director_contract: {},
    director_package: {},
    director_created_for_signature: "",
    director_summary: "",
    directorSummary: "",
    director_summary_preview: "",
    director_story_understanding: {},
    directorOutput: null,
    storyboardOut: null,
    storyboardPackage: {},
    stageStatuses: {},
    diagnostics: {
      ...baseDiagnostics,
      current_scenario_input_signature: directorInputSignature,
      director_signature_matches_current: false,
      persisted_director_result_reused: false,
    },
    ...(clearScenarioText
      ? {
        directorNote: "",
        text: "",
        aiNarrative: "",
      }
      : {}),
  });

  useEffect(() => {
    if ((data?.contentType || "story") !== safeContentType) {
      data?.onFieldChange?.(id, { contentType: safeContentType });
    }
  }, [data?.contentType, data?.onFieldChange, id, safeContentType]);

  useEffect(() => {
    const signatureChanged = prevDirectorInputSignatureRef.current !== directorInputSignature;
    const shouldInvalidatePersistedDirector = signatureChanged || !directorSignatureMatchesCurrent;
    if (!shouldInvalidatePersistedDirector) {
      return;
    }

    prevDirectorInputSignatureRef.current = directorInputSignature;

    const hasLocalAnswers = Object.keys(answers || {}).length > 0;
    const hasLocalQuestions = Array.isArray(dynamicQuestions) && dynamicQuestions.length > 0;
    const hasPersistedAnswers =
      data?.directorAnswers &&
      typeof data.directorAnswers === "object" &&
      Object.keys(data.directorAnswers).length > 0;
    const hasPersistedConfig =
      data?.director_config &&
      typeof data.director_config === "object" &&
      Object.keys(data.director_config).length > 0;

    if (hasLocalQuestions) setDynamicQuestions([]);

    if (hasLocalAnswers) {
      setAnswers({});
    }
    setAssistantMessage("");
    setDirectorChatDone(false);
    setStaleDirectorState(true);

    if (hasPersistedAnswers || hasPersistedConfig || !directorSignatureMatchesCurrent) {
      data?.onFieldChange?.(id, {
        directorStale: true,
        director_signature_matches_current: false,
        downstream_cleared_due_to_input_change: true,
        director_stale_reason: "scenario_input_signature_mismatch",
        directorAnswers: {},
        director_config: {},
        director_contract: {},
        director_package: {},
        directorOutput: null,
        storyboardOut: null,
        storyboardPackage: {},
        stageStatuses: {},
        diagnostics: {
          current_scenario_input_signature: directorInputSignature,
          stored_director_signature: storedDirectorSignature,
          director_signature_matches_current: false,
          downstream_cleared_due_to_input_change: true,
        },
        director_summary: "",
      });
    }
  }, [directorInputSignature, id, data?.onFieldChange, data?.directorAnswers, data?.director_config, answers, dynamicQuestions, directorSignatureMatchesCurrent, storedDirectorSignature]);

  useEffect(() => {
    const persistedAnswers = data?.directorAnswers && typeof data.directorAnswers === "object" ? data.directorAnswers : {};
    setAnswers((prev) => {
      if (JSON.stringify(prev) === JSON.stringify(persistedAnswers)) return prev;
      return persistedAnswers;
    });
  }, [data?.directorAnswers]);


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

  const runDirectorChat = async ({ phase = "init", answerPatch = {}, allowFallback = true }) => {
    if (aiLoading) return;
    setAiError("");
    setAiLoading(true);
    try {
      const directorPayload = buildScenarioDirectorRequestPayload(data || {}) || {};
      const isManualInit = phase === "init";
      const canReuseDirectorArtifacts = !isManualInit && directorSignatureMatchesCurrent;
      const directorNoteText = String(data?.directorNote || "").trim();
      const storyTextValue = String(data?.text || "").trim();
      const nextAnswers = {
        ...(canReuseDirectorArtifacts && answers && typeof answers === "object" ? answers : {}),
        ...(answerPatch && typeof answerPatch === "object" ? answerPatch : {}),
      };
      const submittedAnswers = Object.fromEntries(
        Object.entries(nextAnswers || {}).filter(([key]) => Boolean(String(key || "").trim()))
      );
      const body = {
        ...directorPayload,
        mode: "director_v2",
        phase,
        narrative_note: directorNoteText,
        story_text: directorNoteText || storyTextValue,
        director_note: directorNoteText,
        content_type: safeContentType,
        director_mode: String(data?.directorMode || data?.director_mode || "clip").trim() || "clip",
        format: String(data?.format || "9:16").trim() || "9:16",
        route_strategy: {
          mode: safeRouteStrategyMode,
          preset: String(data?.routeStrategyPreset || "").trim(),
        },
        routeTargetsPerBlock,
        refs_by_role: connectedContext?.refsByRole || {},
        directorAnswers: isManualInit ? {} : submittedAnswers,
        director_config: isManualInit ? {} : (data?.director_config && typeof data.director_config === "object" ? data.director_config : {}),
        director_contract: canReuseDirectorArtifacts && data?.director_contract && typeof data.director_contract === "object" ? data.director_contract : {},
        director_package: canReuseDirectorArtifacts && data?.director_package && typeof data.director_package === "object" ? data.director_package : {},
        current_scenario_input_signature: directorInputSignature,
        force_regenerate: isManualInit,
        full_node_payload: isManualInit
          ? stripDirectorRuntimeFromNodeData(data)
          : (data && typeof data === "object" ? data : {}),
      };
      console.debug("[AI DIRECTOR V2 REQUEST]", {
        director_note_sent: body?.director_note || "",
        story_text_sent: body?.story_text || "",
        force_regenerate: Boolean(body?.force_regenerate),
        reused_director_artifacts: canReuseDirectorArtifacts,
        reused_answers: !isManualInit && Object.keys(nextAnswers || {}).length > 0,
        current_scenario_input_signature: directorInputSignature,
        hasNarrative: Boolean(String(body?.story_text || body?.narrative_note || "").trim()),
        hasAudio: Boolean(body?.metadata?.audio?.url || body?.source?.source_mode === "audio"),
        hasVideo: Boolean(body?.source?.source_mode === "video_file" || body?.source?.source_mode === "video_link"),
        refsByRole: Object.keys(body?.refs_by_role || {}),
        existingDirectorPackage: Boolean(body?.director_package && Object.keys(body.director_package).length),
        full_node_payload_sanitized: isManualInit,
      });
      if (!isManualInit) {
        console.debug("[AI DIRECTOR V2 ANSWERS]", {
          normalized_questions: normalizedQuestions,
          submitted_answers: submittedAnswers,
          answer_keys: Object.keys(submittedAnswers),
          unanswered_required_questions: unansweredRequiredQuestions,
        });
      }
      const json = await fetchJson("/api/director/chat", {
        method: "POST",
        body,
      });
      const nextNormalizedAnswers = json?.answers && typeof json.answers === "object" ? json.answers : nextAnswers;
      const hasQuestions = Array.isArray(json?.questions) && json.questions.length > 0;
      setAnswers(nextNormalizedAnswers);
      setAssistantMessage(String(json?.assistant_message || "").trim());
      setDynamicQuestions(hasQuestions ? json.questions : []);
      setDirectorChatDone(Boolean(json?.done));
      const resolvedDone = Boolean(json?.done);
      const patch = {
        directorAnswers: nextNormalizedAnswers,
        director_summary_preview: String(json?.director_summary || "").trim(),
        director_story_understanding: json?.story_understanding && typeof json.story_understanding === "object" ? json.story_understanding : {},
      };

      if (resolvedDone) {
        const createdForSignature = String(
          json?.current_scenario_input_signature
          || json?.director_created_for_signature
          || directorInputSignature
        ).trim();
        const finalConfig = json?.director_config && typeof json.director_config === "object" ? json.director_config : {};
        const finalContract = json?.director_contract && typeof json.director_contract === "object"
          ? json.director_contract
          : buildDirectorContractFromConfig(finalConfig);
        const finalPackage = json?.director_package && typeof json.director_package === "object" ? json.director_package : {};
        finalContract.created_for_signature = createdForSignature;
        finalPackage.created_for_signature = createdForSignature;
        Object.assign(patch, {
          directorStale: false,
          director_signature_matches_current: createdForSignature === directorInputSignature,
          director_created_for_signature: createdForSignature,
          director_config: finalConfig,
          director_contract: finalContract,
          director_package: finalPackage,
          director_summary: String(json?.director_summary || "").trim(),
          directorSummary: String(json?.director_summary || "").trim(),
          director_config_preview: {},
          director_contract_preview: {},
          director_package_preview: {},
        });
      } else {
        Object.assign(patch, {
          directorStale: true,
          director_signature_matches_current: false,
          director_config_preview: json?.director_config_preview && typeof json.director_config_preview === "object" ? json.director_config_preview : {},
          director_contract_preview: json?.director_contract_preview && typeof json.director_contract_preview === "object" ? json.director_contract_preview : {},
          director_package_preview: json?.director_package_preview && typeof json.director_package_preview === "object" ? json.director_package_preview : {},
        });
      }

      if (isManualInit) {
        patch.diagnostics = {
          ...baseDiagnostics,
          current_scenario_input_signature: directorInputSignature,
          director_signature_matches_current: Boolean(patch?.director_signature_matches_current),
          persisted_director_result_reused: false,
        };
      }
      data?.onFieldChange?.(id, patch);
      setStaleDirectorState(!resolvedDone);
      if (!resolvedDone && !hasQuestions && allowFallback) {
        throw new Error("AI Director вернул пустой список вопросов.");
      }
    } catch (error) {
      const geminiRequestFailed = String(error?.message || "").includes("gemini_request_failed");
      setAiError(geminiRequestFailed ? "Gemini временно не ответил. Ответы не потеряны, можно повторить отправку." : String(error?.message || "AI Director failed"));
      if (allowFallback) {
        if (geminiRequestFailed && phase === "answer") {
          setAssistantMessage("Gemini временно не ответил. Ответы не потеряны, можно повторить отправку.");
        } else {
          setAssistantMessage("AI Director временно недоступен. Можно продолжить вручную.");
          setDynamicQuestions([
            { id: "fallback_director_note", label: "Опишите ключевые режиссёрские решения в свободной форме.", type: "free_text", required: true },
          ]);
        }
      }
    } finally {
      setAiLoading(false);
    }
  };

  const startDirectorChat = async () => {
    if (aiLoading) return;
    setDirectorChatDone(false);
    setAssistantMessage("");
    setAnswers({});
    setDynamicQuestions([]);
    await runDirectorChat({ phase: "init" });
  };

  const resetDirectorChat = ({ clearScenarioText = false } = {}) => {
    setAssistantMessage("");
    setDynamicQuestions([]);
    setDirectorChatDone(false);
    setAnswers({});
    setStaleDirectorState(true);
    data?.onFieldChange?.(id, buildDirectorResetPatch({ clearScenarioText }));
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
                  aiNarrative: e.target.value,
                  directorAnswers: {},
                  director_config: {},
                  director_contract: {},
                  director_package: {},
                  director_created_for_signature: "",
                  directorOutput: null,
                  storyboardOut: null,
                  storyboardPackage: {},
                  stageStatuses: {},
                  director_signature_matches_current: false,
                  directorStale: true,
                })}
                placeholder="Например: добавь экшена, сделай мрачнее, усиль конфликт"
                rows={3}
              />
            </label>
            <button
              type="button"
              className="clipSB_btn clipSB_btnPrimary"
              onClick={startDirectorChat}
              disabled={aiLoading || !String(data?.directorNote || data?.text || "").trim()}
            >
              {aiLoading ? "AI режиссёр думает…" : "🎬 Сформировать через AI"}
            </button>
            {aiError ? <div className="clipSB_narrativeEmptyHint" role="alert">{aiError}</div> : null}
            {(dynamicQuestions.length > 0 || directorChatDone || assistantMessage) ? (
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={() => resetDirectorChat()} disabled={aiLoading}>
                  Сбросить режиссуру
                </button>
                <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={() => resetDirectorChat({ clearScenarioText: true })} disabled={aiLoading}>
                  Очистить текст сценария
                </button>
              </div>
            ) : null}
            <section className="clipSB_narrativeSection ai-director-panel">
              <div className="clipSB_brainLabel">AI режиссёр</div>
              <div className="ai-director-status">
                {aiError
                  ? "Ошибка AI режиссёра"
                  : directorChatDone && directorSignatureMatchesCurrent
                    ? "Режиссура собрана"
                    : normalizedQuestions.length > 0
                      ? "Нужно ответить на вопросы"
                      : (staleDirectorState || !directorSignatureMatchesCurrent)
                        ? "Режиссура устарела"
                        : "Ожидание данных"}
              </div>
              {assistantMessage ? <div className="ai-director-message">{assistantMessage}</div> : null}
              {normalizedQuestions.length > 0 ? (
                <div className="ai-director-questions">
                  {normalizedQuestions.map((question, index) => {
                    const type = String(question?.type || "free_text").trim().toLowerCase();
                    const qid = String(question?.id || "").trim();
                    const qValue = qid ? answers?.[qid] : undefined;
                    const options = Array.isArray(question?.options) ? question.options : [];
                    const questionLabel = String(question?.label || "").trim();
                    if (!questionLabel) console.warn("[AI DIRECTOR] Missing question label/text", question);
                    if (!qid) return null;
                    return (
                      <div key={`${qid}-${index}`} className="ai-director-question-card">
                        <div className="ai-director-question-meta">Вопрос {index + 1} из {normalizedQuestions.length}{question?.required ? " • обязательно" : ""}</div>
                        <div className="ai-director-question-label">{questionLabel || "Вопрос без текста. Проверьте формат question_text/label."}</div>
                        {(type === "single_choice" || type === "multiple_choice" || type === "multi_choice") ? (
                          <div className="ai-director-option-list">
                            {options.map((option, optionIndex) => {
                              const optionValue = String(option?.value ?? "").trim();
                              const optionLabel = String(
                                option?.label || option?.text || option?.title || optionValue || `Вариант ${optionIndex + 1}`
                              ).trim();
                              const isActive = Array.isArray(qValue) ? qValue.includes(optionValue) : String(qValue || "") === optionValue;
                              return (
                                <button
                                  key={`${qid}-${optionValue}-${optionIndex}`}
                                  type="button"
                                  className={`ai-director-option-card ${isActive ? "is-selected" : ""}`.trim()}
                                  onClick={() => setAnswers((prev) => {
                                    const previous = prev && typeof prev === "object" ? prev : {};
                                    if (type === "multiple_choice" || type === "multi_choice") {
                                      const prevArr = Array.isArray(previous[qid]) ? previous[qid] : [];
                                      return { ...previous, [qid]: prevArr.includes(optionValue) ? prevArr.filter((v) => v !== optionValue) : [...prevArr, optionValue] };
                                    }
                                    return { ...previous, [qid]: optionValue };
                                  })}
                                  disabled={aiLoading || directorChatDone}
                                >
                                  {optionLabel}
                                </button>
                              );
                            })}
                          </div>
                        ) : (
                          <>
                            <textarea
                              className="clipSB_textarea ai-director-textarea"
                              value={typeof qValue === "string" ? qValue : ""}
                              onChange={(e) => setAnswers((prev) => ({ ...(prev || {}), [qid]: e.target.value }))}
                              rows={3}
                              disabled={aiLoading || directorChatDone}
                            />
                            {String(question?.expected_answer_type || "").toLowerCase() === "integer" ? (
                              <div className="clipSB_narrativeEmptyHint">
                                Введите число{question?.min_value != null || question?.max_value != null ? ` от ${question?.min_value ?? "−∞"} до ${question?.max_value ?? "+∞"}` : ""}.
                              </div>
                            ) : null}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : null}
              {normalizedQuestions.length > 0 ? (
                <div className="ai-director-submit-row">
                  <button
                    type="button"
                    className="clipSB_btn clipSB_btnPrimary"
                    onClick={() => runDirectorChat({ phase: "answer", answerPatch: answers })}
                    disabled={aiLoading || hasUnansweredRequiredQuestions}
                  >
                    Отправить ответы
                  </button>
                  <div className="clipSB_narrativeEmptyHint">Заполнено {requiredAnsweredCount} из {requiredQuestions.length} обязательных вопросов</div>
                </div>
              ) : null}
              {directorChatDone && directorSignatureMatchesCurrent ? (
                <div className="director-question">
                  <div className="question-title">Режиссура собрана ✅ Можно запускать общий пайплайн.</div>
                  <div className="clipSB_narrativeContextChips">
                    {Object.entries(answers || {}).map(([key, value]) => (
                      <span key={key} className="clipSB_narrativeContextChip isReady">{DIRECTOR_ANSWER_LABELS[key] || key}: {String(value)}</span>
                    ))}
                  </div>
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
