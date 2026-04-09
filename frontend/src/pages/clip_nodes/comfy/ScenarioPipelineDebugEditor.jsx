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
  const phraseUnits = Array.isArray(audioMap?.phrase_units) ? audioMap.phrase_units : [];
  const sceneCandidateWindows = Array.isArray(audioMap?.scene_candidate_windows) ? audioMap.scene_candidate_windows : [];
  const transcriptAlignment = audioMap?.transcript_alignment || {};
  const analysisMode = String(audioMap?.analysis_mode || allDiagnostics?.audio_map_analysis_mode || "—");
  const phraseMode = String(audioMap?.analysis_mode || allDiagnostics?.audio_map_phrase_mode || "—");
  const transcriptAvailable = Boolean(
    audioMap?.transcript_available ?? allDiagnostics?.transcript_available ?? false
  );
  const wordTimestampCount = Number(
    allDiagnostics?.word_timestamp_count ?? (Array.isArray(transcriptAlignment?.words) ? transcriptAlignment.words.length : 0)
  );
  const phraseUnitCount = Number(allDiagnostics?.phrase_unit_count ?? phraseUnits.length);
  const sceneCandidateCount = Number(allDiagnostics?.scene_candidate_count ?? sceneCandidateWindows.length);
  const alignmentSource = String(
    audioMap?.audio_map_alignment_source || allDiagnostics?.audio_map_alignment_source || transcriptAlignment?.source || "—"
  );
  const alignmentBackend = String(allDiagnostics?.audio_map_alignment_backend || "—");
  const alignmentAttempted = Boolean(allDiagnostics?.audio_map_alignment_attempted ?? false);
  const alignmentUnavailableReason = String(allDiagnostics?.audio_map_alignment_unavailable_reason || "—");

  const rolePlanSceneRoles = Array.isArray(rolePlan?.scene_roles) ? rolePlan.scene_roles : [];
  const worldContinuity = rolePlan?.world_continuity || null;
  const presenceModeDistribution = rolePlanSceneRoles.reduce((acc, row) => {
    const key = String(row?.scene_presence_mode || "unspecified").trim() || "unspecified";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const performanceFocusDistribution = rolePlanSceneRoles.reduce((acc, row) => {
    const key = row?.performance_focus ? "true" : "false";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
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
        <div className="clipSB_storyboardKv"><span>analysis_mode</span><strong>{analysisMode}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_map_phrase_mode</span><strong>{phraseMode}</strong></div>
        <div className="clipSB_storyboardKv"><span>transcript_available</span><strong>{String(transcriptAvailable)}</strong></div>
        <div className="clipSB_storyboardKv"><span>word_timestamp_count</span><strong>{wordTimestampCount}</strong></div>
        <div className="clipSB_storyboardKv"><span>phrase_unit_count</span><strong>{phraseUnitCount}</strong></div>
        <div className="clipSB_storyboardKv"><span>scene_candidate_count</span><strong>{sceneCandidateCount}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_map_alignment_source</span><strong>{alignmentSource}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_map_alignment_backend</span><strong>{alignmentBackend}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_map_alignment_attempted</span><strong>{String(alignmentAttempted)}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_map_alignment_unavailable_reason</span><strong>{alignmentUnavailableReason}</strong></div>
        <pre className="clipSB_pre">{toJson({
          sections: audioSections,
          phrase_endpoints_sec: phraseEndpoints,
          no_split_ranges: noSplitRanges,
          candidate_cut_points_sec: cutPoints,
        })}</pre>
        <div className="clipSB_storyboardKv"><span>phrase_units</span><strong>{phraseUnits.length}</strong></div>
        <pre className="clipSB_pre">{toJson(phraseUnits)}</pre>
        <div className="clipSB_storyboardKv"><span>scene_candidate_windows</span><strong>{sceneCandidateWindows.length}</strong></div>
        <pre className="clipSB_pre">{toJson(sceneCandidateWindows)}</pre>
        {transcriptAlignment?.source ? (
          <>
            <div className="clipSB_storyboardKv"><span>transcript_alignment.source</span><strong>{String(transcriptAlignment.source)}</strong></div>
            <pre className="clipSB_pre">{toJson(transcriptAlignment)}</pre>
          </>
        ) : null}
        <pre className="clipSB_pre">{toJson(audioMap)}</pre>
      </div>
    ),
    role_plan: Object.keys(rolePlan || {}).length ? (
      <div>
        <div className="clipSB_storyboardKv"><span>global_roles</span><strong>{Object.keys(rolePlan?.global_roles || {}).length ? "present" : "—"}</strong></div>
        <div className="clipSB_storyboardKv"><span>world_continuity</span><strong>{worldContinuity ? "present" : "empty"}</strong></div>
        <div className="clipSB_storyboardKv"><span>scene_roles</span><strong>{rolePlanSceneRoles.length}</strong></div>
        <div className="clipSB_storyboardKv"><span>presence_mode_distribution</span><strong>{Object.keys(presenceModeDistribution).length ? toJson(presenceModeDistribution) : "{}"}</strong></div>
        <div className="clipSB_storyboardKv"><span>performance_focus_distribution</span><strong>{Object.keys(performanceFocusDistribution).length ? toJson(performanceFocusDistribution) : "{}"}</strong></div>
        <div className="clipSB_storyboardKv"><span>role_arc_summary</span><strong>{String(rolePlan?.role_arc_summary || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>continuity_notes</span><strong>{Array.isArray(rolePlan?.continuity_notes) ? rolePlan.continuity_notes.length : 0}</strong></div>
        <pre className="clipSB_pre">{toJson({
          global_roles: rolePlan?.global_roles || {},
          world_continuity: worldContinuity,
          scene_presence_mode_distribution: presenceModeDistribution,
          performance_focus_distribution: performanceFocusDistribution,
          scene_roles: rolePlanSceneRoles,
          role_arc_summary: rolePlan?.role_arc_summary || "",
          continuity_notes: rolePlan?.continuity_notes || [],
        })}</pre>
      </div>
    ) : (
      <div className="clipSB_storyboardKv"><span>ROLE PLAN</span><strong>role_plan empty</strong></div>
    ),
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
