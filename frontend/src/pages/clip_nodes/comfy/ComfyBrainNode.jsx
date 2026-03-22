import React from "react";
import { COMFY_BRAIN_GENRE_OPTIONS, normalizeComfyGenre } from "./comfyBrainDomain";
import BrainPackageView, { isBrainPackageObject } from "./BrainPackageView";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";

const COMFY_BRAIN_FORMAT_OPTIONS = ["9:16", "16:9", "1:1"];

export default function ComfyBrainNode({ id, data }) {
  const mode = data?.mode || "clip";
  const plannerMode = data?.plannerMode || "legacy";
  const plannerBadgeLabel = plannerMode === "gemini_only" ? "Gemini" : "Legacy";
  const plannerBadgeClass = plannerMode === "gemini_only" ? "clipSB_comfyPlannerBadge clipSB_comfyPlannerBadge--gemini" : "clipSB_comfyPlannerBadge clipSB_comfyPlannerBadge--legacy";
  const output = data?.output || "comfy image";
  const genre = normalizeComfyGenre(data?.genre || "");
  const format = COMFY_BRAIN_FORMAT_OPTIONS.includes(data?.format) ? data.format : "9:16";
  const parseStatus = data?.parseStatus || "idle";
  const isParsing = parseStatus === "parsing";
  const isReady = parseStatus === "ready";
  const isError = parseStatus === "error";
  const parseButtonLabel = isParsing ? "Разбираю..." : "Разобрать";
  const brainStateClass = isParsing
    ? "clipSB_nodeComfyBrainStateParsing"
    : isReady
      ? "clipSB_nodeComfyBrainStateReady"
      : isError
        ? "clipSB_nodeComfyBrainStateError"
        : "";
  const plannerModeClass = plannerMode === "gemini_only" ? "clipSB_nodeComfyBrainPlannerGemini" : "clipSB_nodeComfyBrainPlannerLegacy";
  const visibleMode = ["clip", "kino"].includes(String(mode || "").toLowerCase()) ? mode : "clip";
  const visibleOutput = output === "comfy image" ? output : "comfy image";
  const narrativeBrainPackage = isBrainPackageObject(data?.narrativeBrainPackage) ? data.narrativeBrainPackage : null;
  const hasNarrativeBrainPackage = !!narrativeBrainPackage && !!data?.narrativeBrainPackageConnected;
  const sourceStatusLabel = hasNarrativeBrainPackage
    ? "Получен brain package из narrative.brain_package_out"
    : "Подключите brain_package_out из ноды СЦЕНАРИЙ";

  return (<>
    {["brain_package","audio","text","ref_character_1","ref_character_2","ref_character_3","ref_animal","ref_group","ref_location","ref_style","ref_props"].map((h, i) => (
      <Handle key={h} type="target" position={Position.Left} id={h} className="clipSB_handle" style={handleStyle(h === "ref_props" ? "ref_items" : h === "brain_package" ? "brain_package_out" : h, { top: 28 + i * 18 })} />
    ))}
    <Handle type="source" position={Position.Right} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <NodeShell title="МОЗГ" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧠</span>} className={`clipSB_nodeComfyBrain ${brainStateClass} ${plannerModeClass}`.trim()}>
      <div className="clipSB_comfyBrainPanel">
        <section className="clipSB_comfyBrainSection clipSB_comfyBrainInputStage">
          <div className="clipSB_comfyBrainHeadingRow">
            <div>
              <div className="clipSB_brainLabel">BRAIN INPUT</div>
              <div className="clipSB_comfyBrainSubhead">COMFY BRAIN / planning entry point</div>
            </div>
            <span className={`clipSB_comfyBrainInputBadge ${hasNarrativeBrainPackage ? "isReady" : ""}`.trim()}>
              {hasNarrativeBrainPackage ? "Пакет готов" : "Ожидается пакет"}
            </span>
          </div>
          <div className={`clipSB_comfyBrainSourceStatus ${hasNarrativeBrainPackage ? "isConnected" : ""}`.trim()}>
            <div className="clipSB_comfyBrainSourceStatusTitle">{sourceStatusLabel}</div>
            <div className="clipSB_comfyBrainSourceStatusHint">
              {hasNarrativeBrainPackage
                ? "Пакет уже пришёл в production brain node и может быть использован как следующая planning stage основа."
                : "Нода принимает строго типизированный вход narrative.brain_package_out и показывает подготовленную основу для storyboard / planner."}
            </div>
          </div>
          {hasNarrativeBrainPackage ? (
            <BrainPackageView
              brainPackage={narrativeBrainPackage}
              variant="production"
              footer={(
                <div className="clipSB_comfyBrainPlanningReady">
                  <span className="clipSB_comfyBrainPlanningDot" aria-hidden>●</span>
                  Готово к planning stage: source status подтверждён, narrative package структурирован и доступен без переподключения.
                </div>
              )}
            />
          ) : (
            <div className="clipSB_comfyBrainEmptyState">Подключите <code>brain_package_out</code> из ноды <b>СЦЕНАРИЙ</b>, чтобы увидеть подготовленную основу для planner / storyboard.</div>
          )}
        </section>

        <section className="clipSB_comfyBrainSection">
          <div className="clipSB_brainLabel">PLANNER</div>
          <div className="clipSB_comfyPlannerSwitch" role="tablist" aria-label="Planner mode switch">
            <button type="button" className={`clipSB_comfyPlannerSwitchBtn ${plannerMode === "legacy" ? "isActive" : ""}`.trim()} onClick={() => data?.onPlannerMode?.(id, "legacy")}>Current</button>
            <button type="button" className={`clipSB_comfyPlannerSwitchBtn ${plannerMode === "gemini_only" ? "isActive" : ""}`.trim()} onClick={() => data?.onPlannerMode?.(id, "gemini_only")}>Gemini</button>
          </div>
          <div className={plannerBadgeClass}>{plannerBadgeLabel}</div>
        </section>

        <section className="clipSB_comfyBrainSection clipSB_comfyBrainSectionMode">
          <div className="clipSB_brainLabel">MODE</div>
          <select className="clipSB_select clipSB_comfyModeSelect" value={visibleMode} onChange={(e) => data?.onMode?.(id, e.target.value)}>
            {!["clip", "kino"].includes(String(mode || "").toLowerCase()) ? <option value={mode} hidden>{String(mode || "clip")}</option> : null}
            <option value="clip">clip</option>
            <option value="kino">kino</option>
          </select>
        </section>

        <section className="clipSB_comfyBrainSection">
          <div className="clipSB_brainLabel">OUTPUT</div>
          <select className="clipSB_select" value={visibleOutput} onChange={(e) => data?.onOutput?.(id, e.target.value)}>
            {output !== "comfy image" ? <option value={output} hidden>{String(output || "comfy image")}</option> : null}
            <option value="comfy image">comfy image</option>
          </select>
        </section>

        <section className="clipSB_comfyBrainSection">
          <div className="clipSB_brainLabel">GENRE</div>
          <select className="clipSB_select" value={genre} onChange={(e) => data?.onGenre?.(id, e.target.value)}>
            {!genre ? <option value="">Select genre</option> : null}
            {COMFY_BRAIN_GENRE_OPTIONS.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </section>

        <section className="clipSB_comfyBrainSection">
          <div className="clipSB_brainLabel">FORMAT</div>
          <select className="clipSB_select" value={format} onChange={(e) => data?.onFormat?.(id, e.target.value)}>
            {COMFY_BRAIN_FORMAT_OPTIONS.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </section>

        <div className="clipSB_comfyBrainActions">
          <button className="clipSB_btn clipSB_comfyBrainParseBtn" onClick={() => data?.onParse?.(id)} disabled={isParsing}>{parseButtonLabel}</button>
        </div>
      </div>
    </NodeShell>
  </>);
}
