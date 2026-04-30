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

const quickPrompts = ["Больше сюжета", "Больше пения / lip-sync", "Сделать 50/50", "Без first/last кадров", "Добавить воспоминания", "Начать с перрона", "Финал в поезде"];

const toneToColor = { audio: "var(--family-audio)", character: "var(--family-ref-character)", location: "var(--family-ref-location)", style: "var(--family-ref-style)", video: "var(--family-video-ref)", props: "var(--family-ref-items)", text: "var(--family-text)" };
const fmt = (v) => Number(v || 0).toFixed(2);

export default function AiScenarioDirectorV2Node({ id, data }) {
  const [chatInput, setChatInput] = useState("");
  const connections = data?.connections || {};
  const hasAudio = Boolean(connections.audio_in);
  const directorState = data?.directorState || (hasAudio ? DIRECTOR_STATES.READY_TO_PARSE_AUDIO : DIRECTOR_STATES.WAIT_INPUTS);
  const audioMap = data?.audioMap || null;
  const chatMessages = Array.isArray(data?.chatMessages) ? data.chatMessages : [];
  const draftContract = data?.draftContract || null;
  const draftPlan = Array.isArray(data?.draftPlan) ? data.draftPlan : [];
  const error = data?.directorError || "";

  const segments = useMemo(() => {
    const raw = audioMap?.segments || audioMap?.narrative_segments || [];
    return Array.isArray(raw) ? raw : [];
  }, [audioMap]);

  const patchData = (patch) => data?.onPatchNodeData?.(id, patch);
  const isChatLocked = !(directorState === DIRECTOR_STATES.AUDIO_PARSED || directorState === DIRECTOR_STATES.CHAT_ACTIVE || directorState === DIRECTOR_STATES.GENERATING_DRAFT || directorState === DIRECTOR_STATES.DRAFT_READY || directorState === DIRECTOR_STATES.DRAFT_CONFIRMED || directorState === DIRECTOR_STATES.APPLYING || directorState === DIRECTOR_STATES.APPLIED);

  const parseAudio = async () => {
    if (!data?.onParseAudioStage) return;
    patchData({ directorState: DIRECTOR_STATES.PARSING_AUDIO, directorError: "" });
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
    });
  };

  const onSend = () => {
    if (!chatInput.trim()) return;
    patchData({
      directorState: DIRECTOR_STATES.CHAT_ACTIVE,
      chatMessages: [...chatMessages, { role: "user", text: chatInput.trim() }, { role: "assistant", text: "Принял. На следующем шаге Gemini уточнит структуру клипа и соберёт director contract." }],
    });
    setChatInput("");
  };

  const onGenerateDraft = async () => {
    if (!data?.onGenerateDirectorDraft) return;
    patchData({ directorState: DIRECTOR_STATES.GENERATING_DRAFT, directorError: "" });
    const result = await data.onGenerateDirectorDraft(id);
    if (!result?.ok) return patchData({ directorState: DIRECTOR_STATES.ERROR, directorError: String(result?.error || "Ошибка генерации черновика") });
    patchData({ directorState: DIRECTOR_STATES.DRAFT_READY, draftContract: result.draftContract || {}, draftPlan: result.draftPlan || [], draftIsDemo: Boolean(result?.isDemo) });
  };

  const onApply = () => {
    const directorV2Package = { director_contract: draftContract || {}, draft_plan: draftPlan || [], audio_map: audioMap || {}, chat_history: chatMessages || [] };
    console.log("[AI SCENARIO DIRECTOR V2] apply", directorV2Package);
    patchData({ directorState: DIRECTOR_STATES.APPLIED, confirmed: true, applied: true, directorV2Package });
  };

  return (<><Handle type="source" position={Position.Right} id="scenario_out_v2" className="clipSB_handle" style={handleStyle("scenario_out")} />
    {INPUTS.map((item, index) => <Handle key={item.id} type="target" position={Position.Left} id={item.id} className="clipSB_handle" style={{ ...handleStyle(item.id), top: 48 + index * 24 }} />)}
    <NodeShell title="AI РЕЖИССЁР V2" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎬</span>} className="clipSB_nodeStoryboard asdv2_shell" style={{ minWidth: 1120 }}>
      <div className="asdv2_body">
        <div className="asdv2_toolbar"><div className="asdv2_sub">Пошаговый режиссёрский flow</div><span className="asdv2_stepBadge">Состояние: {directorState}</span>
          <div className="asdv2_actions">
            <button className="clipSB_btn asdv2_primaryAction" disabled={!hasAudio || directorState === DIRECTOR_STATES.PARSING_AUDIO} onClick={parseAudio}>{audioMap ? "Переразобрать аудио" : "Разобрать аудио"}</button>
            <button className="clipSB_btn" disabled={!audioMap || directorState === DIRECTOR_STATES.GENERATING_DRAFT} onClick={onGenerateDraft}>{draftPlan.length ? "Перегенерировать" : "Сгенерировать черновик"}</button>
            <button className="clipSB_btn" disabled={directorState !== DIRECTOR_STATES.DRAFT_READY} onClick={() => patchData({ directorState: DIRECTOR_STATES.DRAFT_CONFIRMED, confirmed: true })}>Подтвердить</button>
            <button className="clipSB_btn" disabled={directorState !== DIRECTOR_STATES.DRAFT_CONFIRMED} onClick={onApply}>Применить</button>
          </div></div>
        <div className="asdv2_mainGrid">
          <div className={`asdv2_panel asdv2_chatPanel ${isChatLocked ? "asdv2_lockedPanel" : ""}`}><strong>AI-чат</strong><div className="asdv2_chatMessages">{chatMessages.map((m, i) => <div key={i} className="asdv2_chatMsg"><b>{m.role === "assistant" ? "AI" : "Вы"}:</b> {m.text}</div>)}</div>
            {isChatLocked ? <div className="asdv2_emptyState">Сначала разбери аудио. После этого AI сможет видеть сегменты, длительность, фразы и предложить структуру клипа.</div> : null}
            <div className="asdv2_chatComposer"><textarea className="asdv2_chatInput" value={chatInput} disabled={isChatLocked} onChange={(e) => setChatInput(e.target.value)} /><button className="clipSB_btn" disabled={isChatLocked} onClick={onSend}>Отправить</button></div>
            <div className="asdv2_quickPrompts">{quickPrompts.map((p) => <button key={p} className="clipSB_btn" disabled={isChatLocked} onClick={() => setChatInput((prev) => `${prev}${prev ? " " : ""}${p}`)}>{p}</button>)}</div></div>

          <div className="asdv2_panel"><strong>Черновик контракта режиссёра</strong>{!draftContract ? <div className="asdv2_emptyState">Черновик режиссёра ещё не создан. Сначала разбери аудио, обсуди клип в чате и нажми «Сгенерировать черновик».</div> : <div className="asdv2_contractGrid">{Object.entries(draftContract).map(([k, v]) => <div key={k} className="asdv2_contractCard"><b>{k}</b><small>{String(v)}</small></div>)}</div>}<div className="asdv2_draftActions">{directorState === DIRECTOR_STATES.DRAFT_CONFIRMED ? "Черновик подтверждён. Теперь можно применить его к цепочке." : ""}</div></div>

          <div className="asdv2_panel asdv2_audioMapPanel"><strong>Аудио и этапы</strong>{!hasAudio ? <div className="asdv2_emptyState">Сначала подключи аудио.</div> : !audioMap ? <div className="asdv2_emptyState">Аудио подключено. Нажми «Разобрать аудио», чтобы получить сегменты, тайминги и lip-sync окна.</div> : <><div>Статус: аудио разобрано</div><div>Длительность: {Number(audioMap?.duration_sec || 0).toFixed(2)} сек</div><div>Сегментов: {segments.length}</div><div>Lip-sync кандидатов: {segments.filter((s) => s?.is_lip_sync_candidate).length}</div><div>Источник: AUDIO stage / Audio Map</div><div className="asdv2_chatMessages">{segments.map((seg, index) => <div key={seg.segment_id || index} className="asdv2_audioSegment">{seg.segment_id || `seg_${String(index + 1).padStart(2, "0")}`} · {fmt(seg.start_sec)}–{fmt(seg.end_sec)} · {seg.is_lip_sync_candidate ? "lip-sync ✓" : ""} {seg.intensity ? `· intensity ${Number(seg.intensity).toFixed(2)}` : ""}</div>)}</div></>}</div>
        </div>
        <div className="asdv2_panel asdv2_planPanel"><strong>План клипа</strong>{directorState === DIRECTOR_STATES.DRAFT_READY || directorState === DIRECTOR_STATES.DRAFT_CONFIRMED || directorState === DIRECTOR_STATES.APPLIED ? <div className="asdv2_plan">{draftPlan.map((scene, idx) => <div className="asdv2_scene" key={scene.scene_id || idx}><b>{scene.scene_id || `scene_${idx + 1}`}</b><small>{scene.start_sec}–{scene.end_sec}</small><p>{scene.user_visible_description || scene.purpose || ""}</p></div>)}</div> : <div className="asdv2_emptyState">План клипа появится здесь после генерации режиссёрского черновика.</div>}</div>
        {error ? <div className="asdv2_emptyState">Ошибка: {error}</div> : null}
      </div>
    </NodeShell>
  </>);
}

export { DIRECTOR_STATES };
