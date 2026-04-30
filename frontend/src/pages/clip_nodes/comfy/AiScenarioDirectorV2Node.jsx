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

const PLAN = [
  ["1", "IA2V", "Настоящее", "...", "Мужик поёт в купе поезда."],
  ["2", "I2V", "Воспоминание", "...", "Молодой герой дерётся во дворе."],
  ["3", "IA2V", "Настоящее", "...", "Мужик поёт в тамбуре с сигаретой в руке."],
  ["4", "I2V", "Воспоминание", "...", "Молодой герой ворует и убегает."],
  ["5", "IA2V", "Настоящее", "...", "Мужик в вагоне-ресторане за столом с едой."],
  ["6", "I2V", "Воспоминание", "...", "Молодой герой дарит цветы девушке."],
  ["7", "IA2V", "Финал", "...", "Мужик поёт в том же купе, поезд приближается к Одессе."],
];

const STAGES = [
  { key: "plan", label: "PLAN" },
  { key: "audio", label: "AUDIO" },
  { key: "core", label: "CORE" },
  { key: "roles", label: "ROLES" },
  { key: "scenes", label: "SCENES" },
  { key: "prompts", label: "PROMPTS" },
  { key: "final_video_prompt", label: "FINAL VIDEO PROMPT" },
  { key: "final", label: "FINAL" },
];

const viewerText = {
  plan: "PLAN: краткий план карточек клипа",
  audio: "Здесь будет разбивка аудио",
  core: "Здесь будет смысловой позвоночник истории",
  roles: "Здесь будет распределение ролей",
  scenes: "Здесь будет план сцен",
  prompts: "Здесь будут фото/видео промты",
  final_video_prompt: "Здесь будут финальные LTX-ready промты",
  final: "Здесь будет manifest сборки",
};

const toneToColor = {
  audio: "var(--family-audio)",
  character: "var(--family-ref-character)",
  location: "var(--family-ref-location)",
  style: "var(--family-ref-style)",
  video: "var(--family-video-ref)",
  props: "var(--family-ref-items)",
  text: "var(--family-text)",
};

const formatStatus = (status) => {
  if (!status || status === "idle") return "ожидает";
  if (status === "done") return "готово";
  if (status === "error") return "ошибка";
  if (status === "stale") return "устарело";
  if (status === "running") return "в работе";
  return String(status);
};

export default function AiScenarioDirectorV2Node({ id, data }) {
  const [stage, setStage] = useState("plan");
  const statuses = useMemo(() => data?.stageStatuses || {}, [data?.stageStatuses]);
  const handleUiStubAction = (action) => {
    console.log("[AI SCENARIO DIRECTOR V2] action stub", { nodeId: id, action });
  };

  return (
    <>
      {INPUTS.map((item, index) => (
        <Handle key={item.id} type="target" position={Position.Left} id={item.id} className="clipSB_handle" style={{ ...handleStyle(item.id), top: 48 + index * 24 }} />
      ))}
      <Handle type="source" position={Position.Right} id="scenario_out_v2" className="clipSB_handle" style={handleStyle("scenario_out")} />
      <NodeShell title="AI СЦЕНАРИСТ / РЕЖИССЁР" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎬</span>} className="clipSB_nodeStoryboard" style={{ minWidth: 1120 }}>
        <div className="asdv2_body">
          <div className="asdv2_toolbar">
            <div className="asdv2_titleBlock">
              <div className="asdv2_sub">Режиссёрская нода V2 / планирование клипа</div>
            </div>
            <div className="asdv2_controls">
              <select className="asdv2_select" defaultValue="Клип"><option>Клип</option><option>История</option><option>Реклама</option><option>Кино</option><option>Тест</option></select>
              <select className="asdv2_select" defaultValue="9:16"><option>9:16</option><option>16:9</option><option>1:1</option></select>
              <span className="asdv2_chip">Черновик</span>
            </div>
            <div className="asdv2_actions">
              <button className="clipSB_btn" type="button" title="UI-заготовка: backend ещё не подключён" onClick={() => handleUiStubAction("analyze_inputs")}>Проверить входы</button>
              <button className="clipSB_btn" type="button" title="UI-заготовка: backend ещё не подключён" onClick={() => handleUiStubAction("build_plan")}>Собрать план</button>
              <button className="clipSB_btn" type="button" title="UI-заготовка: backend ещё не подключён" onClick={() => handleUiStubAction("build_contract")}>Собрать контракт</button>
              <button className="clipSB_btn" type="button" title="UI-заготовка: backend ещё не подключён" onClick={() => handleUiStubAction("run_next_stage")}>Запустить следующий этап</button>
            </div>
          </div>

          <div className="asdv2_inputsBar">
            {INPUTS.map((item) => {
              const connected = Boolean(data?.connections?.[item.id]);
              return (
                <div key={item.id} className={`asdv2_inputChip ${connected ? "isConnected" : "isEmpty"}`} style={{ borderColor: toneToColor[item.tone] || "rgba(255,255,255,0.2)" }} title={item.placeholder}>
                  <span>{item.label}</span>
                  <b>{connected ? "✓" : "пусто"}</b>
                </div>
              );
            })}
          </div>

          <div className="asdv2_mainGrid">
            <div className="asdv2_panel asdv2_panelCompact">
              <strong>AI-помощник</strong>
              <p>Опиши клип, который хочешь получить.</p>
              <input className="asdv2_inputLine" placeholder="Например: мужик поёт в купе, между сценами воспоминания молодости..." readOnly />
              <div className="asdv2_row"><button className="clipSB_btn" type="button">Больше сюжета</button><button className="clipSB_btn" type="button">Больше lip-sync</button><button className="clipSB_btn" type="button">50/50</button><button className="clipSB_btn" type="button">Без first_last</button></div>
            </div>

            <div className="asdv2_panel asdv2_panelCompact">
              <div className="asdv2_row asdv2_between"><strong>Черновик контракта режиссёра</strong><button className="clipSB_btn" type="button">JSON</button></div>
              <div className="asdv2_contractGrid">
                {["Замысел", "Роли", "Мир", "Маршруты", "Обязательные сцены", "Референсы", "Монтаж"].map((name) => (
                  <div key={name} className="asdv2_contractCard"><b>{name}</b><small>Черновик раздела контракта.</small></div>
                ))}
              </div>
            </div>

            <div className="asdv2_panel asdv2_panelCompact">
              <strong>Этапы pipeline</strong>
              <div className="asdv2_pipelineList">
                {STAGES.map((item) => {
                  const info = statuses?.[item.key] || statuses?.[item.label] || {};
                  return (
                    <button key={item.key} type="button" className={`asdv2_stage ${stage === item.key ? "isActive" : ""}`} onClick={() => setStage(item.key)}>
                      <b>{item.label}</b>
                      <span>{formatStatus(info.status)}</span>
                      <small>{String(info.summary || "Ожидает")}</small>
                    </button>
                  );
                })}
              </div>
              <div className="asdv2_viewer">{viewerText[stage] || ""}</div>
            </div>
          </div>

          <div className="asdv2_panel asdv2_planPanel">
            <strong>План клипа</strong>
            <div className="asdv2_plan">
              {PLAN.map(([idx, route, timeline, phrase, text]) => (
                <div key={idx} className="asdv2_scene">
                  <div className="asdv2_row"><b>#{idx}</b><span className="asdv2_tag">{route}</span><span className="asdv2_tag">{timeline}</span></div>
                  <small>Фраза: "{phrase}"</small>
                  <p>{text}</p>
                  <small>изменить / закрепить / переместить</small>
                </div>
              ))}
            </div>
          </div>
        </div>
      </NodeShell>
    </>
  );
}
