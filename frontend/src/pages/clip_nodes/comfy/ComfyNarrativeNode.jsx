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
const DIRECTOR_ANSWER_LABELS = {
  lip_sync_density: "Плотность пения",
  performance_place: "Место перформанса",
  world_zones: "Мировые зоны",
  intro_plan: "Интро",
  outro_plan: "Аутро",
  camera_style: "Камера",
};
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
      max_questions: 6,
    },
  };
}

function buildDirectorConfigFromAnswers(answers) {
  const safeAnswers = answers && typeof answers === "object" ? answers : {};
  const config = {};

  if (safeAnswers.lip_sync_density === "vocal_light_30") {
    config.ia2v_ratio = 0.3;
    config.i2v_ratio = 0.7;
  } else if (safeAnswers.lip_sync_density === "balanced_50") {
    config.ia2v_ratio = 0.5;
    config.i2v_ratio = 0.5;
  } else if (safeAnswers.lip_sync_density === "vocal_heavy_70") {
    config.ia2v_ratio = 0.7;
    config.i2v_ratio = 0.3;
  } else if (safeAnswers.lip_sync_density === "full_vocal") {
    config.ia2v_ratio = 0.9;
    config.i2v_ratio = 0.1;
  }

  const performancePlace = String(safeAnswers.performance_place || "").trim().toLowerCase();
  if (["one_main_place", "multiple_places", "performance_plus_memories"].includes(performancePlace)) {
    config.performance_place_mode = performancePlace;
  }

  const worldMode = String(safeAnswers.world_zones || "").trim().toLowerCase();
  if (worldMode === "train_only") {
    config.ia2v_locations = ["train"];
    config.i2v_locations = ["train"];
  } else if (worldMode === "train_and_odesa") {
    config.ia2v_locations = ["train"];
    config.i2v_locations = ["odesa_city", "odesa_port", "odesa_streets"];
  } else if (worldMode === "odesa_dominant") {
    config.ia2v_locations = ["train"];
    config.i2v_locations = ["odesa_city", "odesa_port", "odesa_streets", "odesa_courtyard"];
    config.memory_intercut = true;
  } else if (worldMode === "club_dancefloor") {
    config.ia2v_locations = ["club_dancefloor"];
    config.i2v_locations = ["club_dancefloor"];
  } else if (worldMode === "club_full") {
    config.ia2v_locations = ["club_dancefloor", "club_bar", "club_backstage"];
    config.i2v_locations = ["club_dancefloor", "club_bar", "club_backstage", "crowd"];
  } else if (worldMode === "city_mixed") {
    config.ia2v_locations = ["main_location"];
    config.i2v_locations = ["city", "streets", "interiors"];
  } else if (worldMode === "generic_mixed") {
    config.i2v_locations = ["main_location", "secondary_location"];
  }

  const introMode = String(safeAnswers.intro_plan || "").trim().toLowerCase();
  if (introMode === "intro_location_first") {
    config.intro_scenes = ["location_establishing", "character_entry"];
  } else if (introMode === "intro_character_first") {
    config.intro_scenes = ["hero_closeup", "emotional_setup"];
  } else if (introMode === "intro_action_first") {
    config.intro_scenes = ["action_start", "rhythm_start"];
  }

  const outroPlan = String(safeAnswers.outro_plan || "").trim().toLowerCase();
  if (outroPlan === "outro_stay_inside") {
    config.outro_scenes = ["final_inside", "emotional_hold"];
  } else if (outroPlan === "outro_arrival") {
    config.outro_scenes = ["arrival_or_resolution", "final_look"];
  } else if (outroPlan === "outro_exit_to_world") {
    config.outro_scenes = ["exit_to_world", "wide_final"];
  }

  const cameraStyle = String(safeAnswers.camera_style || "").trim().toLowerCase();
  if (cameraStyle === "static_cinematic") {
    config.camera_style = "still_witness";
  } else if (cameraStyle === "smooth_glide") {
    config.camera_style = "cinematic_glide";
  } else if (cameraStyle === "emotional_close") {
    config.camera_style = "emotional_proximity";
  } else if (cameraStyle === "dynamic_music") {
    config.camera_style = "dynamic_controlled";
  }
  return config;
}

function buildDirectorContractFromConfig(directorConfig) {
  const cfg = directorConfig && typeof directorConfig === "object" ? directorConfig : {};
  const ia2vLocations = Array.isArray(cfg.ia2v_locations) && cfg.ia2v_locations.length
    ? cfg.ia2v_locations
    : ["train", "train_carriage", "compartment", "train_corridor"];
  const i2vLocations = Array.isArray(cfg.i2v_locations) && cfg.i2v_locations.length
    ? cfg.i2v_locations
    : ["odesa_city", "odesa_courtyard", "odesa_port", "odesa_streets", "odesa_sea"];
  return {
    source: "ai_director_chat",
    hard_location_binding: true,
    route_location_rules: {
      ia2v: {
        required_world: "train",
        allowed_zones: ia2vLocations,
        performer_visibility: "required",
        singer_visibility: "required",
        lip_sync_framing: "required",
      },
      i2v: {
        required_world: "odesa_memory",
        allowed_zones: i2vLocations,
        performer_visibility: "optional_or_absent",
        singer_visibility: "offscreen_or_non_dominant",
      },
    },
  };
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
  const [directorMessages, setDirectorMessages] = useState([]);
  const [directorInput, setDirectorInput] = useState("");
  const [directorChatDone, setDirectorChatDone] = useState(false);
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
    const hasLocalMessages = Array.isArray(directorMessages) && directorMessages.length > 0;
    const hasPersistedAnswers =
      data?.directorAnswers &&
      typeof data.directorAnswers === "object" &&
      Object.keys(data.directorAnswers).length > 0;
    const hasPersistedConfig =
      data?.director_config &&
      typeof data.director_config === "object" &&
      Object.keys(data.director_config).length > 0;

    if (hasLocalMessages) setDirectorMessages([]);

    if (hasLocalAnswers) {
      setAnswers({});
    }
    setDirectorInput("");
    setDirectorChatDone(false);

    if (hasPersistedAnswers || hasPersistedConfig) {
      data?.onFieldChange?.(id, {
        directorAnswers: {},
        director_config: {},
        director_contract: {},
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
      director_contract: buildDirectorContractFromConfig(nextConfig),
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
      director_contract: response?.director_contract || buildDirectorContractFromConfig(response?.director_config || data?.director_config || {}),
      lip_sync: !!response?.lip_sync,
      structure,
      routes: Array.isArray(response?.routes) ? response.routes : [],
    });
  };

  const runDirectorChat = async ({ nextMessages, nextAnswers, userMessage }) => {
    if (aiLoading) return;
    setAiError("");
    setAiLoading(true);
    try {
      const context = buildDirectorContext(data);
      const json = await fetchJson("/api/director/chat", {
        method: "POST",
        body: {
          context,
          messages: Array.isArray(nextMessages) ? nextMessages : [],
          director_state: {
            answers: nextAnswers && typeof nextAnswers === "object" ? nextAnswers : {},
            director_config: data?.director_config && typeof data.director_config === "object" ? data.director_config : {},
          },
          user_message: String(userMessage || ""),
        },
      });
      const nextNormalizedAnswers = json?.answers && typeof json.answers === "object" ? json.answers : (nextAnswers || {});
      setAnswers(nextNormalizedAnswers);
      setDirectorChatDone(Boolean(json?.done));

      const assistantMessage = String(json?.assistant_message || "").trim();
      if (assistantMessage) {
        setDirectorMessages((prev) => [...(Array.isArray(prev) ? prev : []), { role: "assistant", content: assistantMessage }]);
      }

      if (json?.director_config_preview && typeof json.director_config_preview === "object") {
        data?.onFieldChange?.(id, {
          directorAnswers: nextNormalizedAnswers,
          director_config: json.director_config_preview,
          director_contract: buildDirectorContractFromConfig(json.director_config_preview),
        });
      }
    } catch (error) {
      setAiError(String(error?.message || "AI Director failed"));
    } finally {
      setAiLoading(false);
    }
  };

  const startDirectorChat = async () => {
    if (aiLoading) return;
    setDirectorMessages([]);
    setDirectorInput("");
    setDirectorChatDone(false);
    await runDirectorChat({ nextMessages: [], nextAnswers: answers, userMessage: "" });
  };

  const sendDirectorMessage = async () => {
    const text = String(directorInput || "").trim();
    if (!text || aiLoading || directorChatDone) return;
    const userEntry = { role: "user", content: text };
    const nextMessages = [...(Array.isArray(directorMessages) ? directorMessages : []), userEntry];
    setDirectorMessages(nextMessages);
    setDirectorInput("");
    await runDirectorChat({
      nextMessages,
      nextAnswers: answers,
      userMessage: text,
    });
  };

  const resetDirectorChat = () => {
    setDirectorMessages([]);
    setDirectorInput("");
    setDirectorChatDone(false);
    setAnswers({});
    data?.onFieldChange?.(id, {
      directorAnswers: {},
      director_config: {},
      director_contract: {},
    });
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
                  director_contract: {},
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
              {directorMessages.length === 0 ? (
                <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={startDirectorChat} disabled={aiLoading}>
                  🎬 Начать с AI режиссёром
                </button>
              ) : (
                <div className="director-question">
                  <div className="clipSB_narrativeContextChips" style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "stretch" }}>
                    {directorMessages.map((message, index) => {
                      const isAssistant = String(message?.role || "") === "assistant";
                      return (
                        <div
                          key={`director-msg-${index}`}
                          style={{
                            alignSelf: isAssistant ? "flex-start" : "flex-end",
                            maxWidth: "92%",
                            background: isAssistant ? "rgba(255,255,255,0.06)" : "rgba(113,87,255,0.2)",
                            border: "1px solid rgba(255,255,255,0.12)",
                            borderRadius: 8,
                            padding: "8px 10px",
                            whiteSpace: "pre-wrap",
                          }}
                        >
                          {String(message?.content || "")}
                        </div>
                      );
                    })}
                  </div>
                  <textarea
                    className="clipSB_textarea clipSB_narrativeTextarea clipSB_narrativeTextarea--compact"
                    value={directorInput}
                    onChange={(e) => setDirectorInput(e.target.value)}
                    placeholder="Ответьте своими словами..."
                    rows={2}
                    disabled={aiLoading || directorChatDone}
                  />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button type="button" className="clipSB_btn clipSB_btnPrimary" onClick={sendDirectorMessage} disabled={aiLoading || directorChatDone || !String(directorInput || "").trim()}>
                      Отправить
                    </button>
                    <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={resetDirectorChat} disabled={aiLoading}>
                      Сбросить режиссуру
                    </button>
                  </div>
                </div>
              )}
              {directorChatDone ? (
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
