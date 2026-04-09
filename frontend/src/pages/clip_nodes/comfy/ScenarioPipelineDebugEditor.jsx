import React, { useMemo, useState } from "react";

const STAGE_BUTTONS = [
  { id: "story_core", label: "CORE" },
  { id: "audio_map", label: "AUDIO" },
  { id: "role_plan", label: "ROLES" },
  { id: "scene_plan", label: "SCENES" },
  { id: "scene_prompts", label: "PROMPTS" },
  { id: "finalize", label: "FINAL" },
];

const TABS = [
  { id: "story_core", label: "STORY CORE" },
  { id: "audio_map", label: "AUDIO MAP" },
  { id: "role_plan", label: "ROLE PLAN" },
  { id: "scene_plan", label: "SCENE PLAN" },
  { id: "scene_prompts", label: "PROMPTS" },
  { id: "final", label: "FINAL" },
  { id: "diagnostics", label: "DIAGNOSTICS" },
  { id: "raw", label: "RAW JSON" },
];

function toJson(value) {
  return JSON.stringify(value || {}, null, 2);
}

export default function ScenarioPipelineDebugEditor({
  open,
  nodeId,
  onClose,
  contextSummary = {},
  storyboardPackage = {},
  stageStatuses = {},
  directorOutput = {},
  diagnostics = {},
  executedStages = [],
  onRunPipelineStage,
}) {
  const [busyStage, setBusyStage] = useState("");
  const [activeTab, setActiveTab] = useState("story_core");

  const chips = useMemo(() => STAGE_BUTTONS.map((stage) => {
    const status = String(stageStatuses?.[stage.id]?.status || "idle").trim().toLowerCase() || "idle";
    const error = String(stageStatuses?.[stage.id]?.error || stageStatuses?.[stage.id]?.message || "").trim();
    const colorByStatus = { idle: "#94a3b8", running: "#0ea5e9", done: "#22c55e", stale: "#f59e0b", error: "#ef4444" };
    return { ...stage, status, error, statusColor: colorByStatus[status] || colorByStatus.idle };
  }), [stageStatuses]);

  const runStage = async (stageId, autoRun = false) => {
    if (typeof onRunPipelineStage !== "function") return;
    setBusyStage(stageId || (autoRun ? "auto" : ""));
    try {
      await onRunPipelineStage(nodeId, { stageId, autoRun });
    } finally {
      setBusyStage("");
    }
  };

  if (!open) return null;

  const storyCore = storyboardPackage?.story_core || {};
  const audioMap = storyboardPackage?.audio_map || {};
  const rolePlan = storyboardPackage?.role_plan || {};
  const scenePlan = storyboardPackage?.scene_plan || {};
  const prompts = storyboardPackage?.scene_prompts || {};
  const finalStoryboard = storyboardPackage?.final_storyboard || {};
  const allDiagnostics = Object.keys(diagnostics || {}).length ? diagnostics : (storyboardPackage?.diagnostics || {});

  const audioSections = Array.isArray(audioMap?.sections) ? audioMap.sections : [];
  const phraseEndpoints = Array.isArray(audioMap?.phrase_endpoints_sec) ? audioMap.phrase_endpoints_sec : [];
  const noSplitRanges = Array.isArray(audioMap?.no_split_ranges) ? audioMap.no_split_ranges : [];
  const cutPoints = Array.isArray(audioMap?.candidate_cut_points_sec) ? audioMap.candidate_cut_points_sec : [];

  const contentByTab = {
    story_core: (
      <div>
        <div className="clipSB_storyboardKv"><span>story_summary</span><strong>{String(storyCore?.story_summary || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>opening_anchor</span><strong>{String(storyCore?.opening_anchor || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>ending_callback_rule</span><strong>{String(storyCore?.ending_callback_rule || "—")}</strong></div>
        <pre className="clipSB_pre">{toJson(storyCore)}</pre>
      </div>
    ),
    audio_map: (
      <div>
        <div className="clipSB_storyboardKv"><span>duration_sec</span><strong>{String(audioMap?.duration_sec ?? "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>sections</span><strong>{audioSections.length}</strong></div>
        <div className="clipSB_storyboardKv"><span>phrase_endpoints_sec</span><strong>{phraseEndpoints.length}</strong></div>
        <div className="clipSB_storyboardKv"><span>candidate_cut_points_sec</span><strong>{cutPoints.length}</strong></div>
        <div className="clipSB_storyboardKv"><span>no_split_ranges</span><strong>{noSplitRanges.length}</strong></div>
        <pre className="clipSB_pre">{toJson({
          sections: audioSections,
          phrase_endpoints_sec: phraseEndpoints,
          no_split_ranges: noSplitRanges,
          candidate_cut_points_sec: cutPoints,
        })}</pre>
        <pre className="clipSB_pre">{toJson(audioMap)}</pre>
      </div>
    ),
    role_plan: <pre className="clipSB_pre">{toJson(rolePlan)}</pre>,
    scene_plan: <pre className="clipSB_pre">{toJson(scenePlan?.scenes || [])}</pre>,
    scene_prompts: <pre className="clipSB_pre">{toJson(prompts)}</pre>,
    final: <pre className="clipSB_pre">{toJson(finalStoryboard?.scenes || [])}</pre>,
    diagnostics: <pre className="clipSB_pre">{toJson(allDiagnostics)}</pre>,
    raw: <pre className="clipSB_pre">{toJson({ contextSummary, executedStages, directorOutput, storyboardPackage })}</pre>,
  };

  return (
    <div className="clipSB_scenarioOverlay" onClick={onClose}>
      <div className="clipSB_scenarioPanel clipSB_scenarioEditorPanel nodrag nopan nowheel" onClick={(event) => event.stopPropagation()}>
        <div className="clipSB_scenarioHeader">
          <div>
            <div className="clipSB_scenarioTitle">Scenario Pipeline Debug</div>
            <div className="clipSB_scenarioMeta">contentType: {contextSummary?.contentType || "—"} • format: {contextSummary?.format || "—"}</div>
          </div>
          <button className="clipSB_iconBtn" onClick={onClose} type="button">×</button>
        </div>

        <div className="clipSB_scenarioEditorTopTabs">
          <div className="clipSB_scenarioEditorBtnRow clipSB_scenarioEditorStageButtonsRow">
            {STAGE_BUTTONS.map((stage) => (
              <button key={stage.id} className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorStageBtn" type="button" disabled={!!busyStage} onClick={() => runStage(stage.id, false)}>{stage.label}</button>
            ))}
            <button className="clipSB_btn clipSB_scenarioEditorStageBtn" type="button" disabled={!!busyStage} onClick={() => runStage("", true)}>AUTO</button>
            <button className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorStageBtn" type="button" onClick={() => setActiveTab("raw")}>JSON</button>
          </div>
          <div className="clipSB_scenarioEditorBtnRow clipSB_scenarioEditorStageStatusRow">
            {chips.map((stage) => (
              <span key={`${stage.id}-status`} className="clipSB_tag clipSB_tagStatus" title={stage.error || `${stage.label}: ${stage.status}`} style={{ borderColor: stage.statusColor, color: stage.statusColor }}>
                {stage.label}:{stage.status}
              </span>
            ))}
          </div>
          <div className="clipSB_scenarioEditorTabsRow">
            {TABS.map((tab) => (
              <button key={tab.id} type="button" className={`clipSB_scenarioEditorTabBtn ${activeTab === tab.id ? "isActive" : ""}`} onClick={() => setActiveTab(tab.id)}>
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <div className="clipSB_scenarioEditorWork">
          {contentByTab[activeTab] || null}
        </div>
      </div>
    </div>
  );
}
