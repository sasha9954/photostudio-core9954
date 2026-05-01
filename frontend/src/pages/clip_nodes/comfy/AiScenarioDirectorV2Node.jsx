import React, { useEffect, useMemo, useRef, useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { NodeShell, handleStyle } from "./comfyNodeShared";
import { resolveDirectorV2ContentType } from "./comfyNarrativeDomain";

const INPUTS = [
  { id: "audio_in", label: "Аудио", tone: "audio", placeholder: "Аудио не подключено" },
  { id: "ref_character_1", label: "Персонаж 1", tone: "character", placeholder: "Референс главного персонажа" },
  { id: "ref_character_2", label: "Персонаж 2", tone: "character", placeholder: "Референс второго персонажа" },
  { id: "ref_character_3", label: "Персонаж 3", tone: "character", placeholder: "Референс третьего персонажа" },
  { id: "ref_animal", label: "Животное", tone: "animal", placeholder: "Животное / существо / питомец" },
  { id: "ref_group", label: "Группа", tone: "group", placeholder: "Группа / толпа / команда / массовка" },
  { id: "ref_location", label: "Локация", tone: "location", placeholder: "Референс локации" },
  { id: "ref_style", label: "Стиль", tone: "style", placeholder: "Визуальный стиль / настроение" },
  { id: "video_ref_in", label: "Видео-референс", tone: "video", placeholder: "Видео для ориентира" },
  { id: "ref_props", label: "Предметы", tone: "props", placeholder: "Предметы / реквизит" },
  { id: "text_in", label: "Идея / текст", tone: "text", placeholder: "Идея, текст или краткий сюжет" },
];

const DIRECTOR_STATES = {
  WAIT_INPUTS: "wait_inputs",
  READY_TO_PARSE_AUDIO: "ready_to_parse_audio",
  PARSING_AUDIO: "parsing_audio",
  AUDIO_PARSED: "audio_parsed",
  CHAT_ACTIVE: "chat_active",
  GENERATING_DRAFT: "generating_draft",
  DRAFT_READY: "draft_ready",
  DRAFT_CONFIRMED: "draft_confirmed",
  APPLYING: "applying",
  APPLIED: "applied",
  ERROR: "error",
};

const DIRECTOR_MODE_OPTIONS = [
  { value: "clip", label: "Клип" },
  { value: "story", label: "История" },
  { value: "advertisement", label: "Реклама" },
  { value: "kino", label: "Кино" },
  { value: "test", label: "Тест" },
];
const DIRECTOR_FORMAT_OPTIONS = ["9:16", "16:9", "1:1"];

const toneToColor = { audio: "var(--family-audio)", character: "var(--family-ref-character)", animal: "var(--family-ref-animal)", group: "var(--family-ref-group)", location: "var(--family-ref-location)", style: "var(--family-ref-style)", video: "var(--family-video-ref)", props: "var(--family-ref-items)", text: "var(--family-text)" };
const fmt = (v) => Number(v || 0).toFixed(2);
const isObject = (v) => !!v && typeof v === "object";
const STAGE_ORDER = ["core", "roles", "scenes", "scene_detail", "prompts", "final_video_prompt", "final"];
const STAGE_TO_PACKAGE_KEY = { core: "story_core", roles: "role_plan", scenes: "scene_plan", scene_detail: "scene_detail", prompts: "scene_prompts", final_video_prompt: "final_video_prompt", final: "final_storyboard" };
const STAGE_TO_BACKEND_STAGE_ID = { core: "story_core", roles: "role_plan", scenes: "scene_plan", scene_detail: "scene_detail", prompts: "scene_prompts", final_video_prompt: "final_video_prompt", final: "finalize" };
const STAGE_META = { core: { title: "CORE — смысловой позвоночник", description: "Базовая смысловая структура и опорные идеи ролика." }, roles: { title: "ROLES — роли и присутствие", description: "Распределение ролей, появлений и эмоционального фокуса." }, scenes: { title: "SCENES — план сцен", description: "Покомпонентный план сцен и переходов." }, scene_detail: { title: "SCENE DETAIL — режиссёрская проработка", description: "Детальная постановка сцен без изменения locked scaffold." }, prompts: { title: "PROMPTS — фото/видео промты", description: "Генерация промтов для визуальных и видео-сцен." }, final_video_prompt: { title: "FINAL VIDEO PROMPT — финальные видео-промты", description: "Финализация видео-промтов для рендера." }, final: { title: "FINAL — storyboard manifest", description: "Финальный storyboard package для передачи в Scenario Storyboard." } };
const renderValue = (value) => (value == null || value === "" ? "—" : String(value));
const mapBackendStatusToPipelineStageStatus = (status = "") => {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "done") return "confirmed";
  if (["running", "queued", "in_progress"].includes(normalized)) return "running";
  if (normalized === "error") return "error";
  if (normalized === "stale") return "stale";
  if (["ready", "idle", "locked"].includes(normalized)) return normalized;
  return "idle";
};

const CONTRACT_SECTIONS = [
  ["mode_understanding", "Понимание режима"],
  ["audio_interpretation", "Понимание аудио"],
  ["visual_directing_rules", "Режиссёрская грамматика"],
  ["downstream_brief", "Задание для цепочки"],
  ["story_goal", "Замысел"],
  ["emotional_arc", "Эмоциональная арка"],
  ["visual_world", "Мир / визуальная среда"],
  ["performance_strategy", "Стратегия перформанса"],
  ["route_mix", "Маршруты"],
  ["lip_sync_policy", "Политика lip-sync"],
  ["memory_policy", "Воспоминания / перебивки"],
  ["opening_strategy", "Начало"],
  ["ending_strategy", "Финал"],
  ["reference_usage", "Использование референсов"],
  ["connected_input_questions_resolved", "Уточнение входящих нод"],
  ["must_keep", "Обязательно сохранить"],
  ["must_avoid", "Нельзя делать"],
  ["continuity_rules", "Правила continuity"],
  ["montage_policy", "Монтажная политика"],
];

const renderContractValue = (value) => {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch (_error) {
    return String(value);
  }
};

const renderInlineMarkdown = (text = "") => {
  const source = String(text || "");
  const nodes = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let lastIndex = 0;
  let match = pattern.exec(source);
  while (match) {
    if (match.index > lastIndex) nodes.push(source.slice(lastIndex, match.index));
    const token = match[0];
    if (token.startsWith("`") && token.endsWith("`")) nodes.push(<code key={`code_${match.index}`}>{token.slice(1, -1)}</code>);
    else if (token.startsWith("**") && token.endsWith("**")) nodes.push(<strong key={`strong_${match.index}`}>{token.slice(2, -2)}</strong>);
    else nodes.push(token);
    lastIndex = match.index + token.length;
    match = pattern.exec(source);
  }
  if (lastIndex < source.length) nodes.push(source.slice(lastIndex));
  return nodes.length ? nodes : [source];
};

const renderAssistantMarkdown = (text = "") => {
  const lines = String(text || "").split("\n");
  const blocks = [];
  let paragraph = [];
  let listItems = [];
  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(<p key={`p_${blocks.length}`}>{paragraph.map((line, idx) => <React.Fragment key={`pf_${idx}`}>{idx > 0 ? <br /> : null}{renderInlineMarkdown(line)}</React.Fragment>)}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (!listItems.length) return;
    blocks.push(<ul key={`ul_${blocks.length}`}>{listItems.map((item, idx) => <li key={`li_${idx}`}>{renderInlineMarkdown(item)}</li>)}</ul>);
    listItems = [];
  };
  lines.forEach((rawLine) => {
    const line = rawLine || "";
    const listMatch = line.match(/^\s*[-*]\s+(.+)$/);
    if (!line.trim()) {
      flushParagraph();
      flushList();
      return;
    }
    if (listMatch) {
      flushParagraph();
      listItems.push(listMatch[1]);
      return;
    }
    flushList();
    paragraph.push(line);
  });
  flushParagraph();
  flushList();
  return blocks.length ? blocks : <p>{renderInlineMarkdown(text)}</p>;
};

function normalizeDirectorV2AudioSegments(audioMap = null) {
  const source = isObject(audioMap) ? audioMap : {};
  const raw = Array.isArray(source?.segments) ? source.segments : [];
  return raw.map((segment, index) => {
    const seg = isObject(segment) ? segment : {};
    const start = Number(seg?.start_sec ?? seg?.startSec ?? seg?.t0 ?? 0) || 0;
    const end = Number(seg?.end_sec ?? seg?.endSec ?? seg?.t1 ?? start) || start;
    const duration = Number(seg?.duration_sec ?? seg?.durationSec ?? (end - start)) || 0;
    return {
      id: String(seg?.segment_id || seg?.id || `seg_${String(index + 1).padStart(2, "0")}`),
      startSec: start,
      endSec: end,
      durationSec: duration,
      transcript: String(seg?.transcript_slice || seg?.transcriptSlice || seg?.text || "").trim(),
      isLipSyncCandidate: Boolean(seg?.is_lip_sync_candidate ?? seg?.isLipSyncCandidate),
      intensity: Number(seg?.intensity ?? seg?.energy ?? 0) || 0,
      rhythmicAnchor: String(seg?.rhythmic_anchor || "").trim(),
    };
  });
}

export default function AiScenarioDirectorV2Node({ id, data }) {
  const abortControllerRef = useRef(null);
  const activeRunIdRef = useRef("");
  const [chatInput, setChatInput] = useState("");
  const [copyFeedback, setCopyFeedback] = useState({});
  const [actionFeedback, setActionFeedback] = useState({});
  const [finalJsonOpen, setFinalJsonOpen] = useState(false);
  const chatMessagesRef = useRef(null);
  const feedbackTimersRef = useRef({});
  const isApplied = data?.directorState === DIRECTOR_STATES.APPLIED;
  const connections = data?.connections || {};
  const connectedInputs = isObject(data?.connectedInputs) ? data.connectedInputs : {};
  const hasAudio = Boolean(connectedInputs?.audio_in || connections.audio_in);
  const directorState = data?.directorState || (hasAudio ? DIRECTOR_STATES.READY_TO_PARSE_AUDIO : DIRECTOR_STATES.WAIT_INPUTS);
  const audioMap = data?.audioMap || null;
  const chatMessages = Array.isArray(data?.chatMessages) ? data.chatMessages : [];
  const draftContract = data?.draftContract || null;
  const draftPlan = Array.isArray(data?.draftPlan) ? data.draftPlan : [];
  const currentMode = data?.directorMode || data?.mode || "clip";
  const currentFormat = data?.directorFormat || data?.format || "9:16";
  const contentType = resolveDirectorV2ContentType(currentMode);
  const error = data?.directorError || "";
  const info = data?.directorInfo || "";
  const draftRegenerating = Boolean(data?.draftRegenerating);
  const currentAudioSourceNodeId = connectedInputs?.audio_in?.sourceNodeId || "";
  const currentAudioUrl = connectedInputs?.audio_in?.value || connectedInputs?.audio_in?.url || "";
  const hasDraft = Boolean(draftContract || draftPlan.length);
  const isAudioChangedAfterParse = Boolean(audioMap) && (
    (data?.parsedAudioSourceNodeId && currentAudioSourceNodeId && data.parsedAudioSourceNodeId !== currentAudioSourceNodeId)
    || (data?.parsedAudioUrl && currentAudioUrl && data.parsedAudioUrl !== currentAudioUrl)
  );
  const isParseLocked = isApplied
    || directorState === DIRECTOR_STATES.PARSING_AUDIO
    || directorState === DIRECTOR_STATES.GENERATING_DRAFT
    || directorState === DIRECTOR_STATES.DRAFT_READY
    || directorState === DIRECTOR_STATES.DRAFT_CONFIRMED
    || directorState === DIRECTOR_STATES.APPLYING
    || hasDraft;

  const segments = useMemo(() => normalizeDirectorV2AudioSegments(audioMap), [audioMap]);

  useEffect(() => {
    const el = chatMessagesRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [chatMessages.length, data?.directorChatPending]);

  const patchData = (patch) => data?.onPatchNodeData?.(id, patch);
  const directorViewMode = data?.directorViewMode || "chat";
  const selectedPipelineStage = data?.selectedPipelineStage || data?.activePipelineStage || "core";
  const activePipelineStage = selectedPipelineStage;
  const pipelineStages = isObject(data?.pipelineStages) ? data.pipelineStages : {};
  const ensureStage = (stageKey) => ({ status: "locked", confirmed: false, stale: false, output: null, editedOutput: null, error: "", ...(pipelineStages?.[stageKey] || {}) });
  const buildInitialPipelineStages = () => ({ core: { status: "idle", confirmed: false, stale: false, output: null, editedOutput: null, error: "" }, roles: { status: "locked", confirmed: false, stale: false, output: null, editedOutput: null, error: "" }, scenes: { status: "locked", confirmed: false, stale: false, output: null, editedOutput: null, error: "" }, scene_detail: { status: "locked", confirmed: false, stale: false, output: null, editedOutput: null, error: "" }, prompts: { status: "locked", confirmed: false, stale: false, output: null, editedOutput: null, error: "" }, final_video_prompt: { status: "locked", confirmed: false, stale: false, output: null, editedOutput: null, error: "" }, final: { status: "locked", confirmed: false, stale: false, output: null, editedOutput: null, error: "" } });
  const isChatLocked = isApplied
    || isAudioChangedAfterParse
    || !(directorState === DIRECTOR_STATES.AUDIO_PARSED || directorState === DIRECTOR_STATES.CHAT_ACTIVE || directorState === DIRECTOR_STATES.GENERATING_DRAFT || directorState === DIRECTOR_STATES.DRAFT_READY || directorState === DIRECTOR_STATES.DRAFT_CONFIRMED || directorState === DIRECTOR_STATES.APPLYING || directorState === DIRECTOR_STATES.APPLIED);

  const parseAudio = async () => {
    if (!data?.onParseAudioStage) return;
    patchData({ directorState: DIRECTOR_STATES.PARSING_AUDIO, directorError: "", directorInfo: "" });
    const result = await data.onParseAudioStage(id);
    if (!result?.ok) return patchData({ directorState: DIRECTOR_STATES.ERROR, directorError: String(result?.error || "Ошибка разбора аудио") });
    const nextAudioMap = result.audioMap || {};
    const nextSegments = Array.isArray(nextAudioMap?.segments) ? nextAudioMap.segments.length : 0;
    const duration = Number(nextAudioMap?.duration_sec || nextAudioMap?.audio_duration_sec || 0) || 0;
    const lip = Array.isArray(nextAudioMap?.segments) ? nextAudioMap.segments.filter((s) => s?.is_lip_sync_candidate).length : 0;
    patchData({
      directorState: DIRECTOR_STATES.AUDIO_PARSED,
      audioMap: nextAudioMap,
      chatMessages: [{ role: "assistant", text: `Аудио разобрано. Я вижу ${nextSegments} сегментов, длительность ${duration.toFixed(2)} сек, lip-sync кандидатов: ${lip}. Теперь можно обсудить структуру клипа.` }],
      directorError: "",
      directorInfo: "",
      parsedAudioSourceNodeId: currentAudioSourceNodeId || "",
      parsedAudioUrl: currentAudioUrl || "",
    });
  };

  const onSend = async () => {
    if (!chatInput.trim()) return;
    if (!data?.onDirectorV2Chat) return;
    const userMessage = chatInput.trim();
    patchData({ directorState: DIRECTOR_STATES.CHAT_ACTIVE, directorChatPending: true, chatMessages: [...chatMessages, { role: "user", text: userMessage }] });
    setChatInput("");
    const result = await data.onDirectorV2Chat(id, userMessage);
    if (!result?.ok) {
      patchData({
        directorChatPending: false,
        directorError: String(result?.error || "Gemini Director V2 не ответил"),
        chatMessages: [...chatMessages, { role: "user", text: userMessage }, { role: "assistant", text: `Ошибка: ${String(result?.error || "Gemini Director V2 не ответил")}` }],
      });
      return;
    }
    patchData({
      directorChatPending: false,
      directorMemory: result?.directorMemory || {},
      directorKnowledgeVersion: result?.knowledgeVersion || data?.directorKnowledgeVersion || "",
      directorError: "",
      chatMessages: [...chatMessages, { role: "user", text: userMessage }, { role: "assistant", text: String(result?.assistantReply || "") }],
    });
  };

  const onGenerateDraft = async () => {
    if (!data?.onGenerateDirectorDraft) return;
    const hadExistingDraft = Boolean(draftContract || draftPlan.length);
    const draftPatch = {
      directorState: DIRECTOR_STATES.GENERATING_DRAFT,
      directorError: "",
      draftRegenerating: hadExistingDraft,
      directorInfo: hadExistingDraft ? "Перегенерирую черновик..." : "",
    };
    if (!hadExistingDraft) {
      draftPatch.draftContract = null;
      draftPatch.draftPlan = [];
      draftPatch.questionsResolved = [];
      draftPatch.remainingRisks = [];
    }
    patchData(draftPatch);
    const result = await data.onGenerateDirectorDraft(id);
    if (!result?.ok) {
      if (hadExistingDraft) {
        return patchData({
          draftRegenerating: false,
          directorState: DIRECTOR_STATES.DRAFT_READY,
          directorError: `Не удалось перегенерировать черновик: ${String(result?.error || "Ошибка генерации черновика")}`,
          directorInfo: "Старый черновик сохранён. Можно повторить перегенерацию.",
        });
      }
      return patchData({
        draftRegenerating: false,
        directorState: DIRECTOR_STATES.ERROR,
        directorError: String(result?.error || "Gemini Director V2 не смог собрать черновик"),
        draftContract: null,
        draftPlan: [],
        questionsResolved: [],
        remainingRisks: [],
      });
    }
    patchData({
      draftRegenerating: false,
      directorState: DIRECTOR_STATES.DRAFT_READY,
      draftContract: result.draftContract || {},
      draftPlan: result.draftPlan || [],
      draftIsDemo: Boolean(result?.isDemo),
      questionsResolved: result.questionsResolved || [],
      remainingRisks: result.remainingRisks || [],
      directorKnowledgeVersion: result?.knowledgeVersion || data?.directorKnowledgeVersion || "",
      directorError: "",
      directorInfo: hadExistingDraft ? "Черновик обновлён." : "",
    });
  };

  const onApply = () => {
    const directorV2Package = {
      director_contract: draftContract || {},
      draft_plan: draftPlan || [],
      audio_map: audioMap || {},
      chat_history: chatMessages || [],
      connected_inputs: connectedInputs || {},
      mode: currentMode,
      format: currentFormat,
      content_type: contentType,
      knowledge_version: data?.directorKnowledgeVersion || "",
      directorKnowledgeVersion: data?.directorKnowledgeVersion || "",
    };
    console.log("[AI SCENARIO DIRECTOR V2] apply", directorV2Package);
    patchData({
      directorState: DIRECTOR_STATES.APPLIED,
      directorViewMode: "pipeline",
      activePipelineStage: "core",
      pipelineStages: buildInitialPipelineStages(),
      confirmed: true,
      applied: true,
      directorV2Package,
      directorError: "",
      directorInfo: "Режиссёрский пакет подготовлен. Подключение к CORE будет следующим шагом.",
    });
  };
  const onReset = () => patchData({
    directorState: hasAudio ? DIRECTOR_STATES.READY_TO_PARSE_AUDIO : DIRECTOR_STATES.WAIT_INPUTS,
    audioMap: null, chatMessages: [], draftContract: null, draftPlan: [], confirmed: false, applied: false,
    directorV2Package: null, directorViewMode: "chat", activePipelineStage: "core", pipelineStages: {}, directorError: "", directorInfo: "", draftIsDemo: false, storyboardPackage: null, stageStatuses: {}, coreOutput: null, roleOutput: null, sceneOutput: null, promptOutput: null, finalVideoPromptOutput: null, finalOutput: null,
    parsedAudioSourceNodeId: "", parsedAudioUrl: "",
    questionsResolved: [], remainingRisks: [], directorMemory: {}, currentDecisions: {}, directorChatPending: false,
    draftRegenerating: false,
  });
  const resetDirectorCycleForSettingsChange = (nextPatch = {}) => patchData({
    ...nextPatch,
    directorState: hasAudio ? DIRECTOR_STATES.READY_TO_PARSE_AUDIO : DIRECTOR_STATES.WAIT_INPUTS,
    audioMap: null, chatMessages: [], draftContract: null, draftPlan: [], confirmed: false, applied: false,
    directorV2Package: null, directorViewMode: "chat", activePipelineStage: "core", pipelineStages: {}, directorError: "", directorInfo: "", draftIsDemo: false, storyboardPackage: null, stageStatuses: {}, coreOutput: null, roleOutput: null, sceneOutput: null, promptOutput: null, finalVideoPromptOutput: null, finalOutput: null,
    parsedAudioSourceNodeId: "", parsedAudioUrl: "",
    questionsResolved: [], remainingRisks: [], directorMemory: {}, currentDecisions: {}, directorChatPending: false,
    draftRegenerating: false,
  });
  const hasDirectorCycleData = Boolean(audioMap) || Boolean(draftContract) || draftPlan.length > 0 || Boolean(data?.directorV2Package);
  const onModeChange = (value) => {
    const nextPatch = { directorMode: value, mode: value, contentType: resolveDirectorV2ContentType(value), content_type: resolveDirectorV2ContentType(value) };
    if (hasDirectorCycleData) return resetDirectorCycleForSettingsChange(nextPatch);
    patchData(nextPatch);
  };
  const onFormatChange = (value) => {
    const nextPatch = { directorFormat: value, format: value, contentType, content_type: contentType };
    if (hasDirectorCycleData) return resetDirectorCycleForSettingsChange(nextPatch);
    patchData(nextPatch);
  };
  const chipSource = Object.keys(connectedInputs).length ? connectedInputs : connections;

  const setTimedFeedback = (setter, key, label, timeoutMs) => {
    setter((prev) => ({ ...prev, [key]: label }));
    if (feedbackTimersRef.current[key]) window.clearTimeout(feedbackTimersRef.current[key]);
    feedbackTimersRef.current[key] = window.setTimeout(() => {
      setter((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      delete feedbackTimersRef.current[key];
    }, timeoutMs);
  };

  const copyText = async (text, key, successLabel = "Скопировано ✓") => {
    try {
      if (!navigator?.clipboard?.writeText) throw new Error("Clipboard API недоступен");
      await navigator.clipboard.writeText(String(text || ""));
      setTimedFeedback(setCopyFeedback, key, successLabel, 1800);
    } catch (copyError) {
      console.warn(`[AI Director V2] Не удалось скопировать ${key}`, copyError);
      setTimedFeedback(setCopyFeedback, key, "Не удалось скопировать", 2200);
    }
  };


  const markDownstreamStale = (stageKey, sourceStages = pipelineStages) => {
    const nextStages = { ...sourceStages };
    const idx = STAGE_ORDER.indexOf(stageKey);
    STAGE_ORDER.slice(idx + 1).forEach((key) => {
      const prev = nextStages[key] || ensureStage(key);
      nextStages[key] = { ...prev, confirmed: false, stale: true, status: prev.output || prev.editedOutput ? "stale" : "locked" };
    });
    return nextStages;
  };


  const clearRunningStages = (sourceStages = pipelineStages) => {
    const next = { ...sourceStages };
    Object.entries(next).forEach(([key, stRaw]) => {
      const st = stRaw || {};
      if (st.status === "running") {
        next[key] = {
          ...st,
          status: st.output || st.editedOutput ? "stale" : "idle",
          stale: Boolean(st.output || st.editedOutput),
          error: "",
        };
      }
    });
    return next;
  };

  const onRunPipelineStage = async (stageKey) => {
    if (!data?.onRunDirectorV2PipelineStage) return;
    const runId = `${String(stageKey || "")}:${Date.now()}:${Math.random()}`;
    const controller = new AbortController();
    abortControllerRef.current = controller;
    activeRunIdRef.current = runId;
    console.info("[DIRECTOR V2 RUN] start", { stageId: stageKey, runId });
    const current = ensureStage(stageKey);
    const next = { ...pipelineStages, [stageKey]: { ...current, status: "running", error: "" } };
    patchData({ pipelineStages: next, runningStage: stageKey, selectedPipelineStage: stageKey, activePipelineStage: stageKey });
    try {
      console.info("[DIRECTOR V2 RUN] request sent", { stageId: stageKey, runId });
      const result = await data.onRunDirectorV2PipelineStage(id, stageKey, { signal: controller.signal, runId });
      if (activeRunIdRef.current !== runId) return;
      if (!result?.ok) {
        console.info("[DIRECTOR V2 RUN] error", { stageId: stageKey, runId, error: String(result?.error || "Ошибка этапа") });
        patchData({ pipelineStages: { ...next, [stageKey]: { ...current, status: "error", error: String(result?.error || "Ошибка этапа") } } });
        return;
      }
      console.info("[DIRECTOR V2 RUN] success", { stageId: stageKey, runId });
      const updated = ensureStage(stageKey);
      const storyboardPackage = result.storyboardPackage || null;
      const sceneDetailPayload = storyboardPackage?.scene_detail;
      patchData({
        storyboardPackage,
        stageStatuses: result.stageStatuses || {},
        ...(sceneDetailPayload ? {
          scene_detail: sceneDetailPayload,
          directorV2Package: {
            ...(data?.directorV2Package || {}),
            scene_detail: sceneDetailPayload,
          },
          stageOutputs: {
            ...(data?.stageOutputs || {}),
            scene_detail: sceneDetailPayload,
          },
        } : {}),
        pipelineStages: { ...next, [stageKey]: { ...updated, status: "ready", output: result.output || null, error: "", stale: false, confirmed: false } },
      });
    } catch (error) {
      if (String(error?.name || "") === "AbortError") {
        console.info("[DIRECTOR V2 RUN] abort", { stageId: stageKey, runId });
        patchData({ pipelineStages: { ...next, [stageKey]: { ...current, status: current?.output || current?.editedOutput ? "stale" : "idle", stale: true, error: "" } } });
      } else {
        console.info("[DIRECTOR V2 RUN] error", { stageId: stageKey, runId, error: String(error?.message || error || "stage_failed") });
        patchData({ pipelineStages: { ...next, [stageKey]: { ...current, status: "error", error: String(error?.message || error || "Ошибка этапа") } } });
      }
    } finally {
      if (activeRunIdRef.current === runId) activeRunIdRef.current = "";
      if (abortControllerRef.current === controller) abortControllerRef.current = null;
      patchData({ runningStage: null });
    }
  };

  const onStopOrUnlock = () => {
    const currentStage = String(data?.runningStage || activePipelineStage || "");
    if (abortControllerRef.current && currentStage) {
      abortControllerRef.current.abort();
      const current = ensureStage(currentStage);
      patchData({ pipelineStages: { ...pipelineStages, [currentStage]: { ...current, status: current?.output || current?.editedOutput ? "stale" : "idle", stale: true } }, runningStage: null, directorInfo: "Выполнение остановлено." });
      return;
    }
    patchData({
      pipelineStages: clearRunningStages(pipelineStages),
      runningStage: null,
      directorInfo: "Зависшее выполнение сброшено",
    });
  };

  const lastAssistantMessage = [...chatMessages].reverse().find((m) => m?.role === "assistant")?.text || "";
  const sourceState = data && typeof data === "object" ? data : {};
  const packageData = sourceState?.storyboardPackage && typeof sourceState.storyboardPackage === "object" ? sourceState.storyboardPackage : {};
  const lastBackendPackage = sourceState?.lastBackendPackage && typeof sourceState.lastBackendPackage === "object" ? sourceState.lastBackendPackage : {};
  const sceneDetail =
    sourceState?.scene_detail
    || sourceState?.directorV2Package?.scene_detail
    || sourceState?.stageOutputs?.scene_detail
    || packageData?.scene_detail
    || null;
  const sceneDetailScenes = Array.isArray(sceneDetail?.scenes) ? sceneDetail.scenes : [];
  const sceneDetailDebug = {
    hasRootSceneDetail: Boolean(sourceState?.scene_detail),
    hasDirectorV2PackageSceneDetail: Boolean(sourceState?.directorV2Package?.scene_detail),
    hasStageOutputsSceneDetail: Boolean(sourceState?.stageOutputs?.scene_detail),
    lastBackendPackageHasSceneDetail: Boolean(lastBackendPackage?.scene_detail),
  };

  useEffect(() => {
    const stageStatuses = packageData?.stage_statuses && typeof packageData.stage_statuses === "object" ? packageData.stage_statuses : {};
    const finalVideoPrompt = packageData?.final_video_prompt && typeof packageData.final_video_prompt === "object" ? packageData.final_video_prompt : {};
    const finalVideoPromptSegments = Array.isArray(finalVideoPrompt?.segments)
      ? finalVideoPrompt.segments
      : (Array.isArray(finalVideoPrompt?.scenes) ? finalVideoPrompt.scenes : []);
    const finalVideoPromptDone = String(stageStatuses?.final_video_prompt?.status || "").trim().toLowerCase() === "done";
    const finalStoryboard = packageData?.final_storyboard && typeof packageData.final_storyboard === "object" ? packageData.final_storyboard : {};
    const finalManifestRows = Array.isArray(finalStoryboard?.render_manifest) ? finalStoryboard.render_manifest : [];
    const finalSceneRows = Array.isArray(finalStoryboard?.scenes) ? finalStoryboard.scenes : [];
    const finalizeDone = String(stageStatuses?.finalize?.status || "").trim().toLowerCase() === "done";
    const finalReady = finalManifestRows.length > 0 || finalSceneRows.length > 0;
    const hasStageStatuses = Object.keys(stageStatuses).length > 0;
    if (!hasStageStatuses && !finalReady) return;

    const nextStages = buildInitialPipelineStages();
    STAGE_ORDER.forEach((stageKey) => {
      const backendStageId = STAGE_TO_BACKEND_STAGE_ID[stageKey];
      const row = backendStageId ? (stageStatuses?.[backendStageId] || {}) : {};
      const mappedStatus = mapBackendStatusToPipelineStageStatus(row?.status);
      nextStages[stageKey] = {
        ...nextStages[stageKey],
        status: mappedStatus,
        confirmed: mappedStatus === "confirmed",
        stale: mappedStatus === "stale",
        error: String(row?.error || "").trim(),
        output: packageData?.[STAGE_TO_PACKAGE_KEY[stageKey]] || null,
      };
    });
    if (finalVideoPromptDone) {
      nextStages.final_video_prompt = {
        ...nextStages.final_video_prompt,
        status: "confirmed",
        confirmed: true,
        stale: false,
        error: "",
        output: finalVideoPrompt,
      };
    }
    if (finalReady || finalizeDone) {
      nextStages.final = {
        ...nextStages.final,
        status: "ready",
        confirmed: false,
        stale: false,
        error: "",
        output: finalStoryboard,
      };
    }
    patchData({ stageStatuses, pipelineStages: nextStages });
  }, [packageData?.stage_statuses, packageData?.final_video_prompt, packageData?.updated_at]);

  const renderSceneDetailPanel = () => {
    if (!sceneDetail) {
      return (
        <div className="asdv2_emptyState">
          SCENE DETAIL отсутствует в UI state
          <div>hasRootSceneDetail: {String(sceneDetailDebug.hasRootSceneDetail)}</div>
          <div>hasDirectorV2PackageSceneDetail: {String(sceneDetailDebug.hasDirectorV2PackageSceneDetail)}</div>
          <div>hasStageOutputsSceneDetail: {String(sceneDetailDebug.hasStageOutputsSceneDetail)}</div>
          <div>lastBackendPackageHasSceneDetail: {String(sceneDetailDebug.lastBackendPackageHasSceneDetail)}</div>
        </div>
      );
    }

    return (
      <>
        {sceneDetailScenes.map((scene, index) => (
          <div className="asdv2_scene" key={scene?.segment_id || scene?.scene_id || index}>
            <b>Scene {index + 1}</b>
            <small>{renderValue(scene?.segment_id)} · {renderValue(scene?.route)} · {renderValue(scene?.primary_role)}</small>

            <div><b>t0/t1:</b> {renderValue(scene?.t0)} / {renderValue(scene?.t1)}</div>
            <div><b>scene_goal:</b> {renderValue(scene?.scene_goal)}</div>
            <div><b>visual_payoff:</b> {renderValue(scene?.visual_payoff)}</div>
            <div><b>action_detail:</b> {renderValue(scene?.action_detail)}</div>
            <div><b>blocking:</b> {renderValue(scene?.blocking)}</div>

            <div><b>camera:</b> framing={renderValue(scene?.camera?.framing)}, angle={renderValue(scene?.camera?.angle)}, movement={renderValue(scene?.camera?.movement)}</div>
            <div><b>performance:</b> facial_expression={renderValue(scene?.performance?.facial_expression)}, body_language={renderValue(scene?.performance?.body_language)}, energy={renderValue(scene?.performance?.energy)}, lip_sync_readability={renderValue(scene?.performance?.lip_sync_readability)}</div>
            <div><b>environment:</b> setting_detail={renderValue(scene?.environment?.setting_detail)}, foreground={renderValue(scene?.environment?.foreground)}, background={renderValue(scene?.environment?.background)}, atmosphere={renderValue(scene?.environment?.atmosphere)}, lighting={renderValue(scene?.environment?.lighting)}</div>

            <div><b>motion_constraints.safe_motion:</b> {renderValue(scene?.motion_constraints?.safe_motion)}</div>
            <div><b>must_show:</b> {renderValue(Array.isArray(scene?.must_show) ? scene.must_show.join(", ") : scene?.must_show)}</div>
            <div><b>must_avoid:</b> {renderValue(Array.isArray(scene?.must_avoid) ? scene.must_avoid.join(", ") : scene?.must_avoid)}</div>
            <div><b>prompt_bridge_notes:</b> {renderValue(scene?.prompt_bridge_notes)}</div>
          </div>
        ))}

        <details>
          <summary>Raw scene_detail JSON</summary>
          <pre>{JSON.stringify(sceneDetail, null, 2)}</pre>
        </details>
      </>
    );
  };
  const segmentLines = segments.map((seg) => `${seg.id} | ${fmt(seg.startSec)}-${fmt(seg.endSec)} | lip-sync: ${seg.isLipSyncCandidate ? "да" : "нет"} | intensity: ${seg.intensity.toFixed(2)} | ${seg.transcript || "—"}`);
  const transcriptLines = segments.map((seg) => seg.transcript).filter(Boolean);
  const sceneCandidateCount = Array.isArray(audioMap?.scene_candidate_windows)
    ? audioMap.scene_candidate_windows.length
    : (Array.isArray(audioMap?.scene_slots) ? audioMap.scene_slots.length : 0);

  return (<><Handle type="source" position={Position.Right} id="scenario_out_v2" className="clipSB_handle" style={handleStyle("scenario_out")} />
    {INPUTS.map((item, index) => <Handle key={item.id} type="target" position={Position.Left} id={item.id} className="clipSB_handle" style={{ ...handleStyle(item.id), top: 48 + index * 24 }} />)}
    <NodeShell title="AI РЕЖИССЁР V2" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎬</span>} className="clipSB_nodeStoryboard asdv2_shell" style={{ minWidth: 1120 }}>
      <div className="asdv2_body">
        <div className="asdv2_toolbar"><div className="asdv2_sub">Пошаговый режиссёрский flow</div><span className="asdv2_stepBadge">Состояние: {directorState}</span>
          <div className="asdv2_settingsRow">
            <label className="asdv2_setting">Режим:
              <select className="asdv2_select" value={currentMode} disabled={isApplied} onChange={(e) => onModeChange(e.target.value)}>
                {DIRECTOR_MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label className="asdv2_setting">Формат:
              <select className="asdv2_select" value={currentFormat} disabled={isApplied} onChange={(e) => onFormatChange(e.target.value)}>
                {DIRECTOR_FORMAT_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </label>
          </div>
          {data?.directorKnowledgeVersion ? <div className="asdv2_sub">Knowledge: {data.directorKnowledgeVersion}</div> : null}
          <div className="asdv2_actions">
            <button className="clipSB_btn asdv2_primaryAction" disabled={!hasAudio || isParseLocked} onClick={parseAudio}>{directorState === DIRECTOR_STATES.PARSING_AUDIO ? "Разбираю аудио..." : (audioMap ? "Переразобрать аудио" : "Разобрать аудио")}</button>
            <button className="clipSB_btn" disabled={isApplied || !audioMap || directorState === DIRECTOR_STATES.GENERATING_DRAFT || isAudioChangedAfterParse} onClick={onGenerateDraft}>{directorState === DIRECTOR_STATES.GENERATING_DRAFT ? (draftRegenerating || hasDraft ? "Перегенерирую..." : "Генерирую...") : (hasDraft ? "Перегенерировать черновик" : "Сгенерировать черновик")}</button>
            <button className="clipSB_btn" disabled={isApplied || directorState !== DIRECTOR_STATES.DRAFT_READY} onClick={() => patchData({ directorState: DIRECTOR_STATES.DRAFT_CONFIRMED, confirmed: true })}>Подтвердить</button>
            <button className="clipSB_btn" disabled={isApplied || Boolean(data?.draftIsDemo) || directorState !== DIRECTOR_STATES.DRAFT_CONFIRMED} onClick={onApply}>Применить к CORE</button>
            <button className="clipSB_btn" onClick={onReset}>Сбросить</button>
          </div></div>
        <div className="asdv2_inputsBar">{INPUTS.map((input) => <div key={input.id} className={`asdv2_inputChip ${chipSource?.[input.id] ? "isConnected" : "isEmpty"}`} style={{ borderColor: toneToColor[input.tone] || "rgba(255,255,255,0.2)" }}>{input.label}: {chipSource?.[input.id] ? "✓" : "пусто"}</div>)}</div>
        {isAudioChangedAfterParse ? <div className="asdv2_emptyState">Подключённое аудио изменилось. Нажми «Сбросить» и разбери новое аудио.</div> : null}
        {directorViewMode === "pipeline" ? <div className="asdv2_pipelineWindow"><div className="asdv2_pipelineTop"><strong>Pipeline Review</strong><div className="asdv2_actions"><button className="clipSB_btn" onClick={() => patchData({ directorViewMode: "chat" })}>← Вернуться к чату</button></div></div><div className="asdv2_pipelineTabs">{STAGE_ORDER.map((key) => { const st = ensureStage(key); const title = key.toUpperCase().replaceAll("_", " "); return <button key={key} className={`asdv2_pipelineTab ${activePipelineStage === key ? "isActive" : ""} ${st.status === "locked" ? "isLocked" : ""} ${st.status === "confirmed" ? "isConfirmed" : ""} ${st.status === "stale" ? "isStale" : ""} ${st.status === "error" ? "isError" : ""}`} disabled={st.status === "locked"} onClick={() => patchData({ selectedPipelineStage: key, activePipelineStage: key })}>{title}<span className="asdv2_stageStatus">{st.status}</span></button>; })}</div><div className="asdv2_stageReview">{(() => { const st = ensureStage(activePipelineStage); const meta = STAGE_META[activePipelineStage] || { title: activePipelineStage, description: "" }; const editorValue = typeof st.reviewDraft === "string" ? st.reviewDraft : JSON.stringify(st.editedOutput || st.output || {}, null, 2); const stageBusyOrLocked = st.status === "running" || st.status === "locked"; const isFinalStage = activePipelineStage === "final"; const finalManifestCount = isFinalStage && Array.isArray(st?.output?.render_manifest) ? st.output.render_manifest.length : 0; const finalSceneCount = isFinalStage && Array.isArray(st?.output?.scenes) ? st.output.scenes.length : 0; const hasFinalContent = isFinalStage && (finalManifestCount > 0 || finalSceneCount > 0); const finalRoutes = isFinalStage && Array.isArray(st?.output?.scenes) ? st.output.scenes.reduce((acc, scene) => { const key = String(scene?.route || "unknown").trim() || "unknown"; acc[key] = (acc[key] || 0) + 1; return acc; }, {}) : {}; const finalPayloadSizeEstimate = isFinalStage ? JSON.stringify(st?.output || {}).length : 0; return <><h4>{meta.title}</h4><p>{meta.description}</p>{st.status === "running" ? <div className="asdv2_emptyState">Этап выполняется...</div> : null}<div className="asdv2_panel">{activePipelineStage === "scene_detail" ? renderSceneDetailPanel() : (isFinalStage ? <>{st.output ? <div className="asdv2_hint">render_manifest_count: {finalManifestCount} · scene_count: {finalSceneCount} · updated_at: {String(st?.output?.updated_at || packageData?.updated_at || "—")}</div> : null}<div className="asdv2_hint">route_distribution: {Object.entries(finalRoutes).map(([k,v]) => `${k}:${v}`).join(", ") || "—"}</div><div className="asdv2_hint">payload_size_estimate: {finalPayloadSizeEstimate} bytes</div><div className="asdv2_hint">final_ui_compact_mode=true · final_ui_full_json_render_disabled=true · final_manual_confirmation_required=true</div><button className="clipSB_btn" type="button" onClick={() => setFinalJsonOpen((v) => !v)}>{finalJsonOpen ? "Скрыть полный JSON" : "Показать полный JSON"}</button>{finalJsonOpen ? <pre>{JSON.stringify(st.output || {}, null, 2)}</pre> : <div className="asdv2_emptyState">Компактный режим FINAL: полный JSON скрыт.</div>}</> : (st.output ? <pre>{JSON.stringify(st.output, null, 2)}</pre> : <div className="asdv2_emptyState">Сначала сгенерируй этап или сохрани ручные правки.</div>))}</div><textarea className="asdv2_chatInput asdv2_stageEditor" value={isFinalStage && !finalJsonOpen ? JSON.stringify({ compact_mode: true, hint: "Open debug JSON to edit full payload" }) : editorValue} disabled={stageBusyOrLocked || (isFinalStage && !finalJsonOpen)} onChange={(e) => patchData({ pipelineStages: { ...pipelineStages, [activePipelineStage]: { ...st, reviewDraft: e.target.value } } })} /><div className="asdv2_stageActions"><button className="clipSB_btn" disabled={stageBusyOrLocked} onClick={() => onRunPipelineStage(activePipelineStage)}>{st.status === "running" ? "Этап выполняется..." : (st.output || st.editedOutput ? "Перегенерировать этап" : "Сгенерировать этап")}</button><button className="clipSB_btn" onClick={onStopOrUnlock}>Остановить / разблокировать</button><button className="clipSB_btn" disabled={stageBusyOrLocked} onClick={() => { try { const parsed = JSON.parse(String(editorValue || "{}")); const current = ensureStage(activePipelineStage); const nextStages = markDownstreamStale(activePipelineStage, { ...pipelineStages, [activePipelineStage]: { ...current, editedOutput: parsed, confirmed: false, status: "ready", stale: false } }); const packageKey = STAGE_TO_PACKAGE_KEY[activePipelineStage]; const storyboardPackage = { ...(data?.storyboardPackage || {}) }; if (packageKey) storyboardPackage[packageKey] = parsed; if (activePipelineStage === "final" && parsed?.render_manifest) storyboardPackage.render_manifest = parsed.render_manifest; patchData({ pipelineStages: nextStages, storyboardPackage }); setTimedFeedback(setActionFeedback, "saveStage", "Правки сохранены ✓", 1800); } catch (_e) { patchData({ pipelineStages: { ...pipelineStages, [activePipelineStage]: { ...ensureStage(activePipelineStage), status: "error", error: "Невалидный JSON" } } }); } }}>{actionFeedback.saveStage || "Сохранить правки"}</button><button className="clipSB_btn" disabled={stageBusyOrLocked || (!st.output && !st.editedOutput)} onClick={() => { const current = ensureStage(activePipelineStage); const nextStages = { ...pipelineStages, [activePipelineStage]: { ...current, status: "confirmed", confirmed: true, stale: false } }; const idx = STAGE_ORDER.indexOf(activePipelineStage); const nextKey = STAGE_ORDER[idx + 1]; if (nextKey) { nextStages[nextKey] = { ...ensureStage(nextKey), status: "idle", stale: false }; patchData({ pipelineStages: nextStages, selectedPipelineStage: nextKey, activePipelineStage: nextKey, directorInfo: `Этап ${activePipelineStage.toUpperCase()} подтверждён. Открыт следующий этап ${nextKey.toUpperCase()}.` }); } else { patchData({ pipelineStages: nextStages, selectedPipelineStage: "final", activePipelineStage: "final", directorInfo: "FINAL подтверждён. Теперь можно передать в Scenario Storyboard." }); } }}>Подтвердить этап</button></div>{st.error ? <div className="asdv2_emptyState">Ошибка: {st.error}</div> : null}</>; })()}</div></div> : null}
        {directorViewMode === "pipeline" ? null : <><div className="asdv2_mainGrid">
          <div className="asdv2_panel asdv2_contractPanel">
            <div className="asdv2_panelHead"><strong>Черновик контракта режиссёра</strong></div>
            {!draftContract ? <div className="asdv2_emptyState">Черновик ещё не создан. Разбери аудио, обсуди клип в чате и нажми «Сгенерировать черновик».</div> : <div className="asdv2_contractList">
              {CONTRACT_SECTIONS.map(([key, label]) => {
                const value = draftContract?.[key];
                if (value == null || value === "") return null;
                return <div key={key} className="asdv2_contractCard"><b>{label}</b><small>{renderContractValue(value)}</small></div>;
              })}
            </div>}
            <div className="asdv2_copyRow">
              <button className={`clipSB_btn ${copyFeedback.contractJson === "Не удалось скопировать" ? "asdv2_copyError" : (copyFeedback.contractJson ? "asdv2_copyOk" : "")}`} disabled={!draftContract} onClick={() => copyText(JSON.stringify(draftContract || {}, null, 2), "contractJson", "JSON скопирован ✓")}>{copyFeedback.contractJson || "Скопировать JSON"}</button>
              <button className={`clipSB_btn ${copyFeedback.contractSummary === "Не удалось скопировать" ? "asdv2_copyError" : (copyFeedback.contractSummary ? "asdv2_copyOk" : "")}`} disabled={!draftContract} onClick={() => copyText(CONTRACT_SECTIONS.map(([key, label]) => `${label}: ${renderContractValue(draftContract?.[key] ?? "—")}`).join("\n\n"), "contractSummary", "Summary скопирован ✓")}>{copyFeedback.contractSummary || "Скопировать summary"}</button>
            </div>
          </div>

          <div className={`asdv2_panel asdv2_chatPanel ${isChatLocked ? "asdv2_lockedPanel" : ""}`}>
            <div className="asdv2_panelHead">
              <strong>AI-чат / обсуждение клипа</strong>{data?.directorV2Package ? <button className="clipSB_btn" onClick={() => patchData({ directorViewMode: "pipeline" })}>Открыть Pipeline Review</button> : null}
              <button className={`clipSB_btn ${copyFeedback.lastReply === "Не удалось скопировать" ? "asdv2_copyError" : (copyFeedback.lastReply ? "asdv2_copyOk" : "")}`} disabled={!lastAssistantMessage} onClick={() => copyText(lastAssistantMessage, "lastReply", "Ответ скопирован ✓")}>{copyFeedback.lastReply || "Скопировать последний ответ"}</button>
            </div>
            <div ref={chatMessagesRef} className="asdv2_chatMessages">{chatMessages.map((m, i) => {
              const isAssistant = m?.role === "assistant";
              const copyKey = `msg_${i}`;
              const copyLabel = copyFeedback[copyKey] || "Копировать";
              return <div key={i} className={`asdv2_chatMsg ${isAssistant ? "isAssistant" : "isUser"}`}>
                <div className="asdv2_chatMsgTop">
                  <span className={`asdv2_chatRole ${isAssistant ? "isAssistant" : "isUser"}`}>{isAssistant ? "AI" : "Вы"}</span>
                  {isAssistant ? <button className={`clipSB_btn asdv2_chatCopyBtn ${copyLabel === "Не удалось скопировать" ? "asdv2_copyError" : (copyLabel !== "Копировать" ? "asdv2_copyOk" : "")}`} onClick={() => copyText(String(m?.text || ""), copyKey, "Скопировано")}>{copyLabel}</button> : null}
                </div>
                <div className="asdv2_chatMsgBody">{isAssistant ? renderAssistantMarkdown(m?.text || "") : <p>{String(m?.text || "")}</p>}</div>
              </div>;
            })}</div>
            {isChatLocked ? <div className="asdv2_emptyState">Сначала разбери аудио. После этого AI увидит сегменты, длительность, фразы и сможет обсудить структуру клипа.</div> : null}
            <div className="asdv2_chatComposer"><textarea className="asdv2_chatInput" value={chatInput} disabled={isChatLocked || Boolean(data?.directorChatPending)} onChange={(e) => setChatInput(e.target.value)} /><button className="clipSB_btn" disabled={isChatLocked || Boolean(data?.directorChatPending)} onClick={onSend}>{data?.directorChatPending ? "AI думает..." : "Отправить"}</button></div>
          </div>

          <div className="asdv2_panel asdv2_audioMapPanel">
            <div className="asdv2_panelHead"><strong>Аудио-разбор</strong></div>
            {!hasAudio ? <div className="asdv2_emptyState">Сначала подключи аудио.</div> : !audioMap ? <div className="asdv2_emptyState">Аудио подключено. Нажми «Разобрать аудио», чтобы получить сегменты, тайминги и lip-sync окна.</div> : <>
              <div>Статус: audio_map готов</div><div>Фразы audio_map: {segments.length}</div>{sceneCandidateCount ? <div>Кандидаты сцен: {sceneCandidateCount}</div> : null}<div>Lip-sync кандидатов: {segments.filter((s) => s?.isLipSyncCandidate).length}</div><div className="asdv2_hint">segments[] — это фразы, AI Director может объединять их в сцены.</div>
              {(audioMap?.mode_audio_reading || audioMap?.director_audio_brief) ? <div className="asdv2_hint"><b>Режиссёрская подсказка аудио</b>{audioMap?.director_audio_brief?.summary ? <div>{audioMap.director_audio_brief.summary}</div> : null}{audioMap?.director_audio_brief?.likely_scene_count_range ? <div>Реком. число сцен: {audioMap.director_audio_brief.likely_scene_count_range.min}–{audioMap.director_audio_brief.likely_scene_count_range.max}</div> : null}{Array.isArray(audioMap?.director_audio_brief?.must_ask_user) && audioMap.director_audio_brief.must_ask_user.length ? <div>Спросить: {audioMap.director_audio_brief.must_ask_user.slice(0, 2).join(", ")}</div> : null}{Array.isArray(audioMap?.mode_audio_reading?.warnings) && audioMap.mode_audio_reading.warnings.length ? <div>⚠ {audioMap.mode_audio_reading.warnings[0]}</div> : null}</div> : null}
              <div className="asdv2_audioSegments">{segments.map((seg) => <div key={seg.id} className="asdv2_audioSegment"><div><b>{seg.id}</b> · {fmt(seg.startSec)}–{fmt(seg.endSec)}</div><div>lip-sync: {seg.isLipSyncCandidate ? "да" : "нет"} · intensity: {seg.intensity.toFixed(2)}</div>{seg.transcript ? <div>Фраза: {seg.transcript}</div> : null}</div>)}</div>
            </>}
            <div className="asdv2_copyRow">
              <button className={`clipSB_btn ${copyFeedback.audioMap === "Не удалось скопировать" ? "asdv2_copyError" : (copyFeedback.audioMap ? "asdv2_copyOk" : "")}`} disabled={!audioMap} onClick={() => copyText(JSON.stringify(audioMap || {}, null, 2), "audioMap", "audio_map скопирован ✓")}>{copyFeedback.audioMap || "Скопировать audio_map JSON"}</button>
              <button className={`clipSB_btn ${copyFeedback.segments === "Не удалось скопировать" ? "asdv2_copyError" : (copyFeedback.segments ? "asdv2_copyOk" : "")}`} disabled={!segments.length} onClick={() => copyText(segmentLines.join("\n"), "segments", "Сегменты скопированы ✓")}>{copyFeedback.segments || "Скопировать сегменты"}</button>
              <button className={`clipSB_btn ${copyFeedback.phrases === "Не удалось скопировать" ? "asdv2_copyError" : (copyFeedback.phrases ? "asdv2_copyOk" : "")}`} disabled={!transcriptLines.length} onClick={() => copyText(transcriptLines.join("\n"), "phrases", "Фразы скопированы ✓")}>{copyFeedback.phrases || "Скопировать фразы"}</button>
            </div>
          </div>
        </div>
        <div className="asdv2_panel asdv2_planPanel"><strong>План клипа</strong>{draftPlan.length ? <div className="asdv2_plan">{draftPlan.map((scene, idx) => <div className="asdv2_scene" key={scene.scene_id || idx}><b>Сцена {idx + 1}</b><small>{scene.segment_id || "—"} · {scene.route || "—"}</small><div className="asdv2_scenePhrase">{scene.audio_phrase || scene.transcript || scene.phrase || "Фраза не указана"}</div><small>{fmt(scene.start_sec)}–{fmt(scene.end_sec)} · {scene.timeline_role || "роль не указана"}</small><p>{scene.user_visible_description || scene.purpose || ""}</p><div className="asdv2_sceneActions"><button className="clipSB_btn" disabled>Редактировать</button><button className="clipSB_btn" disabled>Закрепить</button><button className="clipSB_btn" disabled>Переместить</button></div></div>)}</div> : <div className="asdv2_emptyState">План клипа появится здесь после генерации режиссёрского черновика.</div>}</div></> }
        {Array.isArray(data?.questionsResolved) && data.questionsResolved.length ? <div className="asdv2_panel"><strong>Уточнено</strong><ul>{data.questionsResolved.map((item, idx) => <li key={`resolved_${idx}`}>{String(item || "")}</li>)}</ul></div> : null}
        {Array.isArray(data?.remainingRisks) && data.remainingRisks.length ? <div className="asdv2_panel"><strong>Что проверить перед применением</strong><ul>{data.remainingRisks.map((risk, idx) => <li key={`risk_${idx}`}>{String(risk || "")}</li>)}</ul></div> : null}
        {error ? <div className="asdv2_emptyState">Ошибка: {error}</div> : null}
        {info ? <div className="asdv2_emptyState">{info}</div> : null}
      </div>
    </NodeShell>
  </>);
}

export { DIRECTOR_STATES };
