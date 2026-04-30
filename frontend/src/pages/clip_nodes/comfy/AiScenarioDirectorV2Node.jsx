import React, { useMemo, useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { NodeShell, handleStyle } from "./comfyNodeShared";

const INPUTS = [
  { id: "audio_in", label: "Аудио", tone: "audio", placeholder: "Аудио не подключено" },
  { id: "ref_character_1", label: "Персонаж 1", tone: "character", placeholder: "Референс главного персонажа" },
  { id: "ref_character_2", label: "Персонаж 2", tone: "character", placeholder: "Референс второго персонажа" },
  { id: "ref_character_3", label: "Персонаж 3", tone: "character", placeholder: "Референс третьего персонажа" },
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


const toneToColor = { audio: "var(--family-audio)", character: "var(--family-ref-character)", location: "var(--family-ref-location)", style: "var(--family-ref-style)", video: "var(--family-video-ref)", props: "var(--family-ref-items)", text: "var(--family-text)" };
const fmt = (v) => Number(v || 0).toFixed(2);
const isObject = (v) => !!v && typeof v === "object";

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
  const [chatInput, setChatInput] = useState("");
  const isApplied = data?.directorState === DIRECTOR_STATES.APPLIED;
  const connections = data?.connections || {};
  const connectedInputs = isObject(data?.connectedInputs) ? data.connectedInputs : {};
  const hasAudio = Boolean(connectedInputs?.audio_in || connections.audio_in);
  const directorState = data?.directorState || (hasAudio ? DIRECTOR_STATES.READY_TO_PARSE_AUDIO : DIRECTOR_STATES.WAIT_INPUTS);
  const audioMap = data?.audioMap || null;
  const chatMessages = Array.isArray(data?.chatMessages) ? data.chatMessages : [];
  const draftContract = data?.draftContract || null;
  const draftPlan = Array.isArray(data?.draftPlan) ? data.draftPlan : [];
  const error = data?.directorError || "";
  const info = data?.directorInfo || "";
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

  const patchData = (patch) => data?.onPatchNodeData?.(id, patch);
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
      directorError: "",
      chatMessages: [...chatMessages, { role: "user", text: userMessage }, { role: "assistant", text: String(result?.assistantReply || "") }],
    });
  };

  const onGenerateDraft = async () => {
    if (!data?.onGenerateDirectorDraft) return;
    patchData({ directorState: DIRECTOR_STATES.GENERATING_DRAFT, directorError: "", directorInfo: "" });
    const result = await data.onGenerateDirectorDraft(id);
    if (!result?.ok) return patchData({ directorState: DIRECTOR_STATES.ERROR, directorError: String(result?.error || "Ошибка генерации черновика") });
    patchData({ directorState: DIRECTOR_STATES.DRAFT_READY, draftContract: result.draftContract || {}, draftPlan: result.draftPlan || [], draftIsDemo: Boolean(result?.isDemo) });
  };

  const onApply = () => {
    const directorV2Package = {
      director_contract: draftContract || {},
      draft_plan: draftPlan || [],
      audio_map: audioMap || {},
      chat_history: chatMessages || [],
      connected_inputs: connectedInputs || {},
      mode: "clip",
      format: data?.format || "9:16",
      content_type: "music_video",
    };
    console.log("[AI SCENARIO DIRECTOR V2] apply", directorV2Package);
    patchData({
      directorState: DIRECTOR_STATES.APPLIED,
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
    directorV2Package: null, directorError: "", directorInfo: "", draftIsDemo: false, storyboardPackage: null, stageStatuses: {},
    parsedAudioSourceNodeId: "", parsedAudioUrl: "",
  });
  const chipSource = Object.keys(connectedInputs).length ? connectedInputs : connections;

  return (<><Handle type="source" position={Position.Right} id="scenario_out_v2" className="clipSB_handle" style={handleStyle("scenario_out")} />
    {INPUTS.map((item, index) => <Handle key={item.id} type="target" position={Position.Left} id={item.id} className="clipSB_handle" style={{ ...handleStyle(item.id), top: 48 + index * 24 }} />)}
    <NodeShell title="AI РЕЖИССЁР V2" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎬</span>} className="clipSB_nodeStoryboard asdv2_shell" style={{ minWidth: 1120 }}>
      <div className="asdv2_body">
        <div className="asdv2_toolbar"><div className="asdv2_sub">Пошаговый режиссёрский flow</div><span className="asdv2_stepBadge">Состояние: {directorState}</span>
          <div className="asdv2_actions">
            <button className="clipSB_btn asdv2_primaryAction" disabled={!hasAudio || isParseLocked} onClick={parseAudio}>{audioMap ? "Переразобрать аудио" : "Разобрать аудио"}</button>
            <button className="clipSB_btn" disabled={isApplied || !audioMap || directorState === DIRECTOR_STATES.GENERATING_DRAFT || isAudioChangedAfterParse} onClick={onGenerateDraft}>{draftPlan.length ? "Перегенерировать" : "Сгенерировать черновик"}</button>
            <button className="clipSB_btn" disabled={isApplied || directorState !== DIRECTOR_STATES.DRAFT_READY} onClick={() => patchData({ directorState: DIRECTOR_STATES.DRAFT_CONFIRMED, confirmed: true })}>Подтвердить</button>
            <button className="clipSB_btn" disabled={isApplied || Boolean(data?.draftIsDemo) || directorState !== DIRECTOR_STATES.DRAFT_CONFIRMED} onClick={onApply}>Применить к CORE</button>
            <button className="clipSB_btn" onClick={onReset}>Сбросить</button>
          </div></div>
        <div className="asdv2_inputsBar">{INPUTS.map((input) => <div key={input.id} className={`asdv2_inputChip ${chipSource?.[input.id] ? "isConnected" : "isEmpty"}`} style={{ borderColor: toneToColor[input.tone] || "rgba(255,255,255,0.2)" }}>{input.label}: {chipSource?.[input.id] ? "✓" : "пусто"}</div>)}</div>
        {isAudioChangedAfterParse ? <div className="asdv2_emptyState">Подключённое аудио изменилось. Нажми «Сбросить» и разбери новое аудио.</div> : null}
        <div className="asdv2_mainGrid">
          <div className={`asdv2_panel asdv2_chatPanel ${isChatLocked ? "asdv2_lockedPanel" : ""}`}><strong>AI-чат</strong><div className="asdv2_chatMessages">{chatMessages.map((m, i) => <div key={i} className="asdv2_chatMsg"><b>{m.role === "assistant" ? "AI" : "Вы"}:</b> {m.text}</div>)}</div>
            {isChatLocked ? <div className="asdv2_emptyState">Сначала разбери аудио. После этого AI сможет видеть сегменты, длительность, фразы и предложить структуру клипа.</div> : null}
            <div className="asdv2_chatComposer"><textarea className="asdv2_chatInput" value={chatInput} disabled={isChatLocked || Boolean(data?.directorChatPending)} onChange={(e) => setChatInput(e.target.value)} /><button className="clipSB_btn" disabled={isChatLocked || Boolean(data?.directorChatPending)} onClick={onSend}>{data?.directorChatPending ? "AI думает..." : "Отправить"}</button></div></div>

          <div className="asdv2_panel"><strong>Черновик контракта режиссёра</strong>{!draftContract ? <div className="asdv2_emptyState">Черновик режиссёра ещё не создан. Сначала разбери аудио, обсуди клип в чате и нажми «Сгенерировать черновик».</div> : <div className="asdv2_contractGrid">{Object.entries(draftContract).map(([k, v]) => <div key={k} className="asdv2_contractCard"><b>{k}</b><small>{String(v)}</small></div>)}</div>}<div className="asdv2_draftActions">{directorState === DIRECTOR_STATES.DRAFT_CONFIRMED ? "Черновик подтверждён. Теперь можно применить его к цепочке." : ""}</div></div>

          <div className="asdv2_panel asdv2_audioMapPanel"><strong>Аудио и этапы</strong>{!hasAudio ? <div className="asdv2_emptyState">Сначала подключи аудио.</div> : !audioMap ? <div className="asdv2_emptyState">Аудио подключено. Нажми «Разобрать аудио», чтобы получить сегменты, тайминги и lip-sync окна.</div> : <><div>Статус: audio_map готов</div><div>Сегментов: {segments.length}</div><div>Lip-sync кандидатов: {segments.filter((s) => s?.isLipSyncCandidate).length}</div><div className="asdv2_chatMessages">{segments.map((seg) => <div key={seg.id} className="asdv2_audioSegment">{seg.id} · {fmt(seg.startSec)}–{fmt(seg.endSec)} · {seg.isLipSyncCandidate ? "lip-sync ✓" : "lip-sync —"} {seg.intensity ? `· intensity ${seg.intensity.toFixed(2)}` : ""}{seg.transcript ? <div>"{seg.transcript}"</div> : null}</div>)}</div></>}</div>
        </div>
        <div className="asdv2_panel asdv2_planPanel"><strong>План клипа</strong>{directorState === DIRECTOR_STATES.DRAFT_READY || directorState === DIRECTOR_STATES.DRAFT_CONFIRMED || directorState === DIRECTOR_STATES.APPLIED ? <div className="asdv2_plan">{draftPlan.map((scene, idx) => <div className="asdv2_scene" key={scene.scene_id || idx}><b>{scene.scene_id || `scene_${idx + 1}`}</b><small>{scene.start_sec}–{scene.end_sec}</small><p>{scene.user_visible_description || scene.purpose || ""}</p></div>)}</div> : <div className="asdv2_emptyState">План клипа появится здесь после генерации режиссёрского черновика.</div>}</div>
        {error ? <div className="asdv2_emptyState">Ошибка: {error}</div> : null}
        {info ? <div className="asdv2_emptyState">{info}</div> : null}
      </div>
    </NodeShell>
  </>);
}

export { DIRECTOR_STATES };
