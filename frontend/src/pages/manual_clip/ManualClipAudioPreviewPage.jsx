import React, { useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  getManualClipBoardMaterialStats,
  getManualProjectOwnerId,
  hasMeaningfulManualProject,
  readActiveManualClipBoardProject,
  readManualClipBoardOpenState,
  readManualClipBoardProjectForNode,
  writeManualClipBoardOpenState,
} from "../clip_nodes/manualProjectBackup.js";
import "./ManualClipAudioPreviewPage.css";

function projectIdOf(project = {}) {
  return String(project?.project_id || project?.projectId || "").trim();
}

function inputSignatureOf(project = {}) {
  return String(project?.input_signature || project?.inputSignature || "").trim();
}

function audioSignatureOf(project = {}) {
  return String(project?.audio_signature || project?.audioSignature || "").trim();
}

function audioInfoOf(project = {}) {
  return {
    url: String(project?.audio?.url || project?.audio_url || project?.audioUrl || "").trim(),
    name: String(project?.audio?.name || project?.audio?.filename || project?.audio_name || "").trim(),
    duration_sec: Number(project?.audio?.duration_sec || project?.audio_duration_sec || 0) || 0,
  };
}

function matchesForcedIdentity(project = {}, { forceProjectId = "", forceInputSignature = "", forceAudioSignature = "", sourceNodeId = "" } = {}) {
  if (!hasMeaningfulManualProject(project)) return false;
  const projectOwner = getManualProjectOwnerId(project);
  if (sourceNodeId && projectOwner && projectOwner !== sourceNodeId) return false;
  const candidateProjectId = projectIdOf(project);
  const candidateInputSignature = inputSignatureOf(project);
  const candidateAudioSignature = audioSignatureOf(project);
  if (forceProjectId && candidateProjectId !== forceProjectId) return false;
  if (forceInputSignature && candidateInputSignature && candidateInputSignature !== forceInputSignature) return false;
  if (forceAudioSignature && candidateAudioSignature && candidateAudioSignature !== forceAudioSignature) return false;
  return true;
}

function readManualAudioPreviewProject(locationState = {}) {
  const openState = readManualClipBoardOpenState() || {};
  const navigationProject = locationState?.navigationProject || locationState?.director_board || locationState?.project || null;
  const sourceNodeId = String(locationState?.sourceNodeId || locationState?.ownerNodeId || openState?.sourceNodeId || getManualProjectOwnerId(navigationProject) || "").trim();
  const forceProjectId = String(locationState?.forceProjectId || locationState?.manualBoardForceProjectId || openState?.forceProjectId || openState?.project_id || projectIdOf(navigationProject) || "").trim();
  const forceInputSignature = String(locationState?.forceInputSignature || locationState?.manualBoardForceInputSignature || openState?.forceInputSignature || openState?.input_signature || inputSignatureOf(navigationProject) || "").trim();
  const forceAudioSignature = String(locationState?.forceAudioSignature || locationState?.manualBoardForceAudioSignature || openState?.forceAudioSignature || openState?.audio_signature || audioSignatureOf(navigationProject) || "").trim();
  const forcedIdentity = { forceProjectId, forceInputSignature, forceAudioSignature, sourceNodeId };
  const forcedCandidates = [
    { source: "navigationProject", project: navigationProject },
    { source: "node-scoped", project: sourceNodeId ? readManualClipBoardProjectForNode(sourceNodeId) : null },
  ];

  if (forceProjectId) {
    const picked = forcedCandidates.find(({ project }) => matchesForcedIdentity(project, forcedIdentity));
    console.info("[MANUAL AUDIO PREVIEW PICK]", {
      sourceNodeId,
      ownerNodeId: sourceNodeId,
      forceProjectId,
      forceInputSignature,
      forceAudioSignature,
      picked: picked?.source || "none_forced_identity",
      explicitNewProject: true,
      audio: audioInfoOf(picked?.project),
      activeFallbackSuppressed: true,
      candidates: forcedCandidates.map(({ source, project }) => ({
        source,
        ownerNodeId: getManualProjectOwnerId(project),
        project_id: projectIdOf(project),
        input_signature: inputSignatureOf(project),
        audio_signature: audioSignatureOf(project),
        audio: audioInfoOf(project),
        stats: getManualClipBoardMaterialStats(project),
      })),
    });
    return picked?.project || null;
  }

  const candidates = [
    ...forcedCandidates,
    { source: "active", project: readActiveManualClipBoardProject() },
  ];
  const picked = candidates.find(({ project }) => {
    if (!hasMeaningfulManualProject(project)) return false;
    return !sourceNodeId || getManualProjectOwnerId(project) === sourceNodeId;
  });
  console.info("[MANUAL AUDIO PREVIEW PICK]", {
    sourceNodeId,
    ownerNodeId: sourceNodeId,
    picked: picked?.source || "none",
    explicitNewProject: false,
    audio: audioInfoOf(picked?.project),
  });
  return picked?.project || null;
}

export default function ManualClipAudioPreviewPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const project = useMemo(() => readManualAudioPreviewProject(location.state || {}), [location.state]);
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const audio = audioInfoOf(project);
  const forceProjectId = projectIdOf(project);
  const forceInputSignature = inputSignatureOf(project);
  const forceAudioSignature = audioSignatureOf(project);

  const onBackToDirectorBoard = () => {
    const sourceNodeId = String(project?.sourceNodeId || project?.nodeId || "").trim();
    writeManualClipBoardOpenState({
      isOpen: true,
      sourceNodeId,
      ownerNodeId: sourceNodeId,
      selectedSceneId: String(project?.selectedSceneId || project?.scenes?.[0]?.scene_id || "").trim(),
      project_id: forceProjectId,
      input_signature: forceInputSignature,
      audio_signature: forceAudioSignature,
      manualBoardExplicitNewProject: true,
      forceProjectId,
      forceInputSignature,
      forceAudioSignature,
      routePath: "/studio/storyboard",
      updatedAt: Date.now(),
    });
    console.info("[MANUAL AUDIO PREVIEW RETURN]", {
      sourceNodeId,
      ownerNodeId: sourceNodeId,
      forceProjectId,
      forceInputSignature,
      forceAudioSignature,
      audio,
      explicitNewProject: true,
    });
    navigate("/studio/storyboard", {
      state: {
        openManualDirectorBoard: true,
        manualBoardExplicitNewProject: true,
        manualBoardForceProjectId: forceProjectId,
        manualBoardForceInputSignature: forceInputSignature,
        manualBoardForceAudioSignature: forceAudioSignature,
        forceProjectId,
        forceInputSignature,
        forceAudioSignature,
        sourceNodeId,
        ownerNodeId: sourceNodeId,
        navigationProject: project,
        director_board: project,
        project,
      },
    });
  };

  return <div className="manualAudioPreviewPage">
    <div className="manualAudioPreviewTopbar">
      <button className="clipSB_btn" onClick={onBackToDirectorBoard}>Назад в режиссёрскую доску</button>
    </div>
    <h2>Прослушать сцены</h2>
    {!project ? <div className="manualAudioPreviewEmpty">Текущий проект доски не найден. Вернитесь в Manual Timing и откройте доску заново.</div> : null}
    {project && !audio.url ? <div className="manualAudioPreviewEmpty">В текущей доске нет аудио. Старое аудио не будет использовано.</div> : null}
    {scenes.length === 0 ? <div className="manualAudioPreviewEmpty">Сцены не найдены. Сначала соберите сцены на Manual Clip Board.</div> : <div className="manualAudioPreviewList">
      {scenes.map((scene, idx) => <article key={scene.scene_id || idx} className="manualAudioSceneCard">
        <h3>{scene.scene_id || `seg_${idx + 1}`} / #{idx + 1}</h3>
        <div>route: {scene.route || "ia2v"}</div>
        <div>тайминг: {Number(scene.start_sec || 0).toFixed(2)} – {Number(scene.end_sec || 0).toFixed(2)} c</div>
        <div>длительность: {Number(scene.duration_sec || 0).toFixed(2)} c</div>
        <div>drama_hint: {scene.drama_hint || "—"}</div>
        {scene.audio_slice_url ? <audio controls src={scene.audio_slice_url} /> : (audio.url ? <audio controls src={audio.url} /> : <div className="manualAudioPending">Аудио сцены не нарезано, а аудио проекта отсутствует</div>)}
      </article>)}
    </div>}
  </div>;
}
