import React, { useMemo, useState } from "react";

const STAGE_BUTTONS = [
  { id: "audio_map", label: "AUDIO" },
  { id: "story_core", label: "CORE" },
  { id: "role_plan", label: "ROLES" },
  { id: "scene_plan", label: "SCENES" },
  { id: "scene_prompts", label: "PROMPTS" },
  { id: "final_video_prompt", label: "FINAL VIDEO PROMPT" },
  { id: "finalize", label: "FINAL" },
];

const TABS = [
  { id: "audio_map", label: "AUDIO MAP" },
  { id: "story_core", label: "STORY CORE" },
  { id: "role_plan", label: "ROLE PLAN" },
  { id: "scene_plan", label: "SCENE PLAN" },
  { id: "scene_prompts", label: "PROMPTS" },
  { id: "final_video_prompt", label: "FINAL VIDEO PROMPT" },
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
  final_video_prompt: "final_video_prompt",
  final: "finalize",
};

const FINALIZE_UPSTREAM_STAGES = [
  "story_core",
  "audio_map",
  "role_plan",
  "scene_plan",
  "scene_prompts",
  "final_video_prompt",
];

function hasStagePayloadForFinalize(stageId = "", storyboardPackage = {}) {
  const pkg = storyboardPackage && typeof storyboardPackage === "object" && !Array.isArray(storyboardPackage)
    ? storyboardPackage
    : {};
  const normalized = String(stageId || "").trim().toLowerCase();
  if (!normalized) return false;
  if (normalized === "audio_map") {
    const stage = pkg?.audio_map && typeof pkg.audio_map === "object" ? pkg.audio_map : {};
    return Object.keys(stage).length > 0;
  }
  if (normalized === "story_core") {
    const stage = pkg?.story_core && typeof pkg.story_core === "object" ? pkg.story_core : {};
    return Boolean(String(stage?.core_version || "").trim()) && Array.isArray(stage?.narrative_segments) && stage.narrative_segments.length > 0;
  }
  if (normalized === "role_plan") {
    const stage = pkg?.role_plan && typeof pkg.role_plan === "object" ? pkg.role_plan : {};
    const sceneCasting = stage?.scene_casting;
    if (sceneCasting && typeof sceneCasting === "object" && !Array.isArray(sceneCasting) && Object.keys(sceneCasting).length > 0) return true;
    if (Array.isArray(sceneCasting) && sceneCasting.length > 0) return true;
    if (Array.isArray(stage?.roster) && stage.roster.length > 0) return true;
    if (Array.isArray(stage?.roles) && stage.roles.length > 0) return true;
    if (Array.isArray(stage?.cast) && stage.cast.length > 0) return true;
    return false;
  }
  if (normalized === "scene_plan") {
    const stage = pkg?.scene_plan && typeof pkg.scene_plan === "object" ? pkg.scene_plan : {};
    if (Array.isArray(stage?.segments) && stage.segments.length > 0) return true;
    if (Array.isArray(stage?.scenes) && stage.scenes.length > 0) return true;
    if (stage?.storyboard && typeof stage.storyboard === "object" && !Array.isArray(stage.storyboard)) return Object.keys(stage.storyboard).length > 0;
    return false;
  }
  if (normalized === "scene_prompts") {
    const stage = pkg?.scene_prompts && typeof pkg.scene_prompts === "object" ? pkg.scene_prompts : {};
    if (Array.isArray(stage?.segments) && stage.segments.length > 0) return true;
    if (Array.isArray(stage?.scenes) && stage.scenes.length > 0) return true;
    return Boolean(String(stage?.prompts_version || "").trim()) && Object.keys(stage).length > 0;
  }
  if (normalized === "final_video_prompt") {
    const stage = pkg?.final_video_prompt && typeof pkg.final_video_prompt === "object" ? pkg.final_video_prompt : {};
    if (Array.isArray(stage?.segments) && stage.segments.length > 0) return true;
    if (Array.isArray(stage?.scenes) && stage.scenes.length > 0) return true;
    return false;
  }
  return false;
}

function collectFinalizeStaleStages(stageStatuses = {}, storyboardPackage = {}) {
  const suspiciousReasonPattern = /(stale|invalid|dirty|outdated|rerun|re-run|upstream|changed|not done)/i;
  return FINALIZE_UPSTREAM_STAGES.filter((stageId) => {
    const row = stageStatuses?.[stageId] && typeof stageStatuses[stageId] === "object" ? stageStatuses[stageId] : {};
    const status = String(row?.status || "idle").trim().toLowerCase();
    const hasValidPayload = hasStagePayloadForFinalize(stageId, storyboardPackage);
    const rerunMarkerPresent = Boolean(String(row?.updated_at || "").trim()) || Number(row?.run_count || 0) > 0;
    if (status === "done" && hasValidPayload && rerunMarkerPresent) return false;
    if (status === "done" && hasValidPayload) return false;
    const reason = String(
      row?.reason
      || row?.statusReason
      || row?.invalidateReason
      || row?.staleReason
      || row?.stale_reason
      || row?.invalidatedReason
      || row?.message
      || ""
    ).trim();
    const errorText = String(row?.error || row?.message || "").trim();
    const hasError = status === "error" || Boolean(errorText);
    const hasInvalidationMarker = Boolean(row?.invalidated || row?.invalid || row?.dirty || row?.stale);
    const reasonLooksSuspicious = suspiciousReasonPattern.test(reason);
    return status !== "done" || !hasValidPayload || hasError || hasInvalidationMarker || reasonLooksSuspicious;
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

function resolveInheritedModeMetadata({ stageOutput = {}, storyboardPackage = {}, contextSummary = {} } = {}) {
  const safeStage = stageOutput && typeof stageOutput === "object" ? stageOutput : {};
  const safePkg = storyboardPackage && typeof storyboardPackage === "object" ? storyboardPackage : {};
  const storyCoreV1 = safePkg?.story_core?.story_core_v1 && typeof safePkg.story_core.story_core_v1 === "object"
    ? safePkg.story_core.story_core_v1
    : {};
  const baseDirectorMode = String(
    safePkg?.input?.director_mode
    || contextSummary?.director_mode
    || storyCoreV1?.director_mode
    || "—"
  ).trim() || "—";
  const resolvedDirectorMode = String(safeStage?.director_mode || baseDirectorMode || "—").trim() || "—";
  const resolvedStoryTruth = String(
    safeStage?.story_truth_source
    || storyCoreV1?.story_truth_source
    || (resolvedDirectorMode === "clip" ? "note_refs_primary" : "—")
  ).trim() || "—";
  const resolvedAudioTruth = String(
    safeStage?.audio_truth_scope
    || storyCoreV1?.audio_truth_scope
    || (resolvedDirectorMode === "clip" ? "timing_plus_emotion" : "—")
  ).trim() || "—";
  return {
    director_mode: resolvedDirectorMode,
    story_truth_source: resolvedStoryTruth,
    audio_truth_scope: resolvedAudioTruth,
  };
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
  const rolePlanMeta = resolveInheritedModeMetadata({ stageOutput: safePkg?.role_plan, storyboardPackage: safePkg, contextSummary });
  const scenePlanMeta = resolveInheritedModeMetadata({ stageOutput: safePkg?.scene_plan, storyboardPackage: safePkg, contextSummary });
  const scenePromptsMeta = resolveInheritedModeMetadata({ stageOutput: safePkg?.scene_prompts, storyboardPackage: safePkg, contextSummary });
  const finalMeta = resolveInheritedModeMetadata({ stageOutput: safePkg?.final_storyboard, storyboardPackage: safePkg, contextSummary });
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
      role_plan_mode: rolePlanMeta?.director_mode || "",
      scene_plan_mode: scenePlanMeta?.director_mode || "",
      scene_prompts_mode: scenePromptsMeta?.director_mode || "",
      final_storyboard_mode: finalMeta?.director_mode || "",
    },
    packageStats: {
      hasStoryCore: Boolean(safePkg?.story_core && typeof safePkg.story_core === "object" && Object.keys(safePkg.story_core).length),
      audioWindows: Array.isArray(safePkg?.audio_map?.scene_candidate_windows) ? safePkg.audio_map.scene_candidate_windows.length : 0,
      rolePlanScenes: Array.isArray(safePkg?.role_plan?.scene_casting) ? safePkg.role_plan.scene_casting.length : 0,
      rolePlanLegacySceneRoles: Array.isArray(safePkg?.role_plan?.scene_roles) ? safePkg.role_plan.scene_roles.length : 0,
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
      const staleStages = collectFinalizeStaleStages(stageStatuses, storyboardPackage);
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
  const finalVideoPrompt = resolvedStoryboardPackage?.final_video_prompt || {};
  const finalVideoPromptScenes = Array.isArray(finalVideoPrompt?.scenes) ? finalVideoPrompt.scenes : [];
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
  const rolePlanMeta = resolveInheritedModeMetadata({ stageOutput: rolePlan, storyboardPackage: resolvedStoryboardPackage, contextSummary });
  const scenePlanMeta = resolveInheritedModeMetadata({ stageOutput: scenePlan, storyboardPackage: resolvedStoryboardPackage, contextSummary });
  const promptsMeta = resolveInheritedModeMetadata({ stageOutput: prompts, storyboardPackage: resolvedStoryboardPackage, contextSummary });
  const finalVideoPromptMeta = resolveInheritedModeMetadata({ stageOutput: finalVideoPrompt, storyboardPackage: resolvedStoryboardPackage, contextSummary });
  const finalMeta = resolveInheritedModeMetadata({ stageOutput: finalStoryboard, storyboardPackage: resolvedStoryboardPackage, contextSummary });

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

  const rolePlanRoster = Array.isArray(rolePlan?.roster) ? rolePlan.roster : [];
  const rolePlanSceneCasting = Array.isArray(rolePlan?.scene_casting) ? rolePlan.scene_casting : [];
  const rolePlanSceneRoles = Array.isArray(rolePlan?.scene_roles) ? rolePlan.scene_roles : [];
  const rolePlanCompiledContract = rolePlan?.compiled_contract && typeof rolePlan.compiled_contract === "object" ? rolePlan.compiled_contract : null;
  const hasLegacySceneRoles = rolePlanSceneRoles.length > 0;
  const hasLegacyCompiledContract = Boolean(rolePlanCompiledContract && Object.keys(rolePlanCompiledContract).length);
  const hasCanonicalAndLegacyRoles = rolePlanSceneCasting.length > 0 && hasLegacySceneRoles;
  const scenePlanScenes = Array.isArray(scenePlan?.scenes) ? scenePlan.scenes : [];
  const scenePlanRouteMix = scenePlan?.route_mix_summary || {};
  const scenePlanRouteCounts = scenePlanScenes.reduce((acc, row) => {
    const key = String(row?.route || "unknown").trim() || "unknown";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const worldContinuity = rolePlan?.world_continuity || null;
  const presenceModeDistribution = rolePlanSceneCasting.reduce((acc, row) => {
    const key = String(row?.presence_mode || "unspecified").trim() || "unspecified";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const presenceWeightDistribution = rolePlanSceneCasting.reduce((acc, row) => {
    const key = String(row?.presence_weight || "unspecified").trim() || "unspecified";
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
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(rolePlanMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(rolePlanMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(rolePlanMeta?.audio_truth_scope || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>roles_version</span><strong>{String(rolePlan?.roles_version || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>roster_count</span><strong>{rolePlanRoster.length}</strong></div>
        <div className="clipSB_storyboardKv"><span>scene_casting_count</span><strong>{rolePlanSceneCasting.length}</strong></div>
        <div className="clipSB_storyboardKv"><span>presence_mode_distribution</span><strong>{Object.keys(presenceModeDistribution).length ? toJson(presenceModeDistribution) : "{}"}</strong></div>
        <div className="clipSB_storyboardKv"><span>presence_weight_distribution</span><strong>{Object.keys(presenceWeightDistribution).length ? toJson(presenceWeightDistribution) : "{}"}</strong></div>
        <div className="clipSB_storyboardKv"><span>has_legacy_scene_roles</span><strong>{String(hasLegacySceneRoles)}</strong></div>
        <div className="clipSB_storyboardKv"><span>has_legacy_compiled_contract</span><strong>{String(hasLegacyCompiledContract)}</strong></div>
        <div className="clipSB_storyboardKv"><span>legacy scene_roles_count</span><strong>{rolePlanSceneRoles.length}</strong></div>
        <div className="clipSB_storyboardKv"><span>legacy bridge</span><strong>{rolePlan?.legacy_bridge_generated ? "deprecated bridge present" : "none"}</strong></div>
        {hasCanonicalAndLegacyRoles ? (
          <div className="clipSB_storyboardKv"><span>transition_note</span><strong>canonical roles active; legacy bridge present</strong></div>
        ) : null}
        <pre className="clipSB_pre">{toJson({
          roles_version: rolePlan?.roles_version || "",
          roster_count: rolePlanRoster.length,
          scene_casting_count: rolePlanSceneCasting.length,
          has_legacy_scene_roles: hasLegacySceneRoles,
          has_legacy_compiled_contract: hasLegacyCompiledContract,
          transition_note: hasCanonicalAndLegacyRoles ? "canonical roles active; legacy bridge present" : "",
          roster: rolePlanRoster,
          scene_casting: rolePlanSceneCasting,
          world_continuity: worldContinuity,
          presence_mode_distribution: presenceModeDistribution,
          presence_weight_distribution: presenceWeightDistribution,
          legacy_bridge: rolePlan?.legacy_bridge_generated ? {
            legacy_bridge_generated: rolePlan?.legacy_bridge_generated,
            legacy_bridge_source: rolePlan?.legacy_bridge_source,
            deprecated: rolePlan?.deprecated,
            scene_roles: rolePlanSceneRoles,
            compiled_contract: rolePlanCompiledContract,
          } : null,
        })}</pre>
      </div>
    ) : (
      <div>
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(rolePlanMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(rolePlanMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(rolePlanMeta?.audio_truth_scope || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>ROLE PLAN</span><strong>role_plan empty</strong></div>
      </div>
    ),
    scene_plan: scenePlanScenes.length ? (
      <div>
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(scenePlanMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(scenePlanMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(scenePlanMeta?.audio_truth_scope || "—")}</strong></div>
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
      <div>
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(scenePlanMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(scenePlanMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(scenePlanMeta?.audio_truth_scope || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>SCENE PLAN</span><strong>scene_plan empty</strong></div>
      </div>
    ),
    scene_prompts: promptScenes.length ? (
      <div>
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(promptsMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(promptsMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(promptsMeta?.audio_truth_scope || "—")}</strong></div>
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
      <div>
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(promptsMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(promptsMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(promptsMeta?.audio_truth_scope || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>SCENE PROMPTS</span><strong>scene_prompts empty</strong></div>
      </div>
    ),
    final_video_prompt: finalVideoPromptScenes.length ? (
      <div>
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(finalVideoPromptMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(finalVideoPromptMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(finalVideoPromptMeta?.audio_truth_scope || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>scene_count</span><strong>{finalVideoPromptScenes.length}</strong></div>
        <pre className="clipSB_pre">{toJson(finalVideoPromptScenes.map((row) => ({
          scene_id: row?.scene_id,
          route: row?.route,
          video_prompt: row?.video_prompt,
          negative_prompt: row?.negative_prompt,
          transition_action_prompt: row?.transition_action_prompt,
          cinematic_transition_prompt: row?.cinematic_transition_prompt,
        })))}</pre>
        <pre className="clipSB_pre">{toJson(finalVideoPrompt)}</pre>
      </div>
    ) : (
      <div>
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(finalVideoPromptMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(finalVideoPromptMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(finalVideoPromptMeta?.audio_truth_scope || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>FINAL VIDEO PROMPT</span><strong>final_video_prompt empty</strong></div>
      </div>
    ),
    final: (
      <div>
        <div className="clipSB_storyboardKv"><span>director_mode</span><strong>{String(finalMeta?.director_mode || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>story_truth_source</span><strong>{String(finalMeta?.story_truth_source || "—")}</strong></div>
        <div className="clipSB_storyboardKv"><span>audio_truth_scope</span><strong>{String(finalMeta?.audio_truth_scope || "—")}</strong></div>
        {Object.prototype.hasOwnProperty.call(allDiagnostics || {}, "finalize_used_legacy_scene_roles_fallback") ? (
          <div className="clipSB_storyboardKv">
            <span>finalize_used_legacy_scene_roles_fallback</span>
            <strong>{String(allDiagnostics?.finalize_used_legacy_scene_roles_fallback)}</strong>
          </div>
        ) : null}
        {Object.prototype.hasOwnProperty.call(allDiagnostics || {}, "finalize_scene_id_segment_id_mismatch_count") ? (
          <div className="clipSB_storyboardKv">
            <span>finalize_scene_id_segment_id_mismatch_count</span>
            <strong>{String(allDiagnostics?.finalize_scene_id_segment_id_mismatch_count)}</strong>
          </div>
        ) : null}
        {Object.prototype.hasOwnProperty.call(allDiagnostics || {}, "finalize_has_both_scene_casting_and_scene_roles") ? (
          <div className="clipSB_storyboardKv">
            <span>finalize_has_both_scene_casting_and_scene_roles</span>
            <strong>{String(allDiagnostics?.finalize_has_both_scene_casting_and_scene_roles)}</strong>
          </div>
        ) : null}
        <pre className="clipSB_pre">{toJson(finalStoryboard?.scenes || [])}</pre>
      </div>
    ),
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
