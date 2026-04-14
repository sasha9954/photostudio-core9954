import React, { useMemo, useState } from "react";

const STAGE_BUTTONS = [
  { id: "audio_map", label: "AUDIO" },
  { id: "story_core", label: "CORE" },
  { id: "role_plan", label: "ROLES" },
  { id: "scene_plan", label: "SCENES" },
  { id: "scene_prompts", label: "PROMPTS" },
  { id: "finalize", label: "FINAL" },
];

const TABS = [
  { id: "audio_map", label: "AUDIO MAP" },
  { id: "story_core", label: "STORY CORE" },
  { id: "role_plan", label: "ROLE PLAN" },
  { id: "scene_plan", label: "SCENE PLAN" },
  { id: "scene_prompts", label: "PROMPTS" },
  { id: "final", label: "FINAL" },
  { id: "diagnostics", label: "DIAGNOSTICS" },
  { id: "raw", label: "RAW JSON" },
];

const TAB_STAGE_ID = {
  story_core: "story_core",
  audio_map: "audio_map",
  role_plan: "role_plan",
  scene_plan: "scene_plan",
  scene_prompts: "scene_prompts",
  final: "finalize",
};

const FINALIZE_UPSTREAM_STAGES = [
  "story_core",
  "audio_map",
  "role_plan",
  "scene_plan",
  "scene_prompts",
];

function collectFinalizeStaleStages(stageStatuses = {}) {
  const suspiciousReasonPattern = /(stale|invalid|dirty|outdated|rerun|re-run|upstream|changed|not done)/i;
  return FINALIZE_UPSTREAM_STAGES.filter((stageId) => {
    const row = stageStatuses?.[stageId] && typeof stageStatuses[stageId] === "object" ? stageStatuses[stageId] : {};
    const status = String(row?.status || "idle").trim().toLowerCase();
    const reason = String(
      row?.reason
      || row?.statusReason
      || row?.invalidateReason
      || row?.invalidatedReason
      || row?.message
      || ""
    ).trim();
    const errorText = String(row?.error || row?.message || "").trim();
    const hasError = status === "error" || Boolean(errorText);
    const hasInvalidationMarker = Boolean(row?.invalidated || row?.invalid || row?.dirty || row?.stale);
    const reasonLooksSuspicious = suspiciousReasonPattern.test(reason);
    return status !== "done" || hasError || hasInvalidationMarker || reasonLooksSuspicious;
  });
}

function toJson(value) {
  return JSON.stringify(value || {}, null, 2);
}

function resolveDirectorModeDisplay(value = "") {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "clip") return "Клип";
  if (normalized === "story") return "История";
  if (normalized === "ad") return "Реклама";
  return "—";
}

function buildCompactDebugSnapshot({ contextSummary = {}, executedStages = [], directorOutput = {}, storyboardPackage = {}, diagnostics = {} } = {}) {
  const safePkg = storyboardPackage && typeof storyboardPackage === "object" ? storyboardPackage : {};
  const safeDiagnostics = diagnostics && typeof diagnostics === "object" && Object.keys(diagnostics).length
    ? diagnostics
    : (safePkg?.diagnostics && typeof safePkg.diagnostics === "object" ? safePkg.diagnostics : {});
  const stageStatuses = safePkg?.stage_statuses && typeof safePkg.stage_statuses === "object" ? safePkg.stage_statuses : {};
  const refsInventory = safePkg?.refs_inventory && typeof safePkg.refs_inventory === "object" ? safePkg.refs_inventory : {};
  const refsSummary = {
    rolesWithRefs: Object.keys(refsInventory).filter((key) => String(key || "").startsWith("ref_")).length,
    attachedRefRoles: Array.isArray(safeDiagnostics?.story_core_attached_ref_roles) ? safeDiagnostics.story_core_attached_ref_roles : [],
    availableRoles: Array.isArray(safeDiagnostics?.story_core_available_roles_resolved) ? safeDiagnostics.story_core_available_roles_resolved : [],
  };
  return {
    contextSummary: {
      contentType: contextSummary?.contentType || "",
      format: contextSummary?.format || "",
      director_mode: contextSummary?.director_mode || safePkg?.input?.director_mode || "",
    },
    executedStages: Array.isArray(executedStages) ? executedStages : [],
    stageStatuses,
    diagnosticsSummary: {
      story_core_payload_mode: safeDiagnostics?.story_core_payload_mode || "",
      story_core_director_world_lock_summary: safeDiagnostics?.story_core_director_world_lock_summary || "",
      story_core_compact_context_size_estimate: safeDiagnostics?.story_core_compact_context_size_estimate || 0,
    },
    packageStats: {
      hasStoryCore: Boolean(safePkg?.story_core && typeof safePkg.story_core === "object" && Object.keys(safePkg.story_core).length),
      audioWindows: Array.isArray(safePkg?.audio_map?.scene_candidate_windows) ? safePkg.audio_map.scene_candidate_windows.length : 0,
      rolePlanScenes: Array.isArray(safePkg?.role_plan?.scene_roles) ? safePkg.role_plan.scene_roles.length : 0,
      scenePlanScenes: Array.isArray(safePkg?.scene_plan?.scenes) ? safePkg.scene_plan.scenes.length : 0,
      scenePrompts: Array.isArray(safePkg?.scene_prompts?.scenes) ? safePkg.scene_prompts.scenes.length : 0,
      finalScenes: Array.isArray(safePkg?.final_storyboard?.scenes) ? safePkg.final_storyboard.scenes.length : 0,
    },
    shortStoryCoreSummary: {
      story_summary: String(safePkg?.story_core?.story_summary || ""),
      opening_anchor: String(safePkg?.story_core?.opening_anchor || ""),
      ending_callback_rule: String(safePkg?.story_core?.ending_callback_rule || ""),
    },
    compactRefsSummary: refsSummary,
    directorOutputSummary: {
      pipeline: directorOutput?.pipeline || "",
      storyCorePresent: Boolean(directorOutput?.story_core),
    },
  };
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
  onClearScenarioPipeline,
  stageButtonStateById = {},
}) {
  const [busyStage, setBusyStage] = useState("");
  const [activeTab, setActiveTab] = useState("audio_map");
  const [finalizeWarning, setFinalizeWarning] = useState({ open: false, staleStages: [] });
  const [rebuildHint, setRebuildHint] = useState("");

  const chips = useMemo(() => STAGE_BUTTONS.map((stage) => {
    const status = String(stageStatuses?.[stage.id]?.status || "idle").trim().toLowerCase() || "idle";
    const error = String(stageStatuses?.[stage.id]?.error || stageStatuses?.[stage.id]?.message || "").trim();
    const colorByStatus = { idle: "#94a3b8", running: "#0ea5e9", done: "#22c55e", stale: "#f59e0b", error: "#ef4444" };
    return { ...stage, status, error, statusColor: colorByStatus[status] || colorByStatus.idle };
  }), [stageStatuses]);
  const activeStageStatus = String(stageStatuses?.[TAB_STAGE_ID[activeTab]]?.status || "").trim().toLowerCase();

  const executeStageRun = async (stageId, autoRun = false) => {
    if (typeof onRunPipelineStage !== "function") return;
    setBusyStage(stageId || (autoRun ? "auto" : ""));
    try {
      await onRunPipelineStage(nodeId, { stageId, autoRun });
    } finally {
      setBusyStage("");
    }
  };

  const runStage = async (stageId, autoRun = false) => {
    if (stageId === "finalize" && !autoRun) {
      const staleStages = collectFinalizeStaleStages(stageStatuses);
      if (staleStages.length > 0) {
        setFinalizeWarning({ open: true, staleStages });
        return;
      }
    }
    if (finalizeWarning.open) setFinalizeWarning({ open: false, staleStages: [] });
    if (rebuildHint) setRebuildHint("");
    await executeStageRun(stageId, autoRun);
  };

  if (!open) return null;

  const resolvedStoryboardPackage = (
    storyboardPackage && typeof storyboardPackage === "object" && Object.keys(storyboardPackage).length
      ? storyboardPackage
      : {}
  );

  const storyCore = resolvedStoryboardPackage?.story_core || {};
  const audioMap = resolvedStoryboardPackage?.audio_map || {};
  const rolePlan = resolvedStoryboardPackage?.role_plan || {};
  const scenePlan = resolvedStoryboardPackage?.scene_plan || {};
  const prompts = resolvedStoryboardPackage?.scene_prompts || {};
  const promptScenes = Array.isArray(prompts?.scenes) ? prompts.scenes : [];
  const finalStoryboard = resolvedStoryboardPackage?.final_storyboard || {};
  const allDiagnostics = Object.keys(diagnostics || {}).length ? diagnostics : (resolvedStoryboardPackage?.diagnostics || {});
  const directorMode = String(
    resolvedStoryboardPackage?.input?.director_mode
    || contextSummary?.director_mode
    || audioMap?.director_mode
    || storyCore?.story_core_v1?.director_mode
    || "—"
  );
  const audioTruthScope = String(audioMap?.audio_truth_scope || storyCore?.story_core_v1?.audio_truth_scope || "—");
  const storyTruthSource = String(storyCore?.story_core_v1?.story_truth_source || "—");

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
  const scenePlanScenes = Array.isArray(scenePlan?.scenes) ? scenePlan.scenes : [];
  const scenePlanRouteMix = scenePlan?.route_mix_summary || {};
  const scenePlanRouteCounts = scenePlanScenes.reduce((acc, row) => {
    const key = String(row?.route || "unknown").trim() || "unknown";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
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
        <div className="clipSB_storyboardKv"><span>story_core_v1.director_mode</span><strong>{String(storyCore?.story_core_v1?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(storyCore?.story_core_v1?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(storyCore?.story_core_v1?.audio_truth_scope || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>opening_anchor</span><strong>{String(storyCore?.opening_anchor || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>ending_callback_rule</span><strong>{String(storyCore?.ending_callback_rule || "—")}</strong></div>
        <pre className="clipSB_pre">{toJson({
          global_arc: storyCore?.global_arc || "",
          identity_lock: storyCore?.identity_lock || {},
          world_lock: storyCore?.world_lock || {},
          style_lock: storyCore?.style_lock || {},
          scenes_count: Array.isArray(storyCore?.scenes) ? storyCore.scenes.length : 0,
        })}</pre>
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
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(audioMap?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>semantic_source_type</span><strong>{String(audioMap?.semantic_source_type || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(audioMap?.audio_truth_scope || "—")}</strong></div>
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
            <pre className="clipSB_pre">{toJson({
              source: transcriptAlignment?.source,
              words_count: Array.isArray(transcriptAlignment?.words) ? transcriptAlignment.words.length : 0,
            })}</pre>
          </>
        ) : null}
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
    scene_plan: scenePlanScenes.length ? (
      <div>
        <div className="clipSB_storyboardKv"><span>route_mix_summary</span><strong>{toJson(scenePlanRouteMix)}</strong></div>
        <div className="clipSB_storyboardKv"><span>count_per_route</span><strong>{toJson(scenePlanRouteCounts)}</strong></div>
        <div className="clipSB_storyboardKv"><span>scene_count</span><strong>{scenePlanScenes.length}</strong></div>
        <pre className="clipSB_pre">{toJson(scenePlanScenes.map((row) => ({
          scene_id: row?.scene_id,
          route: row?.route,
          route_reason: row?.route_reason,
          emotional_intent: row?.emotional_intent,
          motion_intent: row?.motion_intent,
          watchability_role: row?.watchability_role,
        })))}</pre>
        <pre className="clipSB_pre">{toJson(scenePlan)}</pre>
      </div>
    ) : (
      <div className="clipSB_storyboardKv"><span>SCENE PLAN</span><strong>scene_plan empty</strong></div>
    ),
    scene_prompts: promptScenes.length ? (
      <div>
        <div className="clipSB_storyboardKv"><span>scene_count</span><strong>{promptScenes.length}</strong></div>
        <pre className="clipSB_pre">{toJson(promptScenes.map((row) => ({
          scene_id: row?.scene_id,
          route: row?.route,
          photo_prompt: row?.photo_prompt,
          video_prompt: row?.video_prompt,
          negative_prompt: row?.negative_prompt,
          prompt_notes: row?.prompt_notes || {},
        })))}</pre>
        <pre className="clipSB_pre">{toJson({
          plan_version: prompts?.plan_version,
          mode: prompts?.mode,
          global_prompt_rules: prompts?.global_prompt_rules || [],
        })}</pre>
      </div>
    ) : (
      <div className="clipSB_storyboardKv"><span>SCENE PROMPTS</span><strong>scene_prompts empty</strong></div>
    ),
    final: <pre className="clipSB_pre">{toJson(finalStoryboard?.scenes || [])}</pre>,
    diagnostics: <pre className="clipSB_pre">{toJson(allDiagnostics)}</pre>,
    raw: <pre className="clipSB_pre">{toJson(buildCompactDebugSnapshot({
      contextSummary,
      executedStages,
      directorOutput,
      storyboardPackage: resolvedStoryboardPackage,
      diagnostics: allDiagnostics,
    }))}</pre>,
  };

  return (
    <div className="clipSB_scenarioOverlay" onClick={onClose}>
      <div className="clipSB_scenarioPanel clipSB_scenarioEditorPanel nodrag nopan nowheel" onClick={(event) => event.stopPropagation()}>
        <div className="clipSB_scenarioHeader">
          <div>
            <div className="clipSB_scenarioTitle">Scenario Pipeline Debug</div>
            <div className="clipSB_scenarioMeta">contentType: {contextSummary?.contentType || "—"} • format: {contextSummary?.format || "—"}</div>
            <div className="clipSB_scenarioMeta">
              Selected mode / Режим: {resolveDirectorModeDisplay(directorMode)} • Director mode: {directorMode} • Audio truth: {audioTruthScope} • Story truth: {storyTruthSource}
            </div>
          </div>
          <button className="clipSB_iconBtn" onClick={onClose} type="button">×</button>
        </div>

        <div className="clipSB_scenarioEditorTopTabs">
          <div className="clipSB_scenarioEditorBtnRow clipSB_scenarioEditorStageButtonsRow">
            {STAGE_BUTTONS.map((stage) => (
              <button
                key={stage.id}
                className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorStageBtn"
                type="button"
                disabled={!!busyStage || !stageButtonStateById?.[stage.id]?.enabled}
                title={stageButtonStateById?.[stage.id]?.reason || ""}
                style={{
                  opacity: stageButtonStateById?.[stage.id]?.enabled ? 1 : 0.48,
                  outline: stageButtonStateById?.[stage.id]?.isNext ? "1px solid #22c55e" : "none",
                }}
                onClick={() => runStage(stage.id, false)}
              >
                {stage.label}
              </button>
            ))}
            <button className="clipSB_btn clipSB_scenarioEditorStageBtn" type="button" disabled={!!busyStage} onClick={() => runStage("", true)}>AUTO</button>
            <button className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorStageBtn" type="button" disabled={!!busyStage} onClick={() => onClearScenarioPipeline?.(nodeId)}>ОЧИСТИТЬ</button>
            <button className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorStageBtn" type="button" onClick={() => setActiveTab("raw")}>JSON</button>
          </div>
          <div className="clipSB_scenarioEditorBtnRow clipSB_scenarioEditorStageStatusRow">
            {chips.map((stage) => (
              <span key={`${stage.id}-status`} className="clipSB_tag clipSB_tagStatus" title={stage.error || `${stage.label}: ${stage.status}`} style={{ borderColor: stage.statusColor, color: stage.statusColor }}>
                {stage.label}:{stage.status}
              </span>
            ))}
          </div>
          {finalizeWarning.open ? (
            <div className="clipSB_scenarioFinalizeNotice">
              <div className="clipSB_scenarioFinalizeNoticeText">
                В пакете могут быть неактуальные стадии. Можно собрать Final из текущего пакета, но результат может не включать последние изменения.
                {finalizeWarning.staleStages.length ? (
                  <span className="clipSB_scenarioFinalizeNoticeStages">
                    Неактуальные стадии: {finalizeWarning.staleStages.join(", ")}
                  </span>
                ) : null}
              </div>
              <div className="clipSB_scenarioFinalizeNoticeActions">
                <button
                  className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorStageBtn"
                  type="button"
                  onClick={() => {
                    setFinalizeWarning({ open: false, staleStages: [] });
                    void executeStageRun("finalize", false);
                  }}
                >
                  Собрать текущий Final
                </button>
                <button
                  className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorStageBtn"
                  type="button"
                  onClick={() => setFinalizeWarning({ open: false, staleStages: [] })}
                >
                  Отмена
                </button>
                <button
                  className="clipSB_btn clipSB_scenarioEditorStageBtn"
                  type="button"
                  onClick={() => {
                    const firstStale = String(finalizeWarning.staleStages?.[0] || "").trim();
                    if (firstStale && TAB_STAGE_ID[firstStale]) setActiveTab(firstStale);
                    setFinalizeWarning({ open: false, staleStages: [] });
                    setRebuildHint("Запустите нужные стадии кнопками сверху, затем снова нажмите FINAL.");
                  }}
                >
                  Обновить стадии сначала
                </button>
              </div>
            </div>
          ) : null}
          {!finalizeWarning.open && rebuildHint ? (
            <div className="clipSB_scenarioFinalizeHint">{rebuildHint}</div>
          ) : null}
          <div className="clipSB_scenarioEditorTabsRow">
            {TABS.map((tab) => (
              <button key={tab.id} type="button" className={`clipSB_scenarioEditorTabBtn ${activeTab === tab.id ? "isActive" : ""}`} onClick={() => setActiveTab(tab.id)}>
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <div className="clipSB_scenarioEditorWork">
          {activeStageStatus === "stale" ? (
            <div className="clipSB_storyboardKv" style={{ marginBottom: 8 }}>
              <span>STAGE STATUS</span>
              <strong>stale / needs rerun</strong>
            </div>
          ) : null}
          {contentByTab[activeTab] || null}
        </div>
      </div>
    </div>
  );
}
