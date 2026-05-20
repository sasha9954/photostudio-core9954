import { getAccountScopedStorageKey } from "../manualProjectBackup.js";

export const VIDEO_MATCH_BOARD_ACTIVE_PROJECT_KEY = "VIDEO_MATCH_BOARD_ACTIVE_PROJECT_KEY";
export const VIDEO_MATCH_BOARD_ACTIVE_PROJECT_ID_KEY = "VIDEO_MATCH_BOARD_ACTIVE_PROJECT_ID_KEY";
export const VIDEO_MATCH_BOARD_SCHEMA_V1 = "video_match_board_v1";
export const VIDEO_MATCH_BOARD_SCHEMA_V2 = "video_match_board_v2";
export const VIDEO_MATCH_BOARD_SCHEMA_PHOTOSTUDIO_V2 = "photostudio_video_match_board_v2";

function isBlobUrl(value = "") {
  return String(value || "").trim().startsWith("blob:");
}

export function buildVideoMatchImportSignature(project = {}) {
  const source = project && typeof project === "object" ? project : {};
  const sourceVideoPath = String(
    source?.sourceVideo?.path
    || source?.source_video?.path
    || source?.sourceVideoPath
    || "",
  ).trim();
  const segments = Array.isArray(source.matchSegments) ? source.matchSegments : [];
  const signatureParts = segments.map((segment = {}, index = 0) => {
    const id = String(segment.id || segment.audioSceneId || segment.audio_scene_id || `seg_${index}`).trim();
    const t0 = toFiniteNumber(segment.targetStartSec ?? segment.target_t0, 0).toFixed(3);
    const t1 = toFiniteNumber(segment.targetEndSec ?? segment.target_t1, 0).toFixed(3);
    const selectedCandidateId = String(segment.selectedCandidateId || segment.selected_candidate_id || "").trim();
    return `${id}:${t0}:${t1}:${selectedCandidateId}`;
  });
  return [String(source.schema || "").trim(), sourceVideoPath, ...signatureParts].join("|");
}

function getVideoMatchBoardAccountScopedStorageKey(baseKey = "") {
  return getAccountScopedStorageKey(baseKey);
}

export function getVideoMatchBoardNodeStorageKey(nodeId = "") {
  return getVideoMatchBoardAccountScopedStorageKey(`video_match_board:node:${String(nodeId || "default").trim() || "default"}`);
}

export function getVideoMatchBoardLastGoodStorageKey(nodeId = "") {
  return getVideoMatchBoardAccountScopedStorageKey(`video_match_board:last_good:${String(nodeId || "default").trim() || "default"}`);
}

export function getVideoMatchBoardEmergencyStorageKey(nodeId = "") {
  return getVideoMatchBoardAccountScopedStorageKey(`video_match_board:emergency:${String(nodeId || "default").trim() || "default"}`);
}

export function getVideoMatchBoardStoragePrefix() {
  return getVideoMatchBoardAccountScopedStorageKey("video_match_board:");
}

function getVideoMatchBoardActiveProjectStorageKey() {
  return getVideoMatchBoardAccountScopedStorageKey(VIDEO_MATCH_BOARD_ACTIVE_PROJECT_KEY);
}

function getVideoMatchBoardActiveProjectIdStorageKey() {
  return getVideoMatchBoardAccountScopedStorageKey(VIDEO_MATCH_BOARD_ACTIVE_PROJECT_ID_KEY);
}

export function safeReadVideoMatchJson(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function safeWriteJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (error) {
    console.error("[VIDEO MATCH PROJECT SAVE ERROR]", { key, error });
    return false;
  }
}

function toFiniteNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function toNullableFiniteNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function toBool(value) {
  if (value === true || value === 1) return true;
  if (value === false || value === 0 || value === null || value === undefined || value === "") return false;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "y"].includes(normalized)) return true;
    if (["false", "0", "no", "n", ""].includes(normalized)) return false;
  }
  return false;
}

export function normalizeVideoMatchSourceVideo(input = {}) {
  const source = input && typeof input === "object" ? input : {};
  const sourceVideo = source.sourceVideo && typeof source.sourceVideo === "object"
    ? source.sourceVideo
    : (source.source_video && typeof source.source_video === "object" ? source.source_video : {});
  const path = String(
    sourceVideo.path
    || source.sourceVideoPath
    || source.source_video_path
    || source.source_video?.path
    || "",
  ).trim();
  const filename = String(
    sourceVideo.filename
    || sourceVideo.name
    || source.sourceVideoFilename
    || source.source_video?.filename
    || "",
  ).trim();
  const durationSec = Number(
    sourceVideo.durationSec
    || sourceVideo.duration_sec
    || source.sourceVideoDurationSec
    || source.source_video?.duration_sec
    || 0,
  );
  return {
    ...sourceVideo,
    path,
    filename,
    name: filename,
    durationSec,
    duration_sec: durationSec,
  };
}

export function normalizeVideoMatchTimingContext(raw = {}) {
  const source = raw && typeof raw === "object" ? raw : {};
  const audio = source.audio && typeof source.audio === "object" ? source.audio : {};
  const sourceAudioUrl = String(source.sourceAudioUrl || source.audioUrl || audio.url || "").trim();
  const audioDurationRaw = Number(source.audioDurationSec ?? source.audio_duration_sec ?? audio.duration_sec ?? audio.durationSec ?? 0);
  const timingScenes = Array.isArray(source.timingScenes)
    ? source.timingScenes
    : (Array.isArray(source.scenes) ? source.scenes : []);
  const segments = Array.isArray(source.segments)
    ? source.segments
    : (Array.isArray(source.audioSegments) ? source.audioSegments : []);
  return {
    sourceAudioUrl,
    audioDurationSec: Number.isFinite(audioDurationRaw) && audioDurationRaw > 0 ? Number(audioDurationRaw.toFixed(3)) : 0,
    timingScenes,
    segments,
    podcastEditManifest: source.podcastEditManifest || source.podcast_edit_manifest || null,
    composerEditManifest: source.composerEditManifest || source.composer_edit_manifest || null,
    sourceNodeId: String(source.sourceNodeId || source.nodeId || "").trim(),
    updatedAt: source.updatedAt || source.updated_at || Date.now(),
  };
}

export function buildVideoMatchTimingContextFromManualTimingNodeData(data = {}, nodeId = "") {
  const source = data && typeof data === "object" ? data : {};
  const audio = source.audio && typeof source.audio === "object" ? source.audio : {};
  const directorBoard = source.director_board && typeof source.director_board === "object" ? source.director_board : {};
  return normalizeVideoMatchTimingContext({
    sourceAudioUrl: audio.url || source.sourceAudioUrl || "",
    audioDurationSec: audio.duration_sec ?? audio.durationSec ?? source.audioDurationSec ?? 0,
    timingScenes: Array.isArray(source.scenes) ? source.scenes : [],
    segments: Array.isArray(source.segments) ? source.segments : (Array.isArray(source.markers) ? source.markers : []),
    podcastEditManifest: source.podcastEditManifest || source.podcast_edit_manifest || directorBoard.podcastEditManifest || directorBoard.podcast_edit_manifest || null,
    composerEditManifest: source.composerEditManifest || source.composer_edit_manifest || directorBoard.composerEditManifest || directorBoard.composer_edit_manifest || null,
    sourceNodeId: nodeId,
    updatedAt: source.updatedAt || Date.now(),
  });
}


function hasVideoMatchSourceVideoMetadata(sourceVideo = {}) {
  if (!sourceVideo || typeof sourceVideo !== "object") return false;
  return Boolean(String(sourceVideo.filename || "").trim())
    || Number(sourceVideo.duration_sec || sourceVideo.durationSec || 0) > 0
    || Number(sourceVideo.size || 0) > 0
    || Boolean(String(sourceVideo.type || sourceVideo.mimeType || "").trim());
}

function countVideoMatchCandidates(segments = []) {
  if (!Array.isArray(segments)) return 0;
  return segments.reduce((total, segment) => total + (Array.isArray(segment?.candidates) ? segment.candidates.length : 0), 0);
}

function hasVideoMatchTimingContext(timingContext = {}) {
  if (!timingContext || typeof timingContext !== "object") return false;
  return Boolean(String(timingContext.sourceAudioUrl || timingContext.audioUrl || "").trim())
    || Number(timingContext.audioDurationSec || timingContext.audio_duration_sec || 0) > 0
    || (Array.isArray(timingContext.timingScenes) && timingContext.timingScenes.length > 0)
    || (Array.isArray(timingContext.scenes) && timingContext.scenes.length > 0)
    || (Array.isArray(timingContext.segments) && timingContext.segments.length > 0)
    || Boolean(timingContext.podcastEditManifest || timingContext.composerEditManifest);
}

export function getVideoMatchProjectStats(project = {}) {
  const source = project && typeof project === "object" ? project : {};
  const matchSegments = Array.isArray(source.matchSegments) ? source.matchSegments : [];
  const videoBlocks = Array.isArray(source.videoBlocks) ? source.videoBlocks : [];
  const hasJsonInput = String(source.jsonInput || "").trim().length > 0;
  const matchSegmentsCount = matchSegments.length;
  const videoBlocksCount = videoBlocks.length;
  const candidatesTotal = countVideoMatchCandidates(matchSegments);
  const hasSourceVideoMetadata = hasVideoMatchSourceVideoMetadata(source.sourceVideo);
  const hasSourceVideoUrl = String(source.sourceVideoUrl || "").trim().length > 0;
  const hasTimingContext = hasVideoMatchTimingContext(source.timingContext);
  const materialScore = [
    hasJsonInput ? 20 : 0,
    matchSegmentsCount * 10,
    videoBlocksCount * 8,
    candidatesTotal * 4,
    hasSourceVideoMetadata ? 12 : 0,
    hasSourceVideoUrl ? 3 : 0,
    hasTimingContext ? 6 : 0,
  ].reduce((sum, value) => sum + value, 0);
  return {
    hasJsonInput,
    matchSegmentsCount,
    videoBlocksCount,
    candidatesTotal,
    hasSourceVideoMetadata,
    hasSourceVideoUrl,
    hasTimingContext,
    materialScore,
  };
}

function hasAnyVideoMatchMaterials(stats = {}) {
  return Boolean(stats.hasJsonInput)
    || Number(stats.matchSegmentsCount || 0) > 0
    || Number(stats.videoBlocksCount || 0) > 0
    || Number(stats.candidatesTotal || 0) > 0
    || Boolean(stats.hasSourceVideoMetadata)
    || Boolean(stats.hasTimingContext);
}

export function shouldSkipVideoMatchPersistToProtectMaterials(nextProject = {}, existingProject = {}, options = {}) {
  if (options?.explicitReset || options?.forceReplace || options?.allowMaterialLoss) return false;
  if (!existingProject || typeof existingProject !== "object") return false;
  const nextStats = getVideoMatchProjectStats(nextProject);
  const existingStats = getVideoMatchProjectStats(existingProject);
  if (!hasAnyVideoMatchMaterials(existingStats)) return false;
  const nextSourcePath = String(
    nextProject?.sourceVideo?.path
    || nextProject?.sourceVideoPath
    || nextProject?.source_video?.path
    || "",
  ).trim();
  const existingSourcePath = String(
    existingProject?.sourceVideo?.path
    || existingProject?.sourceVideoPath
    || existingProject?.source_video?.path
    || "",
  ).trim();
  if (nextSourcePath) return false;
  if (String(existingSourcePath).startsWith("blob:") && nextSourcePath && !String(nextSourcePath).startsWith("blob:")) return false;

  const losesMaterials = (existingStats.hasJsonInput && !nextStats.hasJsonInput)
    || Number(nextStats.matchSegmentsCount || 0) < Number(existingStats.matchSegmentsCount || 0)
    || Number(nextStats.videoBlocksCount || 0) < Number(existingStats.videoBlocksCount || 0)
    || Number(nextStats.candidatesTotal || 0) < Number(existingStats.candidatesTotal || 0)
    || (existingStats.hasSourceVideoMetadata && !nextStats.hasSourceVideoMetadata)
    || (existingStats.hasTimingContext && !nextStats.hasTimingContext);
  const materiallyPoorer = Number(nextStats.materialScore || 0) < Number(existingStats.materialScore || 0);
  const onlyBlobUrlWasCleared = existingStats.hasSourceVideoUrl
    && !nextStats.hasSourceVideoUrl
    && String(existingProject?.sourceVideoUrl || "").startsWith("blob:")
    && !losesMaterials;

  if (!onlyBlobUrlWasCleared && (losesMaterials || materiallyPoorer)) {
    console.warn("[VIDEO MATCH PERSIST SKIPPED PROTECT_MATERIALS]", {
      nodeId: nextProject?.nodeId || existingProject?.nodeId || "",
      nextStats,
      existingStats,
      options,
    });
    return true;
  }
  return false;
}

export function getDefaultVideoMatchBoardProject(nodeId = "", extra = {}) {
  const safeNodeId = String(nodeId || "default").trim() || "default";
  const now = Date.now();
  return {
    projectId: `video_match_board_${safeNodeId}`,
    schema: VIDEO_MATCH_BOARD_SCHEMA_V2,
    nodeId: safeNodeId,
    sourceNodeId: safeNodeId,
    sourceVideo: { filename: "", duration_sec: 0 },
    sourceVideoUrl: "",
    timingContext: normalizeVideoMatchTimingContext(extra.timingContext || {}),
    matchSegments: [],
    videoBlocks: [],
    selectedSegmentId: "",
    selectedCandidateId: "",
    selectedBlockId: "",
    jsonInput: "",
    jsonError: "",
    createdAt: now,
    updatedAt: now,
    ...extra,
  };
}

export function normalizeVideoMatchCandidate(candidate = {}, segment = {}, sourceVideoUrl = "", index = 0) {
  const source = candidate && typeof candidate === "object" ? candidate : {};
  const segmentId = String(segment.id || segment.audioSceneId || segment.audio_scene_id || `segment_${String(index + 1).padStart(3, "0")}`).trim();
  const id = String(source.id || source.candidateId || source.candidate_id || source.candidate_id || `${segmentId}_candidate_${String(index + 1).padStart(2, "0")}`).trim();
  const warnings = Array.isArray(source.warnings) ? source.warnings.map((warning) => String(warning || "").trim()).filter(Boolean) : [];
  return {
    id,
    candidateId: id,
    segmentId,
    videoStartSec: toFiniteNumber(source.video_t0 ?? source.videoStartSec ?? source.sourceVideoStartSec ?? source.source_video_start_sec, 0),
    videoEndSec: toFiniteNumber(source.video_t1 ?? source.videoEndSec ?? source.sourceVideoEndSec ?? source.source_video_end_sec, 0),
    sourceVideoStartSec: toFiniteNumber(source.video_t0 ?? source.videoStartSec ?? source.sourceVideoStartSec ?? source.source_video_start_sec, 0),
    sourceVideoEndSec: toFiniteNumber(source.video_t1 ?? source.videoEndSec ?? source.sourceVideoEndSec ?? source.source_video_end_sec, 0),
    fitMode: String(source.fit_mode || source.fitMode || "").trim(),
    confidence: toNullableFiniteNumber(source.confidence),
    matchReason: String(source.match_reason || source.matchReason || source.visual_reason_ru || "").trim(),
    visualType: String(source.visual_type || source.visualType || "").trim(),
    shotType: String(source.shot_type || source.shotType || "").trim(),
    emotion: String(source.emotion || "").trim(),
    action: String(source.action || "").trim(),
    containsFace: toBool(source.contains_face ?? source.containsFace),
    mouthVisible: toBool(source.mouth_visible ?? source.mouthVisible),
    lipSyncCandidate: toBool(source.lip_sync_candidate ?? source.lipSyncCandidate),
    dialoguePresent: toBool(source.dialogue_present ?? source.dialoguePresent),
    motionLevel: String(source.motion_level || source.motionLevel || "").trim(),
    cameraMotion: String(source.camera_motion || source.cameraMotion || "").trim(),
    thumbnail: String(source.thumbnail || "").trim(),
    warnings,
    candidateType: String(source.candidateType || source.candidate_type || "").trim(),
    sourceKind: String(source.sourceKind || source.source_kind || "").trim(),
    overrideVideoPath: String(source.overrideVideoPath || source.override_video_path || "").trim(),
    overrideVideoUrl: String(source.overrideVideoUrl || source.override_video_url || "").trim(),
    overrideDurationSec: toNullableFiniteNumber(source.overrideDurationSec ?? source.override_duration_sec),
    sourceVideoUrl: String(source.sourceVideoUrl || sourceVideoUrl || "").trim(),
  };
}

export function normalizeVideoMatchSegment(segment = {}, index = 0, sourceVideoUrl = "") {
  const source = segment && typeof segment === "object" ? segment : {};
  const audioSceneId = String(source.audio_scene_id || source.audioSceneId || source.id || `segment_${String(index + 1).padStart(3, "0")}`).trim();
  const storySceneId = String(source.story_scene_id ?? source.storySceneId ?? "").trim();
  const id = audioSceneId || `segment_${String(index + 1).padStart(3, "0")}`;
  const baseSegment = { id, audioSceneId, storySceneId };
  const rawCandidates = Array.isArray(source.candidates) ? source.candidates : [];
  const candidates = rawCandidates.map((candidate, candidateIndex) => normalizeVideoMatchCandidate(candidate, baseSegment, sourceVideoUrl, candidateIndex));
  const rawSelectedCandidateId = String(
    source.selected_candidate_id
    ?? source.selectedCandidateId
    ?? source.selected_candidate?.candidate_id
    ?? source.selected_candidate?.id
    ?? source.selectedCandidate?.candidate_id
    ?? source.selectedCandidate?.id
    ?? "",
  ).trim();
  const selectedCandidateId = candidates.some((candidate) => candidate.id === rawSelectedCandidateId)
    ? rawSelectedCandidateId
    : (candidates[0]?.id || rawSelectedCandidateId);
  return {
    id,
    audioSceneId,
    audio_scene_id: audioSceneId,
    storySceneId,
    story_scene_id: storySceneId,
    targetStartSec: toFiniteNumber(source.target_t0 ?? source.targetStartSec, 0),
    targetEndSec: toFiniteNumber(source.target_t1 ?? source.targetEndSec, 0),
    text: String(source.text || "").trim(),
    mood: String(source.mood || "").trim(),
    visualNeed: String(source.visual_need || source.visualNeed || "").trim(),
    selectedCandidateId,
    selected_candidate_id: selectedCandidateId,
    candidates,
  };
}

export function normalizeVideoBlock(match = {}, sourceVideoUrl = "") {
  const id = String(match.id || match.candidateId || `match_${Date.now()}`).trim();
  return {
    id,
    segmentId: String(match.segmentId || match.audioSceneId || match.audio_scene_id || "").trim(),
    candidateId: String(match.candidateId || match.id || "").trim(),
    audioSceneId: String(match.audio_scene_id || match.audioSceneId || match.segmentId || "").trim(),
    storySceneId: String(match.story_scene_id ?? match.storySceneId ?? "").trim(),
    story_scene_id: String(match.story_scene_id ?? match.storySceneId ?? "").trim(),
    targetStartSec: toFiniteNumber(match.target_t0 ?? match.targetStartSec, 0),
    targetEndSec: toFiniteNumber(match.target_t1 ?? match.targetEndSec, 0),
    sourceVideoStartSec: toFiniteNumber(match.video_t0 ?? match.sourceVideoStartSec ?? match.videoStartSec, 0),
    sourceVideoEndSec: toFiniteNumber(match.video_t1 ?? match.sourceVideoEndSec ?? match.videoEndSec, 0),
    sourceVideoUrl: String(match.sourceVideoUrl || sourceVideoUrl || "").trim(),
    matchReason: String(match.match_reason || match.matchReason || "").trim(),
    confidence: toNullableFiniteNumber(match.confidence),
    candidateType: String(match.candidateType || match.candidate_type || "").trim(),
    sourceKind: String(match.sourceKind || match.source_kind || "").trim(),
    overrideVideoPath: String(match.overrideVideoPath || match.override_video_path || "").trim(),
    overrideVideoUrl: String(match.overrideVideoUrl || match.override_video_url || "").trim(),
  };
}

export function buildVideoBlocksFromMatchSegments(matchSegments = [], sourceVideoUrl = "") {
  if (!Array.isArray(matchSegments)) return [];
  return matchSegments
    .map((segment, index) => {
      const normalizedSegment = normalizeVideoMatchSegment(segment, index, sourceVideoUrl);
      const selectedCandidate = normalizedSegment.candidates.find((candidate) => candidate.id === normalizedSegment.selectedCandidateId) || normalizedSegment.candidates[0];
      if (!selectedCandidate) return null;
      const isOverride = selectedCandidate.sourceKind === "override_video"
        || selectedCandidate.overrideVideoPath
        || selectedCandidate.overrideVideoUrl;
      return normalizeVideoBlock({
        id: selectedCandidate.id,
        candidateId: selectedCandidate.id,
        segmentId: normalizedSegment.id,
        audioSceneId: normalizedSegment.audioSceneId,
        storySceneId: normalizedSegment.storySceneId,
        story_scene_id: normalizedSegment.story_scene_id,
        targetStartSec: normalizedSegment.targetStartSec,
        targetEndSec: normalizedSegment.targetEndSec,
        sourceVideoStartSec: isOverride ? 0 : selectedCandidate.sourceVideoStartSec,
        sourceVideoEndSec: isOverride
          ? toFiniteNumber(selectedCandidate.overrideDurationSec ?? selectedCandidate.sourceVideoEndSec, selectedCandidate.sourceVideoEndSec)
          : selectedCandidate.sourceVideoEndSec,
        sourceVideoUrl: selectedCandidate.sourceVideoUrl || sourceVideoUrl,
        matchReason: selectedCandidate.matchReason,
        confidence: selectedCandidate.confidence,
        candidateType: selectedCandidate.candidateType,
        sourceKind: isOverride ? "override_video" : selectedCandidate.sourceKind,
        overrideVideoPath: selectedCandidate.overrideVideoPath,
        overrideVideoUrl: selectedCandidate.overrideVideoUrl,
      }, sourceVideoUrl);
    })
    .filter(Boolean);
}

function normalizeV1MatchAsSegment(match = {}, index = 0, sourceVideoUrl = "") {
  const source = match && typeof match === "object" ? match : {};
  const audioSceneId = String(source.audio_scene_id || source.audioSceneId || source.segmentId || `seg_${String(index + 1).padStart(2, "0")}`).trim();
  const candidateId = String(source.id || source.candidate_id || `${audioSceneId}_candidate_01`).trim();
  return normalizeVideoMatchSegment({
    audio_scene_id: audioSceneId,
    target_t0: source.target_t0 ?? source.targetStartSec ?? 0,
    target_t1: source.target_t1 ?? source.targetEndSec ?? 0,
    selected_candidate_id: candidateId,
    candidates: [{
      ...source,
      id: candidateId,
      video_t0: source.video_t0 ?? source.sourceVideoStartSec ?? source.videoStartSec ?? 0,
      video_t1: source.video_t1 ?? source.sourceVideoEndSec ?? source.videoEndSec ?? 0,
    }],
  }, index, sourceVideoUrl);
}

export function parseVideoMatchBoardJson(jsonText = "", sourceVideoUrl = "") {
  let parsed;
  try {
    parsed = JSON.parse(String(jsonText || ""));
  } catch (error) {
    return { ok: false, error: `Невалидный JSON: ${String(error?.message || error)}` };
  }
  if (!parsed || typeof parsed !== "object") return { ok: false, error: "JSON должен быть объектом." };
  const schemaAlias = parsed.schema === VIDEO_MATCH_BOARD_SCHEMA_PHOTOSTUDIO_V2
    ? VIDEO_MATCH_BOARD_SCHEMA_V2
    : parsed.schema;
  if (![VIDEO_MATCH_BOARD_SCHEMA_V1, VIDEO_MATCH_BOARD_SCHEMA_V2].includes(schemaAlias)) {
    return { ok: false, error: `schema должен быть ${VIDEO_MATCH_BOARD_SCHEMA_V1} или ${VIDEO_MATCH_BOARD_SCHEMA_V2}.` };
  }

  let matchSegments = [];
  if (schemaAlias === VIDEO_MATCH_BOARD_SCHEMA_V1) {
    if (!Array.isArray(parsed.matches)) return { ok: false, error: "matches должен быть массивом." };
    matchSegments = parsed.matches.map((match, index) => normalizeV1MatchAsSegment(match, index, sourceVideoUrl));
  } else {
    if (!Array.isArray(parsed.segments)) return { ok: false, error: "segments должен быть массивом." };
    matchSegments = parsed.segments.map((segment, index) => normalizeVideoMatchSegment(segment, index, sourceVideoUrl));
  }

  const videoBlocks = buildVideoBlocksFromMatchSegments(matchSegments, sourceVideoUrl);
  return {
    ok: true,
    schema: schemaAlias,
    sourceVideo: normalizeVideoMatchSourceVideo(parsed),
    source_video: parsed.source_video && typeof parsed.source_video === "object" ? parsed.source_video : {},
    sourceVideoPath: String(parsed.sourceVideoPath || parsed.source_video_path || "").trim(),
    matchSegments,
    videoBlocks,
    selectedSegmentId: matchSegments[0]?.id || "",
    selectedCandidateId: matchSegments[0]?.selectedCandidateId || "",
    raw: parsed,
  };
}

export function readVideoMatchBoardProjectForNode(nodeId = "") {
  const safeNodeId = String(nodeId || "").trim();
  const nodeProject = safeReadVideoMatchJson(getVideoMatchBoardNodeStorageKey(safeNodeId));
  const activeProject = safeReadVideoMatchJson(getVideoMatchBoardActiveProjectStorageKey());
  const emergencyProject = safeReadVideoMatchJson(getVideoMatchBoardEmergencyStorageKey(safeNodeId));
  const candidates = [
    { key: "node", project: nodeProject },
    { key: "active", project: activeProject && (!safeNodeId || String(activeProject.nodeId || "") === safeNodeId) ? activeProject : null },
    { key: "emergency", project: emergencyProject },
  ].filter((item) => item.project);
  if (!candidates.length) return null;

  const scoreProject = (project = {}) => {
    const importedAt = Number(project?.importedAt || 0);
    const matchSegmentsCount = Array.isArray(project?.matchSegments) ? project.matchSegments.length : 0;
    const videoBlocksCount = Array.isArray(project?.videoBlocks) ? project.videoBlocks.length : 0;
    const hasImportMeta = Boolean(project?.importSignature) && importedAt > 0;
    const hasBlobPreview = isBlobUrl(project?.sourceVideoUrl) || isBlobUrl(project?.audioPreviewUrl);
    const updatedAt = Number(project?.updatedAt || 0);
    const hasMaterial = matchSegmentsCount > 0 || videoBlocksCount > 0;
    return { importedAt, matchSegmentsCount, videoBlocksCount, hasImportMeta, hasBlobPreview, updatedAt, hasMaterial };
  };

  const sorted = [...candidates].sort((a, b) => {
    const A = scoreProject(a.project);
    const B = scoreProject(b.project);
    if (A.hasImportMeta !== B.hasImportMeta) return A.hasImportMeta ? -1 : 1;
    if (A.importedAt !== B.importedAt) return B.importedAt - A.importedAt;
    if (A.hasMaterial !== B.hasMaterial) return A.hasMaterial ? -1 : 1;
    if (A.matchSegmentsCount !== B.matchSegmentsCount) return B.matchSegmentsCount - A.matchSegmentsCount;
    if (A.videoBlocksCount !== B.videoBlocksCount) return B.videoBlocksCount - A.videoBlocksCount;
    return B.updatedAt - A.updatedAt;
  });
  const picked = sorted[0];
  const activeStats = scoreProject(nodeProject || activeProject || {});
  const emergencyStats = scoreProject(emergencyProject || {});
  const pickedPath = String(picked.project?.sourceVideo?.path || picked.project?.source_video?.path || picked.project?.sourceVideoPath || "").trim();
  console.info("[VIDEO MATCH HYDRATE PICK]", {
    picked: picked.key,
    activeImportedAt: activeStats.importedAt,
    emergencyImportedAt: emergencyStats.importedAt,
    activeSegments: activeStats.matchSegmentsCount,
    emergencySegments: emergencyStats.matchSegmentsCount,
    hasBlobPreview: Boolean(picked?.project?.sourceVideoUrl && isBlobUrl(picked.project.sourceVideoUrl)),
    sourceVideoPath: pickedPath,
  });
  return picked.project;
}

export function persistVideoMatchBoardProject(project = {}, options = {}) {
  const safeProject = {
    ...getDefaultVideoMatchBoardProject(project?.nodeId || project?.sourceNodeId || "default"),
    ...(project && typeof project === "object" ? project : {}),
    updatedAt: Date.now(),
  };
  const nodeId = String(safeProject.nodeId || safeProject.sourceNodeId || "default").trim() || "default";
  const existingProject = readVideoMatchBoardProjectForNode(nodeId);
  safeProject.nodeId = nodeId;
  safeProject.sourceNodeId = String(safeProject.sourceNodeId || nodeId);
  safeProject.timingContext = normalizeVideoMatchTimingContext(safeProject.timingContext || {});
  safeProject.matchSegments = Array.isArray(safeProject.matchSegments)
    ? safeProject.matchSegments.map((segment, index) => normalizeVideoMatchSegment(segment, index, safeProject.sourceVideoUrl))
    : [];
  safeProject.videoBlocks = safeProject.matchSegments.length
    ? buildVideoBlocksFromMatchSegments(safeProject.matchSegments, safeProject.sourceVideoUrl)
    : (Array.isArray(safeProject.videoBlocks) ? safeProject.videoBlocks.map((block) => normalizeVideoBlock(block, safeProject.sourceVideoUrl)) : []);
  const selectedBlock = safeProject.videoBlocks.find((block) => block.id === safeProject.selectedBlockId) || safeProject.videoBlocks[0] || null;
  const selectedSegment = safeProject.matchSegments.find((segment) => segment.id === safeProject.selectedSegmentId)
    || safeProject.matchSegments.find((segment) => segment.id === selectedBlock?.segmentId)
    || safeProject.matchSegments[0]
    || null;
  safeProject.selectedSegmentId = String(selectedSegment?.id || selectedBlock?.segmentId || safeProject.selectedSegmentId || "").trim();
  safeProject.selectedCandidateId = String(selectedBlock?.candidateId || selectedBlock?.id || selectedSegment?.selectedCandidateId || safeProject.selectedCandidateId || "").trim();
  safeProject.selectedBlockId = String(selectedBlock?.id || safeProject.selectedBlockId || "").trim();

  const nextStats = getVideoMatchProjectStats(safeProject);
  const existingStats = getVideoMatchProjectStats(existingProject);
  if (shouldSkipVideoMatchPersistToProtectMaterials(safeProject, existingProject, options)) {
    console.info("[VIDEO MATCH PROJECT SAVE RESULT]", {
      nodeId,
      saveOk: false,
      nextStats,
      existingStats,
      savedStats: existingStats,
      reason: "protect_materials",
    });
    return existingProject;
  }

  const writeResults = [
    safeWriteJson(getVideoMatchBoardNodeStorageKey(nodeId), safeProject),
    safeWriteJson(getVideoMatchBoardActiveProjectStorageKey(), safeProject),
  ];
  try { localStorage.setItem(getVideoMatchBoardActiveProjectIdStorageKey(), String(safeProject.projectId || nodeId)); } catch (error) { console.error("[VIDEO MATCH PROJECT SAVE ERROR]", { key: getVideoMatchBoardActiveProjectIdStorageKey(), error }); }
  if (options?.lastGood !== false) writeResults.push(safeWriteJson(getVideoMatchBoardLastGoodStorageKey(nodeId), safeProject));
  if (options?.emergency) writeResults.push(safeWriteJson(getVideoMatchBoardEmergencyStorageKey(nodeId), safeProject));
  const saveOk = writeResults.every(Boolean);
  console.info("[VIDEO MATCH PROJECT SAVE RESULT]", {
    nodeId,
    saveOk,
    nextStats,
    existingStats,
    savedStats: getVideoMatchProjectStats(safeProject),
    reason: saveOk ? "saved" : "write_failed",
  });
  return safeProject;
}

export function clearVideoMatchBoardProjectStorage(nodeId = "") {
  const safeNodeId = String(nodeId || "default").trim() || "default";
  const keys = [
    getVideoMatchBoardNodeStorageKey(safeNodeId),
    getVideoMatchBoardLastGoodStorageKey(safeNodeId),
    getVideoMatchBoardEmergencyStorageKey(safeNodeId),
    getVideoMatchBoardActiveProjectStorageKey(),
    getVideoMatchBoardActiveProjectIdStorageKey(),
  ];
  try {
    const prefix = getVideoMatchBoardStoragePrefix();
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key && key.startsWith(prefix) && !keys.includes(key)) keys.push(key);
    }
  } catch {}
  keys.forEach((key) => {
    try { localStorage.removeItem(key); } catch {}
    try { sessionStorage.removeItem(key); } catch {}
  });
}
