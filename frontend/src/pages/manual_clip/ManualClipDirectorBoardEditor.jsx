import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchJson } from "../../services/api.js";
import { buildStoryPrepTemplateText, STORY_PREP_TEMPLATE_META } from "../clip_nodes/manual/manualClipBoardDomain";
import {
  applyManualBlockStoryboardImport,
  applyManualBlockVideoPromptImport,
  buildManualBlockStoryboardContextJson,
  buildManualBlockVideoPromptContextJson,
} from "./manualBlockStoryboardDomain.js";
import {
  buildManualProjectBackupJson,
  getManualClipBoardMaterialStats,
  hasMeaningfulManualProject,
  pickBestManualClipBoardProject,
  persistManualClipBoardProject,
  readActiveManualClipBoardProject,
  readManualClipBoardProjectForNode,
  readLegacyManualClipBoardProject,
  readLegacyManualTimingProject,
  unwrapManualProjectBackupJson,
} from "../clip_nodes/manualProjectBackup.js";
import "./ManualClipDirectorPage.css";

const ROUTES = ["ia2v", "i2v", "i2v_sound", "i2v_text", "first_last", "first_last_sound"];
const I2V_SOUND_GAIN_DEFAULT_DB = -6;
const I2V_SOUND_GAIN_MIN_DB = -18;
const I2V_SOUND_GAIN_MAX_DB = 10;
const MANUAL_TIMING_STORY_VOICEOVER_MODE = "story_voiceover";
const MANUAL_TIMING_MUSIC_CLIP_MODE = "music_clip";
const MANUAL_TIMING_PODCAST_DIALOGUE_MODE = "podcast_dialogue";

function readManualActiveProject(sourceNodeId = "") {
  const nodeProject = readManualClipBoardProjectForNode(sourceNodeId);
  const activeProject = readActiveManualClipBoardProject();
  return pickBestManualClipBoardProject([nodeProject, activeProject]) || activeProject || nodeProject;
}

function persistManualProject(nextProject = {}, options = {}) {
  if (!hasMeaningfulManualProject(nextProject)) return false;
  return persistManualClipBoardProject(nextProject, options);
}

function dispatchManualDirectorBoardUpdate(sourceNodeId = "", project = {}) {
  const safeSourceNodeId = String(sourceNodeId || project?.sourceNodeId || project?.nodeId || "").trim();
  if (!safeSourceNodeId || typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("manual-director-board:update", {
    detail: { sourceNodeId: safeSourceNodeId, project },
  }));
}
const STATUS_VIDEO_READY = "video_ready";

const FIRST_LAST_ROUTES = new Set(["first_last", "first_last_sound"]);
const GENERATED_SOUND_ROUTES = new Set(["i2v_sound", "first_last_sound"]);

function isFirstLastRoute(route = "") {
  return FIRST_LAST_ROUTES.has(String(route || "").trim());
}

function isGeneratedSoundRoute(route = "") {
  return GENERATED_SOUND_ROUTES.has(String(route || "").trim());
}


function normalizeStoryBlock(block = {}, idx = 0) {
  const id = String(block?.block_id || block?.id || block?.story_block_id || `block_${idx + 1}`).trim();
  const blockGoalRu = String(
    block?.block_goal_ru ||
    block?.blockGoalRu ||
    block?.goal_ru ||
    block?.story_block_goal_ru ||
    ""
  );
  const blockRevealRu = String(
    block?.block_reveal_ru ||
    block?.blockRevealRu ||
    block?.reveal_ru ||
    block?.story_block_reveal_ru ||
    ""
  );
  const blockEmotionRu = String(
    block?.block_emotion_ru ||
    block?.blockEmotionRu ||
    block?.emotion_ru ||
    block?.story_block_emotion_ru ||
    ""
  );

  return {
    ...block,
    block_id: id,
    id: String(block?.id || id),
    title_ru: String(block?.title_ru || block?.title || block?.name || id),
    summary_ru: String(block?.summary_ru || block?.summary || ""),
    color: String(block?.color || block?.story_block_color || "#8aa4ff"),
    block_goal_ru: blockGoalRu,
    block_reveal_ru: blockRevealRu,
    block_emotion_ru: blockEmotionRu,
    goal_ru: blockGoalRu,
    reveal_ru: blockRevealRu,
    emotion_ru: blockEmotionRu,
    scene_ids: Array.isArray(block?.scene_ids || block?.sceneIds)
      ? (block?.scene_ids || block?.sceneIds).map((id) => String(id || "").trim()).filter(Boolean)
      : [],
    start_sec: Number(block?.start_sec ?? block?.startSec ?? 0) || 0,
    end_sec: Number(block?.end_sec ?? block?.endSec ?? 0) || 0,
  };
}

function buildStoryBlockLookup(storyBlocks = []) {
  const lookup = new Map();
  (Array.isArray(storyBlocks) ? storyBlocks : []).forEach((block, idx) => {
    const normalized = normalizeStoryBlock(block, idx);
    if (normalized.block_id) lookup.set(normalized.block_id, normalized);
    if (normalized.id) lookup.set(normalized.id, normalized);
  });
  return lookup;
}

function truncateText(value = "", max = 120) {
  const text = String(value || "").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trim()}…`;
}

function isStoryVoiceoverProject(project = {}) {
  return String(project?.project_mode || project?.projectMode || "") === MANUAL_TIMING_STORY_VOICEOVER_MODE;
}

function normalizeSourcePhraseIds(value = []) {
  return (Array.isArray(value) ? value : [])
    .map((id) => String(id || "").trim())
    .filter(Boolean);
}

function getAudioPhrasesForScene(audioPhrases = [], scene = null) {
  if (!scene) return [];
  const sourceIds = normalizeSourcePhraseIds(scene.source_phrase_ids || scene.sourcePhraseIds);
  const phrases = Array.isArray(audioPhrases) ? audioPhrases : [];
  if (sourceIds.length) {
    const byId = new Map(phrases.map((phrase) => [String(phrase?.phrase_id || phrase?.id || ""), phrase]));
    return sourceIds.map((id) => byId.get(id)).filter(Boolean);
  }
  const sceneStart = Number(scene.start_sec || 0);
  const sceneEnd = Number(scene.end_sec || 0);
  return phrases.filter((phrase) => {
    const start = Number(phrase?.start_sec || 0);
    const end = Number(phrase?.end_sec || 0);
    return end > sceneStart + 0.001 && start < sceneEnd - 0.001;
  });
}

function formatDirectorSec(value = 0) {
  const num = Number(value || 0);
  return Number.isFinite(num) ? num.toFixed(2) : "0.00";
}

function isMeaningSceneVisible(scene = {}) {
  return Boolean(
    scene?.story_block_title_ru
    || scene?.story_block_position_ru
    || scene?.translated_text_ru
    || scene?.scene_goal_ru
    || scene?.prompt_hint_ru
    || scene?.photo_prompt_hint_ru
    || scene?.short_note
  );
}

function resolveI2vTextSceneText(scene = {}) {
  return String(
    scene?.lyrics_text
    || scene?.original_text
    || scene?.translated_text_ru
    || scene?.meaning_hint_ru
    || ""
  ).trim();
}

function resolveManualVideoRoutePayload(scene = {}) {
  const route = String(scene.route || "i2v").trim();

  if (route === "ia2v") {
    return {
      resolvedWorkflowKey: "lip_sync_music",
      video_generation_route: "lip_sync_music",
      renderMode: "lip_sync_music",
      lipSync: true,
      requiresAudioSensitiveVideo: true,
      send_audio_to_generator: true,
      audioSliceUrl: String(scene.audio_slice_url || ""),
      keepGeneratedAudio: false,
      generatedAudioPolicy: "mute_generated_video_audio_use_master_track",
    };
  }

  if (route === "i2v_sound") {
    return {
      resolvedWorkflowKey: "i2v_sound",
      video_generation_route: "i2v_sound",
      renderMode: "i2v_sound",
      lipSync: false,
      requiresAudioSensitiveVideo: false,
      send_audio_to_generator: false,
      audioSliceUrl: "",
      keepGeneratedAudio: true,
      generatedAudioPolicy: "mix_generated_audio_under_master",
      generatedAudioGainDb: Number(scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      soundPrompt: String(scene.sound_prompt || ""),
      sound_prompt: String(scene.sound_prompt || ""),
      negativeAudioPrompt: String(scene.negative_audio_prompt || ""),
      negative_audio_prompt: String(scene.negative_audio_prompt || ""),
    };
  }

  if (route === "i2v_text") {
    const soundPrompt = String(scene.sound_prompt || "").trim();
    return {
      resolvedWorkflowKey: "i2v_sound",
      video_generation_route: "i2v_text",
      renderMode: "i2v_sound",
      lipSync: false,
      requiresAudioSensitiveVideo: false,
      send_audio_to_generator: false,
      audioSliceUrl: "",
      keepGeneratedAudio: true,
      generatedAudioPolicy: "mix_generated_audio_under_master",
      generatedAudioGainDb: Number(scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      generated_speech_required: true,
      soundPrompt,
      sound_prompt: soundPrompt,
      negativeAudioPrompt: String(scene.negative_audio_prompt || ""),
      negative_audio_prompt: String(scene.negative_audio_prompt || ""),
    };
  }

  if (route === "first_last" || route === "first_last_sound") {
    const withSound = route === "first_last_sound";
    return {
      resolvedWorkflowKey: withSound ? "first_last_sound" : "first_last",
      video_generation_route: route,
      renderMode: route,
      ltxMode: withSound ? "f_l_sound" : "f_l",
      transitionType: "continuous",
      imageStrategy: "first_last",
      requiresTwoFrames: true,
      lipSync: false,
      requiresAudioSensitiveVideo: false,
      send_audio_to_generator: false,
      audioSliceUrl: "",
      startImageUrl: String(scene.start_image_url || scene.image_url || ""),
      endImageUrl: String(scene.end_image_url || ""),
      keepGeneratedAudio: withSound,
      generatedAudioPolicy: withSound ? "mix_generated_audio_under_master" : "silent_video_use_master_track",
      generatedAudioGainDb: Number(scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      soundPrompt: String(scene.sound_prompt || ""),
      sound_prompt: String(scene.sound_prompt || ""),
      negativeAudioPrompt: String(scene.negative_audio_prompt || ""),
      negative_audio_prompt: String(scene.negative_audio_prompt || ""),
    };
  }

  return {
    resolvedWorkflowKey: "standard_video",
    video_generation_route: "i2v",
    renderMode: "standard_video",
    lipSync: false,
    requiresAudioSensitiveVideo: false,
    send_audio_to_generator: false,
    audioSliceUrl: "",
    keepGeneratedAudio: false,
    generatedAudioPolicy: "silent_video_use_master_track",
  };
}

async function startManualSceneVideo(payload) {
  return fetchJson("/api/clip/video/start", { method: "POST", timeoutMs: 180000, body: payload });
}

async function getManualSceneVideoStatus(jobId) {
  return fetchJson(`/api/clip/video/status/${encodeURIComponent(jobId)}`, { method: "GET", timeoutMs: 60000 });
}

async function extractManualVideoLastFrame(payload) {
  return fetchJson("/api/clip/video/extract-last-frame", {
    method: "POST",
    timeoutMs: 120000,
    body: payload,
  });
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("file_read_failed"));
    reader.readAsDataURL(file);
  });
}

async function uploadManualSceneImage(file) {
  const dataUrl = await fileToDataUrl(file);
  const out = await fetchJson("/api/assets/fromDataUrl", {
    method: "POST",
    body: { dataUrl },
  });

  const url = String(out?.url || out?.asset_url || out?.public_url || "").trim();
  if (!url) throw new Error("image_upload_failed");

  return url;
}

function resolveVideoStartJobId(out = {}) {
  return String(out.jobId || out.job_id || out.id || "").trim();
}

function resolveManualStatusVideoUrl(out = {}) {
  return String(
    out?.videoUrl ||
    out?.video_url ||
    out?.url ||
    out?.finalVideoUrl ||
    out?.final_video_url ||
    out?.result?.videoUrl ||
    out?.result?.video_url ||
    ""
  ).trim();
}

function resolveManualStatusVideoHasAudio(out = {}) {
  return Boolean(out?.videoHasAudio ?? out?.hasAudio ?? out?.video_has_audio ?? false);
}

function resolveManualSceneStatus(scene = {}) {
  if (scene.video_url) return STATUS_VIDEO_READY;
  if (isFirstLastRoute(scene.route)) {
    const hasStart = Boolean(scene.start_image_url || scene.image_url);
    const hasEnd = Boolean(scene.end_image_url);
    if (scene.video_prompt && hasStart && hasEnd) return "prompt_ready";
    if (hasStart || hasEnd) return "photo_loaded";
    if (scene.audio_slice_url) return "audio_ready";
    return "draft";
  }
  if (scene.video_prompt && scene.image_url) return "prompt_ready";
  if (scene.image_url) return "photo_loaded";
  if (scene.audio_slice_url) return "audio_ready";
  return "draft";
}

function getSceneStatusLabel(scene = {}) {
  const status = scene?.status;
  if (status === "video_queued") return "очередь";
  if (status === "video_running") return "генерация";
  if (status === "video_error") return "ошибка";
  if (status === STATUS_VIDEO_READY) return "готово";
  if (scene?.audio_extracted && !scene?.video_url) return "аудио";
  if (status === "photo_loaded") return "фото";
  if (status === "prompt_ready") return "промт";
  if (status === "audio_ready") return "аудио";
  return status || "draft";
}

function toBool(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const v = value.trim().toLowerCase();
    if (["true", "1", "yes", "y", "on"].includes(v)) return true;
    if (["false", "0", "no", "n", "off", ""].includes(v)) return false;
  }
  return fallback;
}

function normalizeScene(scene = {}, idx = 0, storyBlockLookup = null) {
  const start = Number(scene.start_sec || 0);
  const end = Number(scene.end_sec || start);
  const blockId = String(scene.story_block_id || "").trim();
  const block = blockId && storyBlockLookup?.get ? storyBlockLookup.get(blockId) : null;
  return {
    scene_id: String(scene.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene.index || idx + 1),
    route: ROUTES.includes(scene.route) ? scene.route : "i2v",
    start_sec: start,
    end_sec: end,
    speech_start_sec: Number(scene.speech_start_sec ?? scene.speechStartSec ?? start) || start,
    speech_end_sec: Number(scene.speech_end_sec ?? scene.speechEndSec ?? end) || end,
    duration_sec: Number((Math.max(0, end - start)).toFixed(3)),
    use_sound_suggestion: toBool(scene.use_sound_suggestion),
    contains_vocal_assumption: toBool(scene.contains_vocal_assumption),
    contains_instrumental_assumption: toBool(scene.contains_instrumental_assumption),
    contains_vocal: toBool(scene.contains_vocal, toBool(scene.contains_vocal_assumption)),
    contains_instrumental: toBool(scene.contains_instrumental, toBool(scene.contains_instrumental_assumption)),
    story_time: String(scene.story_time || ""),
    scene_type: String(scene.scene_type || ""),
    drama_hint: String(scene.drama_hint || ""),
    short_note: String(scene.short_note || ""),
    scene_goal_ru: String(scene.scene_goal_ru || ""),
    photo_prompt_hint_ru: String(scene.photo_prompt_hint_ru || ""),
    prompt_hint_ru: String(scene.prompt_hint_ru || scene.photo_prompt_hint_ru || ""),
    user_note_ru: String(scene.user_note_ru || scene.user_notes_ru || ""),
    story_position_ru: String(scene.story_position_ru || scene.story_time || ""),
    story_block_id: blockId,
    story_block_title_ru: String(scene.story_block_title_ru || block?.title_ru || ""),
    story_block_color: String(scene.story_block_color || block?.color || ""),
    story_block_position_ru: String(scene.story_block_position_ru || ""),
    story_block_goal_ru: String(scene.story_block_goal_ru || block?.block_goal_ru || block?.goal_ru || ""),
    story_block_reveal_ru: String(scene.story_block_reveal_ru || block?.block_reveal_ru || block?.reveal_ru || ""),
    story_block_emotion_ru: String(scene.story_block_emotion_ru || block?.block_emotion_ru || block?.emotion_ru || ""),
    original_text: String(scene.original_text || ""),
    translated_text_ru: String(scene.translated_text_ru || ""),
    meaning_hint_ru: String(scene.meaning_hint_ru || ""),
    source_text_en: String(scene.source_text_en || ""),
    adapted_text_en: String(scene.adapted_text_en || ""),
    scene_role_in_block_ru: String(scene.scene_role_in_block_ru || ""),
    block_progress_ru: String(scene.block_progress_ru || ""),
    scene_global_context_ru: String(scene.scene_global_context_ru || ""),
    continuity_anchor_ru: String(scene.continuity_anchor_ru || ""),
    must_match_project_identity_ru: String(scene.must_match_project_identity_ru || ""),
    must_match_block_style_ru: String(scene.must_match_block_style_ru || ""),
    storyboard_frame_role_ru: String(scene.storyboard_frame_role_ru || ""),
    source_image_prompt_en: String(scene.source_image_prompt_en || ""),
    source_image_prompt_ru: String(scene.source_image_prompt_ru || ""),
    source_image_negative_prompt_en: String(scene.source_image_negative_prompt_en || ""),
    i2v_prompt_en: String(scene.i2v_prompt_en || ""),
    i2v_negative_prompt_en: String(scene.i2v_negative_prompt_en || ""),
    composition_ru: String(scene.composition_ru || ""),
    camera_angle_ru: String(scene.camera_angle_ru || ""),
    subject_lock_ru: String(scene.subject_lock_ru || ""),
    background_lock_ru: String(scene.background_lock_ru || ""),
    continuity_from_previous_scene_ru: String(scene.continuity_from_previous_scene_ru || ""),
    must_keep_same_ru: String(scene.must_keep_same_ru || ""),
    allowed_variation_ru: String(scene.allowed_variation_ru || ""),
    source_phrase_ids: normalizeSourcePhraseIds(scene.source_phrase_ids || scene.sourcePhraseIds),
    video_prompt: String(scene.video_prompt || ""),
    negative_prompt: String(scene.negative_prompt || ""),
    sound_prompt: String(scene.sound_prompt || ""),
    negative_audio_prompt: String(scene.negative_audio_prompt || ""),
    audio_mode: String(scene.audio_mode || ""),
    voice_mode: String(scene.voice_mode || ""),
    voice_preset_id: String(scene.voice_preset_id || ""),
    voice_language: String(scene.voice_language || ""),
    voice_role: String(scene.voice_role || ""),
    voice_gender: String(scene.voice_gender || ""),
    speech_text: String(scene.speech_text || ""),
    voice_profile: String(scene.voice_profile || ""),
    delivery_style: String(scene.delivery_style || ""),
    ambient_sound_prompt: String(scene.ambient_sound_prompt || ""),
    sound_mix_note_ru: String(scene.sound_mix_note_ru || ""),
    song_block_id: String(scene.song_block_id || ""),
    song_block_type: String(scene.song_block_type || ""),
    song_block_title_ru: String(scene.song_block_title_ru || ""),
    lyrics_text: String(scene.lyrics_text || ""),
    lip_sync_required: Boolean(scene.lip_sync_required),
    vocal_owner_role: String(scene.vocal_owner_role || ""),
    visual_role_ru: String(scene.visual_role_ru || ""),
    performance_role_ru: String(scene.performance_role_ru || ""),
    speaker_id: String(scene.speaker_id || ""),
    speaker_name: String(scene.speaker_name || ""),
    topic_block_id: String(scene.topic_block_id || ""),
    topic_block_title_ru: String(scene.topic_block_title_ru || ""),
    narrator_text_en: String(scene.narrator_text_en || ""),
    narrator_text_ru: String(scene.narrator_text_ru || ""),
    speaker_text_en: String(scene.speaker_text_en || ""),
    speaker_text_ru: String(scene.speaker_text_ru || ""),
    generated_speech_required: Boolean(scene.generated_speech_required),
    voice_profile_id: String(scene.voice_profile_id || ""),
    narrator_voice_profile_en: String(scene.narrator_voice_profile_en || ""),
    negative_voice_traits: String(scene.negative_voice_traits || ""),
    broll_hint_ru: String(scene.broll_hint_ru || ""),
    image_url: String(scene.image_url || scene.start_image_url || ""),
    start_image_url: String(scene.start_image_url || scene.image_url || ""),
    end_image_url: String(scene.end_image_url || ""),
    image_preview_url: String(scene.image_preview_url || scene.start_image_preview_url || ""),
    start_image_preview_url: String(scene.start_image_preview_url || scene.image_preview_url || ""),
    end_image_preview_url: String(scene.end_image_preview_url || ""),
    image_upload_status: String(scene.image_upload_status || ""),
    image_upload_error: String(scene.image_upload_error || ""),
    video_url: String(scene.video_url || ""),
    audio_slice_url: String(scene.audio_slice_url || ""),
    audio_slice_duration_sec: Number(scene.audio_slice_duration_sec || 0),
    status: String(scene.status || "draft"),
    error: String(scene.error || ""),
    audio_extracted: Boolean(scene.audio_extracted),
    video_job_id: String(scene.video_job_id || ""),
    video_error: String(scene.video_error || ""),
    video_has_audio: Boolean(scene.video_has_audio),
    generated_audio_policy: String(scene.generated_audio_policy || ""),
    generated_audio_gain_db: Number(scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
    keep_generated_audio: Boolean(scene.keep_generated_audio),
    video_request_payload_preview: scene.video_request_payload_preview || null,
  };
}

function buildStoryPositionFallback(scene = {}, idx = 0, total = 0) {
  if (idx === 0) return "начало";
  if (idx === total - 1) return "финал";
  if (scene?.route === "ia2v") return "настоящее / lip-sync";
  return "развитие / визуальная сцена";
}

export default function ManualClipDirectorBoardEditor({
  project: embeddedProject = null,
  sourceNodeId = "",
  onProjectChange,
  onClose,
  embedded = false,
} = {}) {
  const navigate = useNavigate();
  const location = useLocation();
  const sourceNodeIdFromRoute = useMemo(() => (
    embedded ? String(sourceNodeId || "") : new URLSearchParams(location.search).get("sourceNodeId") || ""
  ), [embedded, sourceNodeId, location.search]);
  const videoStartInFlightRef = useRef(new Set());
  const resumedVideoJobsRef = useRef(new Set());
  const quickListenAudioRef = useRef(null);
  const quickListenRafRef = useRef(null);
  const playbackRangeRef = useRef({ startSec: 0, endSec: null });
  const didHydrateRef = useRef(false);
  const didWarnMissingSourceNodeIdRef = useRef(false);
  const projectRef = useRef(null);
  const selectedSceneIdRef = useRef("");
  const [project, setProject] = useState(null);
  const [selectedSceneId, setSelectedSceneId] = useState("");
  const [isUserNoteEditorOpen, setIsUserNoteEditorOpen] = useState(false);
  const [storyPrepTemplateText, setStoryPrepTemplateText] = useState("");
  const [isStoryPrepExpanded, setIsStoryPrepExpanded] = useState(false);
  const [backupStatus, setBackupStatus] = useState("");
  const [blockCopyStatus, setBlockCopyStatus] = useState("");
  const [playbackMode, setPlaybackMode] = useState("");
  const [isAudioPlaying, setIsAudioPlaying] = useState(false);
  const [playbackRange, setPlaybackRange] = useState({ startSec: 0, endSec: null });

  const getProjectOwnerNodeId = (candidateProject = {}) => String(
    sourceNodeIdFromRoute
    || candidateProject?.sourceNodeId
    || candidateProject?.nodeId
    || ""
  ).trim();

  const warnMissingSourceNodeId = () => {
    if (didWarnMissingSourceNodeIdRef.current) return;
    didWarnMissingSourceNodeIdRef.current = true;
    console.warn("[manual director] missing sourceNodeId, node-bound board sync disabled");
  };

  const normalizeDirectorProjectOwner = (candidateProject = {}) => {
    const ownerNodeId = getProjectOwnerNodeId(candidateProject);
    if (!ownerNodeId) return candidateProject || {};
    return {
      ...(candidateProject || {}),
      source: candidateProject?.source || "manual_timing_node",
      ownerNodeType: candidateProject?.ownerNodeType || "manualTiming",
      nodeId: ownerNodeId,
      sourceNodeId: ownerNodeId,
    };
  };

  const persistAndBroadcastDirectorProject = (candidateProject = {}, options = {}) => {
    const ownerNodeId = getProjectOwnerNodeId(candidateProject);
    if (!ownerNodeId) {
      warnMissingSourceNodeId();
      return false;
    }
    const safeProject = normalizeDirectorProjectOwner(candidateProject);
    if (embedded && typeof onProjectChange === "function") {
      onProjectChange(safeProject, options?.reason || safeProject.lastPersistReason || "manual_director_embedded_update");
      return true;
    }
    const persisted = persistManualProject(safeProject, options);
    dispatchManualDirectorBoardUpdate(ownerNodeId, safeProject);
    return persisted;
  };

  useEffect(() => {
    const parsedProject = embedded ? embeddedProject : readManualActiveProject(sourceNodeIdFromRoute);
    if (!hasMeaningfulManualProject(parsedProject)) {
      setProject(null);
      setSelectedSceneId("");
      didHydrateRef.current = true;
      return;
    }
    try {
      const parsed = unwrapManualProjectBackupJson(parsedProject);
      const storyBlocks = Array.isArray(parsed?.story_blocks) ? parsed.story_blocks.map(normalizeStoryBlock) : [];
      const storyBlockLookup = buildStoryBlockLookup(storyBlocks);
      const scenes = Array.isArray(parsed?.scenes) ? parsed.scenes.map((scene, idx) => normalizeScene(scene, idx, storyBlockLookup)) : [];
      const hydratedProject = normalizeDirectorProjectOwner({ ...parsed, story_blocks: storyBlocks, scenes });
      setProject(hydratedProject);
      setSelectedSceneId(String(parsed?.selectedSceneId || scenes[0]?.scene_id || ""));
    } catch {
      setProject(null);
    } finally {
      didHydrateRef.current = true;
    }
  }, [embedded, embeddedProject, sourceNodeIdFromRoute]);

  useEffect(() => {
    projectRef.current = project;
  }, [project]);

  useEffect(() => {
    selectedSceneIdRef.current = selectedSceneId;
  }, [selectedSceneId]);

  const persistProject = (nextProject) => {
    const safeProject = normalizeDirectorProjectOwner({
      ...(nextProject || {}),
      selectedSceneId: String(nextProject?.selectedSceneId || selectedSceneIdRef.current || ""),
      updatedAt: Date.now(),
    });
    projectRef.current = safeProject;
    selectedSceneIdRef.current = safeProject.selectedSceneId;
    setProject(safeProject);
    if (!didHydrateRef.current || !hasMeaningfulManualProject(safeProject)) return;
    persistAndBroadcastDirectorProject(safeProject, { reason: safeProject.lastPersistReason || "manual_director_persist_project" });
  };

  const onForceSaveDirectorBoard = () => {
    const currentProject = projectRef.current || project;
    if (!hasMeaningfulManualProject(currentProject)) {
      setBackupStatus("Нет проекта для сохранения");
      return;
    }
    const safeProject = {
      ...currentProject,
      selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || "",
      updatedAt: Date.now(),
      lastPersistReason: "manual_force_save_button",
    };
    projectRef.current = safeProject;
    setProject(safeProject);
    persistAndBroadcastDirectorProject(safeProject, { reason: "manual_force_save_button", forceReplace: false });
    setBackupStatus("Доска сохранена");
    window.setTimeout(() => setBackupStatus(""), 1800);
  };

  const safePersistCurrentProject = (reason = "manual_director_safe_persist") => {
    const currentProject = projectRef.current || project;
    if (!hasMeaningfulManualProject(currentProject)) return false;

    const safeProject = {
      ...currentProject,
      selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || currentProject.scenes?.[0]?.scene_id || "",
      updatedAt: Date.now(),
      lastPersistReason: reason,
    };

    projectRef.current = safeProject;
    selectedSceneIdRef.current = safeProject.selectedSceneId;
    persistAndBroadcastDirectorProject(safeProject, { reason });
    setProject(safeProject);
    return true;
  };

  useEffect(() => {
    const persistBeforeLeave = () => {
      const currentProject = projectRef.current;
      if (!hasMeaningfulManualProject(currentProject)) return;
      persistAndBroadcastDirectorProject({
        ...currentProject,
        selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || "",
        updatedAt: Date.now(),
        lastPersistReason: "pagehide_or_unmount",
      }, { reason: "pagehide_or_unmount" });
    };

    window.addEventListener("pagehide", persistBeforeLeave);
    window.addEventListener("beforeunload", persistBeforeLeave);

    return () => {
      persistBeforeLeave();
      window.removeEventListener("pagehide", persistBeforeLeave);
      window.removeEventListener("beforeunload", persistBeforeLeave);
    };
  }, []);

  const storyBlocks = Array.isArray(project?.story_blocks) ? project.story_blocks : [];
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const audioPhrases = Array.isArray(project?.audio_phrases) ? project.audio_phrases : [];
  const audio = project?.audio && typeof project.audio === "object" ? project.audio : {};
  const audioUrl = String(audio?.url || project?.audio_url || project?.audioUrl || "").trim();
  const isStoryVoiceover = isStoryVoiceoverProject(project);
  const storyPrepProject = useMemo(() => ({
    ...(project || {}),
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    story_blocks: storyBlocks,
    scenes,
  }), [project, storyBlocks, scenes]);

  useEffect(() => {
    if (!project || !Array.isArray(scenes) || !scenes.length) return;

    scenes.forEach((scene) => {
      const jobId = String(scene?.video_job_id || "").trim();
      const sceneId = String(scene?.scene_id || "").trim();
      const status = String(scene?.status || "").trim();

      if (!sceneId || !jobId) return;
      if (scene?.video_url) return;
      if (!["video_running", "video_queued"].includes(status)) return;

      const key = `${sceneId}:${jobId}`;
      if (resumedVideoJobsRef.current.has(key)) return;

      resumedVideoJobsRef.current.add(key);
      pollManualSceneVideo(sceneId, jobId, 0);
    });
  }, [project?.updatedAt, scenes]);

  useEffect(() => {
    if (!project) return;
    setStoryPrepTemplateText(buildStoryPrepTemplateText(storyPrepProject));
  }, [project, storyPrepProject]);

  const refreshStoryPrepTemplate = () => {
    setStoryPrepTemplateText(buildStoryPrepTemplateText(storyPrepProject));
  };

  const onCopyStoryPrepTemplate = async () => {
    const text = storyPrepTemplateText || buildStoryPrepTemplateText(storyPrepProject);
    await navigator.clipboard?.writeText(text);
  };

  const onDownloadStoryPrepTemplate = () => {
    const text = storyPrepTemplateText || buildStoryPrepTemplateText(storyPrepProject);
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "story_prep_template.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const downloadJsonPayload = (payload, filename = "manual_project_backup.json") => {
    try {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setBackupStatus("Backup проекта скачан");
      window.setTimeout(() => setBackupStatus(""), 1800);
    } catch {
      setBackupStatus("Не удалось скачать backup проекта");
    }
  };

  const onDownloadProjectBackup = () => {
    downloadJsonPayload(buildManualProjectBackupJson({ ...(project || {}), selectedSceneId }, { source: "manual_director_board" }));
  };

  const restoreManualProjectObject = (rawProject, successPrefix = "Backup восстановлен") => {
    const parsed = unwrapManualProjectBackupJson(rawProject);
    const storyBlocks = Array.isArray(parsed?.story_blocks) ? parsed.story_blocks.map(normalizeStoryBlock) : [];
    const storyBlockLookup = buildStoryBlockLookup(storyBlocks);
    const scenes = Array.isArray(parsed?.scenes) ? parsed.scenes.map((scene, idx) => normalizeScene(scene, idx, storyBlockLookup)) : [];
    const nextProject = {
      ...parsed,
      story_blocks: storyBlocks,
      scenes,
      selectedSceneId: String(parsed?.selectedSceneId || scenes[0]?.scene_id || ""),
      updatedAt: Date.now(),
    };
    persistProject(nextProject);
    console.debug("[manual director import video/photo]", {
      stats: getManualClipBoardMaterialStats(nextProject),
      selectedSceneId: nextProject.selectedSceneId,
    });
    setSelectedSceneId(nextProject.selectedSceneId);
    setBackupStatus(`${successPrefix}: сцен ${scenes.length}`);
    window.setTimeout(() => setBackupStatus(""), 2200);
  };

  const onRestoreLegacyManualProject = () => {
    const legacyProject = readLegacyManualClipBoardProject() || readLegacyManualTimingProject();
    if (!hasMeaningfulManualProject(legacyProject)) {
      setBackupStatus("Старый проект не найден");
      return;
    }
    try {
      restoreManualProjectObject(legacyProject, "Старый проект восстановлен");
    } catch (error) {
      setBackupStatus(`Ошибка восстановления: ${error?.message || "неверный формат"}`);
    }
  };

  const onImportProjectBackupFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const blockStoryboardProject = applyManualBlockStoryboardImport(project || {}, parsed);
      if (blockStoryboardProject) {
        persistProject(blockStoryboardProject);
        console.debug("[manual director import video/photo]", {
          stats: getManualClipBoardMaterialStats(blockStoryboardProject),
          selectedSceneId: blockStoryboardProject.selectedSceneId,
        });
        setBackupStatus("Раскадровка блока импортирована");
        window.setTimeout(() => setBackupStatus(""), 2200);
        return;
      }
      const blockVideoPromptProject = applyManualBlockVideoPromptImport(project || {}, parsed);
      if (blockVideoPromptProject) {
        persistProject(blockVideoPromptProject);
        console.debug("[manual director import video/photo]", {
          stats: getManualClipBoardMaterialStats(blockVideoPromptProject),
          selectedSceneId: blockVideoPromptProject.selectedSceneId,
        });
        setBackupStatus("Видео-промты блока импортированы");
        window.setTimeout(() => setBackupStatus(""), 2200);
        return;
      }
      restoreManualProjectObject(parsed, "Backup восстановлен");
    } catch (error) {
      setBackupStatus(`Ошибка JSON: ${error?.message || "неверный формат"}`);
    }
  };

  const selectedScene = useMemo(() => scenes.find((s) => s.scene_id === selectedSceneId) || scenes[0] || null, [scenes, selectedSceneId]);
  const currentBlock = useMemo(() => {
    const blockId = String(selectedScene?.story_block_id || "").trim();
    if (!blockId) return null;
    return storyBlocks.find((block) => String(block?.block_id || "") === blockId || String(block?.id || "") === blockId) || null;
  }, [selectedScene?.story_block_id, storyBlocks]);
  const selectedSceneIndex = useMemo(() => scenes.findIndex((s) => s.scene_id === selectedScene?.scene_id), [scenes, selectedScene]);
  const storyPositionText = selectedScene
    ? (selectedScene.story_position_ru || buildStoryPositionFallback(selectedScene, selectedSceneIndex >= 0 ? selectedSceneIndex : 0, scenes.length))
    : "";
  const dramaturgyText = selectedScene ? (selectedScene.drama_hint || selectedScene.short_note || "—") : "—";
  const sceneGoalText = selectedScene ? (selectedScene.scene_goal_ru || selectedScene.drama_hint || selectedScene.short_note || "—") : "—";
  const promptHintText = selectedScene
    ? (selectedScene.prompt_hint_ru || "Напишите prompt вручную под выбранное изображение.")
    : "Напишите prompt вручную под выбранное изображение.";
  const blockSceneCounts = useMemo(() => {
    const counts = new Map();
    scenes.forEach((scene) => {
      const blockId = String(scene?.story_block_id || "").trim();
      if (!blockId) return;
      counts.set(blockId, (counts.get(blockId) || 0) + 1);
    });
    return counts;
  }, [scenes]);
  const visibleStoryBlocks = useMemo(() => storyBlocks.filter((block, idx) => {
    const blockId = String(block?.block_id || block?.id || `block_${idx + 1}`).trim();
    return !(blockId === "block_unknown" && !blockSceneCounts.get(blockId));
  }), [storyBlocks, blockSceneCounts]);
  const storyBlockNumberById = useMemo(() => {
    const numbers = new Map();
    visibleStoryBlocks.forEach((block, idx) => {
      const blockId = String(block?.block_id || block?.id || `block_${idx + 1}`).trim();
      if (blockId) numbers.set(blockId, idx + 1);
    });
    return numbers;
  }, [visibleStoryBlocks]);
  const selectedBlockSceneIndex = useMemo(() => {
    if (!selectedScene?.story_block_id) return 0;
    return scenes.filter((scene) => scene.story_block_id === selectedScene.story_block_id).findIndex((scene) => scene.scene_id === selectedScene.scene_id) + 1;
  }, [scenes, selectedScene]);
  const selectedBlockSceneCount = selectedScene?.story_block_id ? (blockSceneCounts.get(selectedScene.story_block_id) || 0) : 0;
  const selectedBlockNumber = selectedScene?.story_block_id ? storyBlockNumberById.get(selectedScene.story_block_id) : null;
  const selectedSceneActionText = selectedScene ? (selectedScene.scene_goal_ru || selectedScene.prompt_hint_ru || selectedScene.photo_prompt_hint_ru || "") : "";
  const selectedSceneRuText = selectedScene ? (selectedScene.translated_text_ru || selectedScene.short_note || selectedScene.drama_hint || "") : "";
  const selectedSceneOriginalText = selectedScene ? (selectedScene.original_text || selectedScene.adapted_text_en || selectedScene.source_text_en || "") : "";
  const projectMode = String(project?.project_mode || project?.projectMode || "");
  const isMusicClipProject = projectMode === MANUAL_TIMING_MUSIC_CLIP_MODE;
  const isPodcastDialogueProject = projectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE;
  const selectedSceneMeaningDetails = selectedScene ? [
    ["scene_id", selectedScene.scene_id],
    ...(isMusicClipProject ? [
      ["song_block", selectedScene.song_block_title_ru || selectedScene.song_block_id],
      ["song_block_type", selectedScene.song_block_type],
      ["lyrics_text", selectedScene.lyrics_text],
      ["route", selectedScene.route],
      ["lip_sync_required", selectedScene.lip_sync_required ? "yes" : "no"],
      ["vocal_owner_role", selectedScene.vocal_owner_role],
    ] : []),
    ...(isPodcastDialogueProject ? [
      ["speaker", selectedScene.speaker_name || selectedScene.speaker_id],
      ["topic_block", selectedScene.topic_block_title_ru || selectedScene.topic_block_id],
      ["scene_type", selectedScene.scene_type],
      ["narrator_text_en", selectedScene.narrator_text_en],
      ["narrator_text_ru", selectedScene.narrator_text_ru],
      ["speaker_text_en", selectedScene.speaker_text_en],
      ["speaker_text_ru", selectedScene.speaker_text_ru],
    ] : []),
    ["Original", selectedSceneOriginalText],
    ["Meaning", selectedScene.meaning_hint_ru],
    ["Цель сцены", selectedScene.scene_goal_ru],
    ["Подсказка для фото", selectedScene.photo_prompt_hint_ru],
    ["Подсказка для оживления", selectedScene.prompt_hint_ru],
    ["Цель блока", selectedScene.story_block_goal_ru],
    ["Раскрытие блока", selectedScene.story_block_reveal_ru],
    ["Эмоция блока", selectedScene.story_block_emotion_ru],
    ["Роль сцены в блоке", selectedScene.scene_role_in_block_ru],
    ["Прогресс блока", selectedScene.block_progress_ru],
  ].filter(([, value]) => String(value || "").trim()) : [];
  const selectedSceneAudioPhrases = useMemo(() => getAudioPhrasesForScene(audioPhrases, selectedScene), [audioPhrases, selectedScene]);

  const selectedBlockContextId = selectedScene?.story_block_id || selectedScene?.scene_id || "";

  const flashBlockCopyStatus = (message) => {
    setBlockCopyStatus(message);
    window.setTimeout(() => setBlockCopyStatus(""), 1800);
  };

  const buildCurrentManualBlockProject = () => ({ ...(project || {}), story_blocks: storyBlocks, scenes, selectedSceneId });

  const getQuickListenDurationSec = () => {
    const audioEl = quickListenAudioRef.current;
    const mediaDuration = Number(audioEl?.duration || 0);
    const projectDuration = Number(audio?.duration_sec || project?.audio_duration_sec || 0);
    if (Number.isFinite(mediaDuration) && mediaDuration > 0) return mediaDuration;
    if (Number.isFinite(projectDuration) && projectDuration > 0) return projectDuration;
    return 0;
  };

  const clampQuickListenSec = (value) => {
    const num = Number(value || 0);
    const safeValue = Number.isFinite(num) ? Math.max(0, num) : 0;
    const duration = getQuickListenDurationSec();
    return duration > 0 ? Math.min(duration, safeValue) : safeValue;
  };

  const stopQuickListenRaf = () => {
    if (quickListenRafRef.current) {
      window.cancelAnimationFrame(quickListenRafRef.current);
      quickListenRafRef.current = null;
    }
  };

  const resetQuickListenState = () => {
    stopQuickListenRaf();
    setIsAudioPlaying(false);
    setPlaybackMode("");
    playbackRangeRef.current = { startSec: 0, endSec: null };
    setPlaybackRange({ startSec: 0, endSec: null });
  };

  const stopOrPauseCurrentPlayback = () => {
    const audioEl = quickListenAudioRef.current;
    if (audioEl) {
      try {
        audioEl.pause();
      } catch {}
    }
    resetQuickListenState();
  };

  const stopAtQuickListenRangeEndIfNeeded = () => {
    const audioEl = quickListenAudioRef.current;
    if (!audioEl) return false;
    const endSec = Number(playbackRangeRef.current?.endSec);
    if (!Number.isFinite(endSec)) return false;
    if (Number(audioEl.currentTime || 0) < endSec - 0.012) return false;

    try {
      audioEl.pause();
      audioEl.currentTime = clampQuickListenSec(endSec);
    } catch {}
    resetQuickListenState();
    return true;
  };

  const startQuickListenRaf = () => {
    stopQuickListenRaf();
    const tick = () => {
      const audioEl = quickListenAudioRef.current;
      if (!audioEl || audioEl.paused) {
        setIsAudioPlaying(false);
        quickListenRafRef.current = null;
        return;
      }
      if (stopAtQuickListenRangeEndIfNeeded()) {
        quickListenRafRef.current = null;
        return;
      }
      quickListenRafRef.current = window.requestAnimationFrame(tick);
    };
    quickListenRafRef.current = window.requestAnimationFrame(tick);
  };

  const playQuickListenRange = (mode, startValue = 0, endValue = null) => {
    const audioEl = quickListenAudioRef.current;
    if (!audioEl || !audioUrl) return;

    if (isAudioPlaying && playbackMode === mode) {
      stopOrPauseCurrentPlayback();
      return;
    }

    const startSec = clampQuickListenSec(startValue);
    const rawEndSec = Number(endValue);
    const endSec = Number.isFinite(rawEndSec) ? clampQuickListenSec(rawEndSec) : null;
    if (endSec !== null && endSec <= startSec + 0.02) return;

    stopQuickListenRaf();
    try {
      audioEl.pause();
      audioEl.currentTime = startSec;
    } catch {}

    setPlaybackMode(mode);
    playbackRangeRef.current = { startSec, endSec };
    setPlaybackRange({ startSec, endSec });
    setIsAudioPlaying(false);

    window.setTimeout(() => {
      const activeAudio = quickListenAudioRef.current;
      if (!activeAudio) return;
      try {
        activeAudio.currentTime = startSec;
      } catch {}
      activeAudio.play().then(() => {
        setPlaybackMode(mode);
        playbackRangeRef.current = { startSec, endSec };
        setPlaybackRange({ startSec, endSec });
        setIsAudioPlaying(true);
        startQuickListenRaf();
      }).catch(() => {
        setIsAudioPlaying(false);
      });
    }, 30);
  };

  const playSceneRange = () => {
    if (!selectedScene) return;
    playQuickListenRange("scene", Number(selectedScene.start_sec || 0), Number(selectedScene.end_sec || 0));
  };

  const playBlockRange = () => {
    if (!currentBlock) return;
    playQuickListenRange("block", Number(currentBlock.start_sec || 0), Number(currentBlock.end_sec || 0));
  };

  const playFullAudio = () => {
    playQuickListenRange("full", 0, null);
  };

  const onQuickListenTimeUpdate = () => {
    const audioEl = quickListenAudioRef.current;
    if (!audioEl) return;
    if (audioEl.paused) {
      setIsAudioPlaying(false);
      stopQuickListenRaf();
      return;
    }
    if (!stopAtQuickListenRangeEndIfNeeded()) setIsAudioPlaying(true);
  };

  useEffect(() => () => stopQuickListenRaf(), []);

  useEffect(() => {
    stopOrPauseCurrentPlayback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioUrl]);

  useEffect(() => {
    stopOrPauseCurrentPlayback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSceneId]);

  const onCopyBlockStoryboardJson = async () => {
    try {
      const payload = buildManualBlockStoryboardContextJson(
        buildCurrentManualBlockProject(),
        selectedBlockContextId,
      );
      await navigator.clipboard?.writeText(JSON.stringify(payload, null, 2));
      flashBlockCopyStatus(`JSON фото блока скопирован: ${payload.scenes.length} сцен`);
    } catch (error) {
      flashBlockCopyStatus(`Не удалось скопировать JSON фото: ${error?.message || "ошибка"}`);
    }
  };

  const onCopyBlockVideoPromptJson = async () => {
    try {
      const payload = buildManualBlockVideoPromptContextJson(
        buildCurrentManualBlockProject(),
        selectedBlockContextId,
      );
      await navigator.clipboard?.writeText(JSON.stringify(payload, null, 2));
      flashBlockCopyStatus(`JSON видео блока скопирован: ${payload.scenes.length} сцен`);
    } catch (error) {
      flashBlockCopyStatus(`Не удалось скопировать JSON видео: ${error?.message || "ошибка"}`);
    }
  };


  const assertImportedBlockMatchesSelection = (payload = {}) => {
    const importedBlockId = String(
      payload?.target_block_id
      || payload?.target_block?.block_id
      || payload?.story_block?.block_id
      || payload?.block_id
      || "",
    ).trim();
    const selectedBlockId = String(selectedBlockContextId || "").trim();
    if (importedBlockId && selectedBlockId && importedBlockId !== selectedBlockId) {
      throw new Error(`import_block_mismatch:${importedBlockId}/${selectedBlockId}`);
    }
  };

  const onImportBlockStoryboardFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      assertImportedBlockMatchesSelection(parsed?.payload && typeof parsed.payload === "object" ? parsed.payload : parsed);
      const nextProject = applyManualBlockStoryboardImport(buildCurrentManualBlockProject(), parsed);
      if (!nextProject) throw new Error("manual_block_storyboard_split_type_expected");
      persistProject(nextProject);
      console.debug("[manual director import video/photo]", {
        stats: getManualClipBoardMaterialStats(nextProject),
        selectedSceneId: nextProject.selectedSceneId,
      });
      flashBlockCopyStatus("Раскадровка/фото блока импортированы");
    } catch (error) {
      flashBlockCopyStatus(`Не удалось импортировать фото JSON: ${error?.message || "ошибка"}`);
    }
  };

  const onImportBlockVideoPromptFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      assertImportedBlockMatchesSelection(parsed?.payload && typeof parsed.payload === "object" ? parsed.payload : parsed);
      const nextProject = applyManualBlockVideoPromptImport(buildCurrentManualBlockProject(), parsed);
      if (!nextProject) throw new Error("manual_block_video_prompt_split_type_expected");
      persistProject(nextProject);
      console.debug("[manual director import video/photo]", {
        stats: getManualClipBoardMaterialStats(nextProject),
        selectedSceneId: nextProject.selectedSceneId,
      });
      flashBlockCopyStatus("Видео-промты блока импортированы");
    } catch (error) {
      flashBlockCopyStatus(`Не удалось импортировать видео JSON: ${error?.message || "ошибка"}`);
    }
  };

  const userNoteItems = useMemo(() => {
    const raw = String(selectedScene?.user_note_ru || "");
    if (!raw.trim()) return [];
    return raw
      .split(/\n|;/g)
      .map((item) => item.trim())
      .filter(Boolean);
  }, [selectedScene?.user_note_ru]);

  useEffect(() => {
    setIsUserNoteEditorOpen(false);
  }, [selectedSceneId]);

  const updateScene = (sceneId, patchOrFactory) => {
    setProject((prevProject) => {
      const baseProject = prevProject || project || {};
      const prevScenes = Array.isArray(baseProject?.scenes) ? baseProject.scenes : [];
      const nextScenes = prevScenes.map((scene) => {
        if (scene.scene_id !== sceneId) return scene;
        const patch = typeof patchOrFactory === "function" ? patchOrFactory(scene) : patchOrFactory;
        return { ...scene, ...(patch || {}) };
      });
      const nextProject = normalizeDirectorProjectOwner({
        ...baseProject,
        scenes: nextScenes,
        selectedSceneId: selectedSceneIdRef.current || baseProject.selectedSceneId || sceneId,
        updatedAt: Date.now(),
        lastPersistReason: "update_scene",
      });
      projectRef.current = nextProject;
      selectedSceneIdRef.current = nextProject.selectedSceneId;

      if (didHydrateRef.current && hasMeaningfulManualProject(nextProject)) {
        persistAndBroadcastDirectorProject(nextProject, { reason: "update_scene" });
        const savedScene = nextScenes.find((s) => s.scene_id === sceneId);
        console.debug("[manual director updateScene saved]", {
          sceneId,
          stats: getManualClipBoardMaterialStats(nextProject),
          route: savedScene?.route,
          image: savedScene?.image_url,
          prompt: Boolean(savedScene?.video_prompt),
          video: savedScene?.video_url,
        });
      }
      return nextProject;
    });
  };

  const onUploadImage = async (sceneId, file, slot = "main") => {
    if (!file) return;
    const previewUrl = URL.createObjectURL(file);
    const isStartSlot = slot === "start";
    const isEndSlot = slot === "end";

    updateScene(sceneId, {
      ...(isEndSlot ? { end_image_preview_url: previewUrl } : { image_preview_url: previewUrl, start_image_preview_url: previewUrl }),
      image_upload_status: "uploading",
      image_upload_error: "",
    });

    try {
      const imageUrl = await uploadManualSceneImage(file);

      updateScene(sceneId, (currentScene = {}) => {
        const nextScene = {
          ...currentScene,
          ...(isEndSlot
            ? { end_image_url: imageUrl, end_image_preview_url: previewUrl }
            : { image_url: imageUrl, start_image_url: imageUrl, image_preview_url: previewUrl, start_image_preview_url: previewUrl }),
        };
        return {
          ...(isEndSlot
            ? { end_image_url: imageUrl, end_image_preview_url: previewUrl }
            : { image_url: imageUrl, start_image_url: imageUrl, image_preview_url: previewUrl, start_image_preview_url: previewUrl }),
          image_upload_status: "done",
          image_upload_error: "",
          status: resolveManualSceneStatus(nextScene),
          error: "",
        };
      });
    } catch (err) {
      updateScene(sceneId, {
        image_upload_status: "error",
        image_upload_error: String(err?.message || "image_upload_failed"),
        error: String(err?.message || "image_upload_failed"),
      });
    }
  };

  const onUsePreviousLastFrame = async (scene) => {
    if (!scene) return;
    const idx = scenes.findIndex((item) => item.scene_id === scene.scene_id);
    const previousScene = idx > 0 ? scenes[idx - 1] : null;
    const previousVideoUrl = String(previousScene?.video_url || "").trim();

    if (!previousScene || !previousVideoUrl) {
      updateScene(scene.scene_id, {
        error: "У предыдущей сцены нет готового видео для извлечения последнего кадра.",
      });
      return;
    }

    if (scene.video_url) {
      updateScene(scene.scene_id, {
        error: "Сначала удалите видео текущей сцены, потом можно заменить стартовое фото.",
      });
      return;
    }

    updateScene(scene.scene_id, {
      image_upload_status: "extracting_last_frame",
      image_upload_error: "",
      error: "",
    });

    try {
      const out = await extractManualVideoLastFrame({
        sceneId: scene.scene_id,
        sourceSceneId: previousScene.scene_id,
        videoUrl: previousVideoUrl,
        frameOffsetSec: 0.08,
      });
      const imageUrl = String(out?.imageUrl || out?.image_url || out?.url || "").trim();
      if (out?.ok === false || !imageUrl) {
        throw new Error(String(out?.detail || out?.hint || "last_frame_extract_failed"));
      }

      updateScene(scene.scene_id, (currentScene = {}) => {
        const nextScene = { ...currentScene, image_url: imageUrl, start_image_url: imageUrl };
        return {
          image_url: imageUrl,
          start_image_url: imageUrl,
          start_image_preview_url: "",
          image_preview_url: "",
          image_upload_status: "done",
          image_upload_error: "",
          error: "",
          status: resolveManualSceneStatus(nextScene),
        };
      });
    } catch (err) {
      updateScene(scene.scene_id, {
        image_upload_status: "error",
        image_upload_error: String(err?.message || "last_frame_extract_failed"),
        error: String(err?.message || "last_frame_extract_failed"),
      });
    }
  };

  async function pollManualSceneVideo(sceneId, jobId, attempt = 0) {
    const maxAttempts = 180;
    const delayMs = 5000;
    try {
      const statusOut = await getManualSceneVideoStatus(jobId);
      const status = String(statusOut?.status || "").toLowerCase();
      const doneVideoUrl = resolveManualStatusVideoUrl(statusOut);
      const isDoneStatus = ["done", "ready", "success", "completed"].includes(status);

      if (isDoneStatus && doneVideoUrl) {
        const videoHasAudio = resolveManualStatusVideoHasAudio(statusOut);

        updateScene(sceneId, (currentScene = {}) => ({
          status: "video_ready",
          video_url: doneVideoUrl,
          video_job_id: jobId,
          video_error: "",
          error: "",
          video_has_audio: videoHasAudio,
          keep_generated_audio: Boolean(statusOut?.keepGeneratedAudio ?? statusOut?.keep_generated_audio ?? currentScene?.keep_generated_audio ?? false),
          generated_audio_policy: String(statusOut?.generatedAudioPolicy ?? statusOut?.generated_audio_policy ?? currentScene?.generated_audio_policy ?? ""),
          generated_audio_gain_db: Number(statusOut?.generatedAudioGainDb ?? statusOut?.generated_audio_gain_db ?? currentScene?.generated_audio_gain_db ?? -16),
          video_request_payload_preview: {
            ...(currentScene?.video_request_payload_preview || {}),
            status: status || "done",
            resolvedWorkflowKey: String(statusOut?.workflowKey || statusOut?.resolvedWorkflowKey || currentScene?.video_request_payload_preview?.resolvedWorkflowKey || ""),
            mode: String(statusOut?.mode || currentScene?.video_request_payload_preview?.mode || ""),
            videoUrl: doneVideoUrl,
            videoHasAudio,
            keepGeneratedAudio: Boolean(statusOut?.keepGeneratedAudio ?? statusOut?.keep_generated_audio ?? currentScene?.keep_generated_audio ?? false),
            generatedAudioPolicy: String(statusOut?.generatedAudioPolicy ?? statusOut?.generated_audio_policy ?? currentScene?.generated_audio_policy ?? ""),
            generatedAudioGainDb: Number(statusOut?.generatedAudioGainDb ?? statusOut?.generated_audio_gain_db ?? currentScene?.generated_audio_gain_db ?? -16),
            soundPromptPreview: String(statusOut?.soundPromptPreview || currentScene?.video_request_payload_preview?.soundPromptPreview || "").slice(0, 180),
          },
        }));
        return;
      }

      if (isDoneStatus && !doneVideoUrl) {
        updateScene(sceneId, {
          status: "video_error",
          video_job_id: jobId,
          video_error: "video_done_without_url",
          error: "Видео сгенерировано, но backend не вернул videoUrl",
        });
        return;
      }

      if (status === "error" || status === "stopped" || status === "not_found") {
        updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: String(statusOut?.error || statusOut?.hint || "video_job_failed"), error: String(statusOut?.error || statusOut?.hint || "video_job_failed") });
        return;
      }
      if (attempt >= maxAttempts) {
        updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: "video_poll_timeout", error: "video_poll_timeout" });
        return;
      }
      updateScene(sceneId, { status: "video_running", video_job_id: jobId, video_error: "" });
      setTimeout(() => pollManualSceneVideo(sceneId, jobId, attempt + 1), delayMs);
    } catch (err) {
      updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: String(err?.message || "video_poll_failed"), error: String(err?.message || "video_poll_failed") });
    }
  }

  const onCreateVideo = async (scene) => {
    const runningStatus = String(scene?.status || "").toLowerCase();
    const runningKey = String(scene?.scene_id || "").trim();
    if (runningStatus === "video_queued" || runningStatus === "video_running" || (runningKey && videoStartInFlightRef.current.has(runningKey))) {
      updateScene(scene.scene_id, {
        error: "Эта сцена уже отправлена на генерацию. Дождитесь результата или ошибки.",
        status: runningStatus || "video_running",
      });
      return;
    }
    const firstLast = isFirstLastRoute(scene.route);
    const safeImageUrl = String(firstLast ? (scene.start_image_url || scene.image_url || "") : (scene.image_url || "")).trim();
    const safeStartImageUrl = String(scene.start_image_url || scene.image_url || "").trim();
    const safeEndImageUrl = String(scene.end_image_url || "").trim();

    if (!scene.video_prompt.trim()) {
      updateScene(scene.scene_id, { error: "Добавьте video_prompt", status: "draft" });
      return;
    }
    if (firstLast) {
      if (!safeStartImageUrl || !safeEndImageUrl) {
        updateScene(scene.scene_id, { error: "Для first/last нужны первый и последний кадр", status: scene.status || "draft" });
        return;
      }
      if (safeStartImageUrl.startsWith("blob:") || safeEndImageUrl.startsWith("blob:")) {
        updateScene(scene.scene_id, {
          error: "Первый/последний кадр ещё не сохранён на сервер. Загрузите фото заново или дождитесь сохранения.",
          status: scene.status || "draft",
        });
        return;
      }
    } else if (!safeImageUrl || safeImageUrl.startsWith("blob:")) {
      updateScene(scene.scene_id, {
        error: "Фото ещё не сохранено на сервер. Загрузите фото заново или дождитесь сохранения.",
        status: scene.status || "draft",
      });
      return;
    }

    if (scene.route === "ia2v" && (!scene.audio_slice_url || !scene.audio_extracted)) {
      updateScene(scene.scene_id, { error: "Для ia2v сначала нажмите «Изъять аудио»", status: scene.status || "draft" });
      return;
    }

    if (scene.route === "i2v_text" && !String(scene.sound_prompt || "").trim()) {
      updateScene(scene.scene_id, { error: "Для i2v_text заполните поле «Промт речи и звука»: кто говорит, точная фраза, стиль голоса и фон.", status: scene.status || "draft" });
      return;
    }

    if (runningKey) videoStartInFlightRef.current.add(runningKey);
    const routePayload = resolveManualVideoRoutePayload(scene);
    const sceneTextPreview = scene.route === "i2v_text" ? resolveI2vTextSceneText(scene) : "";
    const requestedDurationSec = Number(scene.duration_sec || scene.audio_slice_duration_sec || 5);
    updateScene(scene.scene_id, { status: "video_queued", video_error: "", error: "", video_job_id: "" });
    try {
      const payload = {
        sceneId: scene.scene_id,
        imageUrl: safeImageUrl,
        startImageUrl: firstLast ? safeStartImageUrl : undefined,
        endImageUrl: firstLast ? safeEndImageUrl : undefined,
        videoPrompt: scene.video_prompt,
        videoNegativePrompt: scene.negative_prompt || "",
        video_negative_prompt: scene.negative_prompt || "",
        soundPrompt: String(routePayload.soundPrompt || scene.sound_prompt || ""),
        sound_prompt: String(routePayload.sound_prompt || scene.sound_prompt || ""),
        negativeAudioPrompt: String(routePayload.negativeAudioPrompt || scene.negative_audio_prompt || ""),
        negative_audio_prompt: String(routePayload.negative_audio_prompt || scene.negative_audio_prompt || ""),
        requestedDurationSec,
        targetDurationSec: requestedDurationSec,
        sceneStartSec: Number(scene.start_sec || 0),
        sceneEndSec: Number(scene.end_sec || 0),
        sceneDurationSec: Number(scene.duration_sec || requestedDurationSec),
        format: project?.format || "9:16",
        provider: "comfy_remote",
        ...routePayload,
        manualClip: true,
        manual_clip: true,
        source: "manual_clip_board",
        project_kind: project?.project_kind || "clip",
        keepGeneratedAudio: Boolean(routePayload.keepGeneratedAudio),
        generatedAudioPolicy: routePayload.generatedAudioPolicy,
        generatedAudioGainDb: Number(routePayload.generatedAudioGainDb ?? scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      };
      const out = await startManualSceneVideo(payload);
      const jobId = resolveVideoStartJobId(out);
      if (out?.ok === false || !jobId) throw new Error(String(out?.detail || out?.error || "video_start_failed"));
      updateScene(scene.scene_id, {
        status: "video_running",
        video_job_id: jobId,
        video_error: "",
        error: "",
        keep_generated_audio: Boolean(payload.keepGeneratedAudio),
        generated_audio_policy: String(payload.generatedAudioPolicy || ""),
        generated_audio_gain_db: Number(payload.generatedAudioGainDb ?? I2V_SOUND_GAIN_DEFAULT_DB),
        video_request_payload_preview: {
          sceneId: scene.scene_id,
          route: scene.route,
          resolvedWorkflowKey: payload.resolvedWorkflowKey,
          renderMode: payload.renderMode,
          lipSync: payload.lipSync,
          hasAudioSliceUrl: Boolean(payload.audioSliceUrl),
          hasStartImageUrl: Boolean(payload.startImageUrl),
          hasEndImageUrl: Boolean(payload.endImageUrl),
          keepGeneratedAudio: Boolean(payload.keepGeneratedAudio),
          generatedAudioPolicy: payload.generatedAudioPolicy,
          generatedAudioGainDb: Number(payload.generatedAudioGainDb ?? I2V_SOUND_GAIN_DEFAULT_DB),
          soundPromptPreview: String(payload.soundPrompt || "").slice(0, 180),
          negativeAudioPromptPreview: String(payload.negativeAudioPrompt || payload.negative_audio_prompt || "").slice(0, 180),
          sceneTextPreview: String(sceneTextPreview || "").slice(0, 180),
          narratorTextPreview: String(payload.narratorText || "").slice(0, 180),
          speakerTextPreview: String(payload.speakerText || "").slice(0, 180),
        },
      });
      if (runningKey) videoStartInFlightRef.current.delete(runningKey);
      const resumedKey = `${scene.scene_id}:${jobId}`;
      resumedVideoJobsRef.current.add(resumedKey);
      pollManualSceneVideo(scene.scene_id, jobId, 0);
    } catch (err) {
      if (runningKey) videoStartInFlightRef.current.delete(runningKey);
      updateScene(scene.scene_id, { status: "video_error", video_error: String(err?.message || "video_start_failed"), error: String(err?.message || "video_start_failed") });
    }
  };

  if (!project) return <div className="manualDirectorPage"><div className="manualDirectorEmpty"><h2>Проект режиссёрской доски не найден</h2><p>Сначала откройте AI-разбивку и нажмите «Перейти в режиссёрскую доску» или восстановите backup JSON.</p><div className="manualDirectorEmptyActions"><button className="clipSB_btn" onClick={() => (typeof onClose === "function" ? onClose() : navigate("/studio/storyboard"))}>Вернуться в студию</button><label className="clipSB_btn manualUploadBtn">Импорт backup / storyboard JSON<input type="file" accept=".json,application/json" hidden onChange={onImportProjectBackupFile} /></label><button className="clipSB_btn clipSB_btnSecondary" onClick={onRestoreLegacyManualProject}>Восстановить старый проект</button></div>{backupStatus ? <span className="manualDirectorBackupStatus">{backupStatus}</span> : null}</div></div>;

  return <div className="manualDirectorPage">
    <div className="manualDirectorTopbar">
      <button
        className="clipSB_btn"
        onClick={() => {
          safePersistCurrentProject(embedded ? "close_embedded_director_board" : "back_to_ai_split");
          if (embedded && typeof onClose === "function") {
            onClose();
            return;
          }
          navigate("/studio/storyboard");
        }}
      >
        Назад к AI-разбивке
      </button>
      <button className="clipSB_btn" onClick={() => navigate("/studio/manual-clip-audio-preview")}>Прослушать сцены</button>
      <button className="clipSB_btn clipSB_btnPrimary" onClick={onDownloadProjectBackup}>Скачать backup проекта</button>
      <label className="clipSB_btn manualUploadBtn">Импорт backup / storyboard JSON<input type="file" accept=".json,application/json" hidden onChange={onImportProjectBackupFile} /></label>
      {backupStatus ? <span className="manualDirectorBackupStatus">{backupStatus}</span> : null}
    </div>
    {visibleStoryBlocks.length ? <div className="storyboardBlockStrip">
      {visibleStoryBlocks.map((block, idx) => {
        const blockId = String(block.block_id || block.id || `block_${idx + 1}`);
        const firstScene = scenes.find((scene) => scene.story_block_id === blockId);
        const isActive = selectedScene?.story_block_id === blockId;
        return <button
          key={`story-block-${blockId}-${idx}`}
          type="button"
          className={`storyboardBlockChip ${isActive ? "storyboardBlockChipActive" : ""}`}
          style={{ "--storyboard-block-color": block.color || "#8aa4ff" }}
          onClick={() => firstScene ? setSelectedSceneId(firstScene.scene_id) : undefined}
          disabled={!firstScene}
        >
          {block.title_ru || blockId} · {blockSceneCounts.get(blockId) || 0}
        </button>;
      })}
    </div> : null}

    <section className="storyPrepTemplatePanel manualDirectorStoryPrepPanel">
      <div className="storyPrepTemplateHeader">
        <div className="storyPrepTemplateTitle">
          <h3>Шаблон подготовки сюжета</h3>
          <small>Живой production-чеклист: блоки, сцены, полные фразы, тайминги и материалы.</small>
        </div>
        <div className="storyPrepTemplateActions">
          <button
            type="button"
            className="clipSB_btn storyPrepTemplateExpandBtn"
            onClick={() => setIsStoryPrepExpanded((v) => !v)}
            aria-pressed={isStoryPrepExpanded}
            title={isStoryPrepExpanded ? "Свернуть preview" : "Развернуть preview"}
          >
            <span aria-hidden="true">{isStoryPrepExpanded ? "⤡" : "⛶"}</span>
            <span>{isStoryPrepExpanded ? "Свернуть" : "Развернуть"}</span>
          </button>
          <button className="clipSB_btn" onClick={refreshStoryPrepTemplate}>Обновить шаблон</button>
          <button className="clipSB_btn" onClick={onCopyStoryPrepTemplate}>Скопировать шаблон</button>
          <button className="clipSB_btn" onClick={onDownloadStoryPrepTemplate}>Скачать .txt</button>
        </div>
      </div>
      {isStoryPrepExpanded ? (
        <textarea className="storyPrepTemplatePreview" value={storyPrepTemplateText} onChange={(e) => setStoryPrepTemplateText(e.target.value)} spellCheck={false} />
      ) : null}
    </section>

    <div className="manualDirectorGrid">
      <aside className="manualDirectorScenes">
        {scenes.map((scene, idx) => {
          const sceneBlockNumber = scene.story_block_id ? storyBlockNumberById.get(scene.story_block_id) : null;
          const sceneBlockTitle = String(scene.story_block_title_ru || "").trim();
          const sceneRuPreview = truncateText(
            scene.translated_text_ru || scene.short_note || scene.drama_hint || scene.scene_goal_ru || "—",
            104
          );
          return <button
            key={scene.scene_id}
            className={`manualDirectorSceneItem ${selectedScene?.scene_id === scene.scene_id ? "active" : ""} ${scene.status === STATUS_VIDEO_READY ? "ready" : ""}`}
            style={scene.story_block_color ? { "--storyboard-block-color": scene.story_block_color } : undefined}
            onClick={() => setSelectedSceneId(scene.scene_id)}
          >
            <strong>{idx + 1} сцена</strong><span>{scene.route}</span><span>{Number(scene.start_sec).toFixed(2)}–{Number(scene.end_sec).toFixed(2)} c</span><span className={`manualStatusBadge ${scene.status === STATUS_VIDEO_READY ? "ready" : scene.status === "video_error" ? "error" : (scene.status === "video_running" || scene.status === "video_queued") ? "running" : ""}`}>{getSceneStatusLabel(scene)}</span>
            {(sceneBlockNumber || sceneBlockTitle) ? <span className="manualStoryBlockBadges">
              {sceneBlockNumber ? <span className="manualStoryBlockBadge manualStoryBlockNumberBadge">Блок {sceneBlockNumber}</span> : null}
              {sceneBlockTitle ? <span className="manualStoryBlockBadge">{sceneBlockTitle}</span> : null}
            </span> : null}
            <small className="manualSceneRuPreview">{sceneRuPreview}</small>
          </button>;
        })}
      </aside>

      {selectedScene ? <section className="manualDirectorCenter">
        <div className="storyboardSceneBlockHeader">
          <div className="storyboardSceneBlockTitleRow">
            <h2>Сцена {selectedScene.index}</h2>
            <div className="manualBlockWorkflowActions" aria-label="Workflow JSON блока">
              <button
                type="button"
                className="clipSB_btn manualBlockPhotoBtn"
                title="Скопировать JSON блока для генерации фото / раскадровки"
                onClick={onCopyBlockStoryboardJson}
              >🖼 Фото JSON</button>
              <button
                type="button"
                className="clipSB_btn manualBlockVideoBtn"
                title="Скопировать JSON блока для video prompts"
                onClick={onCopyBlockVideoPromptJson}
              >🎬 Видео JSON</button>
              <label
                className="clipSB_btn manualUploadBtn manualBlockPhotoBtn manualBlockImportBtn"
                title="Импортировать JSON раскадровки блока"
              >📥 Фото<input type="file" accept=".json,application/json" hidden onChange={onImportBlockStoryboardFile} /></label>
              <label
                className="clipSB_btn manualUploadBtn manualBlockVideoBtn manualBlockImportBtn"
                title="Импортировать JSON видео-промтов блока"
              >📥 Видео<input type="file" accept=".json,application/json" hidden onChange={onImportBlockVideoPromptFile} /></label>
            </div>
          </div>
          {selectedScene.story_block_title_ru ? <div className="storyboardSceneBlockBadge" style={{ "--storyboard-block-color": selectedScene.story_block_color || "#8aa4ff" }}>
            {selectedBlockNumber ? <span>Блок {selectedBlockNumber}</span> : null}
            <strong>Блок: {selectedScene.story_block_title_ru}</strong>
          </div> : null}
          {blockCopyStatus ? <span className="manualBlockCopyStatus">{blockCopyStatus}</span> : null}
        </div>
        <div className="manualDirectorQuickListenBar" aria-label="Быстрое прослушивание аудио" title={`Диапазон прослушивания: ${formatDirectorSec(playbackRange.startSec)} → ${playbackRange.endSec === null ? "конец" : formatDirectorSec(playbackRange.endSec)} c`}>
          {audioUrl ? <audio
            ref={quickListenAudioRef}
            className="manualDirectorQuickListenAudio"
            src={audioUrl}
            preload="metadata"
            onTimeUpdate={onQuickListenTimeUpdate}
            onPlay={() => { setIsAudioPlaying(true); startQuickListenRaf(); }}
            onPause={() => { setIsAudioPlaying(false); stopQuickListenRaf(); }}
            onEnded={resetQuickListenState}
          /> : null}
          <button
            type="button"
            className="clipSB_btn manualDirectorQuickListenScene"
            onClick={playSceneRange}
            disabled={!audioUrl || !selectedScene}
          >{isAudioPlaying && playbackMode === "scene" ? "⏸ Сцена" : "▶ Сцена"}</button>
          <button
            type="button"
            className="clipSB_btn manualDirectorQuickListenBlock"
            onClick={playBlockRange}
            disabled={!audioUrl || !currentBlock}
          >{isAudioPlaying && playbackMode === "block" ? "⏸ Блок" : "🟢 Блок"}</button>
          <button
            type="button"
            className="clipSB_btn manualDirectorQuickListenFull"
            onClick={playFullAudio}
            disabled={!audioUrl}
          >{isAudioPlaying && playbackMode === "full" ? "⏸ Всё аудио" : "🎵 Всё аудио"}</button>
        </div>

        <label>Route
          <select value={selectedScene.route} onChange={(e) => {
            const route = e.target.value;
            updateScene(selectedScene.scene_id, {
              route,
              keep_generated_audio: (route === "i2v_sound" || route === "i2v_text" || route === "first_last_sound") ? true : false,
              generated_audio_policy: (route === "i2v_sound" || route === "i2v_text" || route === "first_last_sound") ? "mix_generated_audio_under_master" : "",
              generated_audio_gain_db: (route === "i2v_sound" || route === "i2v_text" || route === "first_last_sound") ? Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB) : Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
              start_image_url: isFirstLastRoute(route) ? String(selectedScene.start_image_url || selectedScene.image_url || "") : selectedScene.start_image_url,
              image_url: isFirstLastRoute(route) ? String(selectedScene.start_image_url || selectedScene.image_url || "") : selectedScene.image_url,
            });
          }}>{ROUTES.map((route) => <option key={route} value={route}>{route}</option>)}</select>
        </label>

        <div className="manualTimingReadonly">
          <div>scene_id: {selectedScene.scene_id}</div>
          <div>Тайминг сцены: {formatDirectorSec(selectedScene.start_sec)} → {formatDirectorSec(selectedScene.end_sec)} c</div>
          <div>Длительность: {formatDirectorSec(selectedScene.duration_sec)} c</div>
          {selectedScene.source_phrase_ids?.length ? <div>source_phrase_ids: {selectedScene.source_phrase_ids.join(", ")}</div> : null}
        </div>

        {isStoryVoiceover ? <section className="manualDirectorStoryPassPanel">
          <div className="manualDirectorStoryPassHeader">
            <strong>Story Pass сцены</strong>
            {selectedScene.story_block_title_ru ? <span style={{ "--storyboard-block-color": selectedScene.story_block_color || "#8aa4ff" }}>{selectedBlockNumber ? `Блок ${selectedBlockNumber}: ` : ""}{selectedScene.story_block_title_ru}</span> : null}
          </div>
          <div className="manualDirectorStoryPassCompactHint">
            {selectedSceneRuText || selectedSceneActionText || "Подробный Story Pass доступен в раскрывающемся блоке ниже и через кнопки копирования JSON/текста блока."}
          </div>
          <details className="manualDirectorStoryPassDetails">
            <summary>Показать подробные поля Story Pass сцены</summary>
            <div className="manualDirectorStoryPassGrid">
              <span>translated_text_ru</span><strong>{selectedScene.translated_text_ru || "—"}</strong>
              <span>original_text</span><strong>{selectedSceneOriginalText || "—"}</strong>
              <span>meaning_hint_ru</span><strong>{selectedScene.meaning_hint_ru || "—"}</strong>
              <span>scene_goal_ru</span><strong>{selectedScene.scene_goal_ru || "—"}</strong>
              <span>photo_prompt_hint_ru</span><strong>{selectedScene.photo_prompt_hint_ru || "—"}</strong>
              <span>prompt_hint_ru</span><strong>{selectedScene.prompt_hint_ru || "—"}</strong>
              <span>scene_role_in_block_ru</span><strong>{selectedScene.scene_role_in_block_ru || "—"}</strong>
              <span>block_progress_ru</span><strong>{selectedScene.block_progress_ru || "—"}</strong>
            </div>
          </details>
          <details className="manualDirectorAsrDetails">
            <summary>ASR-фразы сцены ({selectedSceneAudioPhrases.length})</summary>
            {selectedSceneAudioPhrases.length ? <div className="manualDirectorAsrList">
              {selectedSceneAudioPhrases.map((phrase, idx) => <div key={`${selectedScene.scene_id}-asr-${phrase.phrase_id || idx}`}>
                <b>{phrase.phrase_id || idx + 1}</b> · {formatDirectorSec(phrase.start_sec)}→{formatDirectorSec(phrase.end_sec)} · {phrase.text_ru || phrase.text_en || phrase.text || "—"}
              </div>)}
            </div> : <div className="manualDirectorAsrList">ASR-фразы не найдены для source_phrase_ids этой сцены.</div>}
          </details>
        </section> : null}

        {!isStoryVoiceover && isMeaningSceneVisible(selectedScene) ? <div className="storyboardSceneMeaningCompact">
          <div className="storyboardSceneMeaningHeader">
            <strong>Смысл сцены</strong>
            {selectedScene.story_block_title_ru ? <span style={{ "--storyboard-block-color": selectedScene.story_block_color || "#8aa4ff" }}>{selectedBlockNumber ? `Блок ${selectedBlockNumber}: ` : ""}{selectedScene.story_block_title_ru}</span> : null}
            {selectedBlockSceneCount ? <em>сцена {selectedBlockSceneIndex || 1} из {selectedBlockSceneCount}</em> : null}
          </div>
          <div className="storyboardSceneMeaningBody">
            {isMusicClipProject && (selectedScene.song_block_title_ru || selectedScene.song_block_id) ? <p><b>Song block:</b> {selectedScene.song_block_title_ru || selectedScene.song_block_id} {selectedScene.song_block_type ? `(${selectedScene.song_block_type})` : ""}</p> : null}
            {isMusicClipProject && selectedScene.lyrics_text ? <p><b>Lyrics:</b> {selectedScene.lyrics_text}</p> : null}
            {isMusicClipProject ? <p><b>Route:</b> {selectedScene.route} · lip-sync: {selectedScene.lip_sync_required ? "yes" : "no"}{selectedScene.vocal_owner_role ? ` · ${selectedScene.vocal_owner_role}` : ""}</p> : null}
            {isPodcastDialogueProject && (selectedScene.speaker_name || selectedScene.speaker_id) ? <p><b>Speaker:</b> {selectedScene.speaker_name || selectedScene.speaker_id}</p> : null}
            {isPodcastDialogueProject && (selectedScene.topic_block_title_ru || selectedScene.topic_block_id) ? <p><b>Topic:</b> {selectedScene.topic_block_title_ru || selectedScene.topic_block_id}</p> : null}
            {isPodcastDialogueProject && selectedScene.scene_type ? <p><b>Scene type:</b> {selectedScene.scene_type}</p> : null}
            {isPodcastDialogueProject && (selectedScene.narrator_text_en || selectedScene.speaker_text_en) ? <p><b>Text EN:</b> {selectedScene.narrator_text_en || selectedScene.speaker_text_en}</p> : null}
            {selectedScene.story_block_title_ru ? <p><b>Блок:</b> {selectedScene.story_block_title_ru}</p> : null}
            {(selectedScene.story_block_position_ru || storyPositionText) ? <p><b>Позиция:</b> {selectedScene.story_block_position_ru || storyPositionText}</p> : null}
            {selectedSceneRuText ? <p><b>Фраза RU:</b> {selectedSceneRuText}</p> : null}
            {selectedSceneActionText ? <p><b>Что делать:</b> {selectedSceneActionText}</p> : null}
          </div>
          {selectedSceneMeaningDetails.length ? <details className="storyboardSceneMeaningDetails">
            <summary>Подробнее</summary>
            <div className="storyboardSceneMeaningGrid">
              {selectedSceneMeaningDetails.map(([label, value]) => <React.Fragment key={`${selectedScene.scene_id}-${label}`}>
                <span>{label}</span>
                <strong>{value}</strong>
              </React.Fragment>)}
            </div>
          </details> : null}
        </div> : null}

        <div className="manualSceneGuidance">
          <strong>Подсказка сцены</strong>
          <div>Позиция: {storyPositionText}</div>
          <div>Драматургия: {dramaturgyText}</div>
          <div>Смысл: {sceneGoalText}</div>
          <div>Что учесть в prompt: {promptHintText}</div>
          {userNoteItems.length ? <div className="manualUserNotesList">
            <div>Заметки пользователя:</div>
            {userNoteItems.map((item, idx) => <div key={`${selectedScene.scene_id}-user-note-${idx}`}>• {item}</div>)}
          </div> : null}
        </div>
        <div className="manualUserNoteEditor">
          <button className="clipSB_btn manualUserNoteToggle" onClick={() => setIsUserNoteEditorOpen((prev) => !prev)}>
            {isUserNoteEditorOpen ? "Скрыть заметку" : "+ заметка"}
          </button>
          {isUserNoteEditorOpen ? <textarea
            value={String(selectedScene.user_note_ru || "")}
            placeholder="Своя заметка к сцене: звук, фраза, визуал, что не забыть..."
            onChange={(e) => updateScene(selectedScene.scene_id, { user_note_ru: e.target.value })}
          /> : null}
        </div>

        <label className="manualPromptBlock">Prompt<textarea value={selectedScene.video_prompt} onChange={(e) => {
          const nextScene = { ...selectedScene, video_prompt: e.target.value };
          updateScene(selectedScene.scene_id, { video_prompt: e.target.value, status: resolveManualSceneStatus(nextScene) });
        }} /></label>
        <label className="manualNegativePromptBlock">Negative prompt<textarea value={selectedScene.negative_prompt} onChange={(e) => {
          const nextScene = { ...selectedScene, negative_prompt: e.target.value };
          updateScene(selectedScene.scene_id, { negative_prompt: e.target.value, status: resolveManualSceneStatus(nextScene) });
        }} /></label>
        {selectedScene.route === "i2v_text" ? <section className="manualSoundBox">
          <strong>Текст сцены</strong>
          <div className="manualSceneTextDisplay" aria-readonly="true">{resolveI2vTextSceneText(selectedScene) || "—"}</div>
          <strong>Промт речи и звука</strong>
          <label className="manualPromptBlock">
            <textarea
              value={selectedScene.sound_prompt}
              placeholder={'Narrator voice-over says exactly: "..."\nCalm documentary male narrator, clear pronunciation.\nQuiet room tone, subtle ambience, no music overpowering the voice.\nAvoid robotic voice, slurred words, distorted speech.'}
              onChange={(e) => updateScene(selectedScene.scene_id, { sound_prompt: e.target.value, generated_speech_required: true })}
            />
          </label>
          <div className="manualRouteHint">В этом поле одной инструкцией укажите: кто говорит, точную фразу, стиль голоса и фоновые звуки. Текст сцены выше показан только как подсказка — его можно скопировать в prompt.</div>
        </section> : null}
        {isGeneratedSoundRoute(selectedScene.route) ? <section className="manualSoundBox">
          <strong>Сценический звук</strong>
          <div className="manualRouteHint">Опишите звук отдельно: скрипка, выстрел, сирена/мигалки, волны, ветер, короткая фраза, шум толпы. Backend добавит это к prompt как sound design, нормализует сценический звук и применит громкость для монтажа.</div>
          <label className="manualPromptBlock" htmlFor={`manual-sound-prompt-${selectedScene.scene_id}`}>Sound prompt<textarea id={`manual-sound-prompt-${selectedScene.scene_id}`} value={selectedScene.sound_prompt} placeholder="Например для природы: raw field recording of wind through grass, insects, distant birds and dry leaves" onChange={(e) => {
            updateScene(selectedScene.scene_id, { sound_prompt: e.target.value });
          }} /></label>
          <label className="manualPromptBlock" htmlFor={`manual-negative-audio-prompt-${selectedScene.scene_id}`}>Negative audio prompt<textarea id={`manual-negative-audio-prompt-${selectedScene.scene_id}`} value={selectedScene.negative_audio_prompt} placeholder="music, background music, cinematic score, melody, chords, pads, synth, strings, drums, rhythm, beat, trailer music, emotional soundtrack, orchestral ambience, choir, vocals, speech, narrator, human voice" onChange={(e) => {
            updateScene(selectedScene.scene_id, { negative_audio_prompt: e.target.value });
          }} /></label>
          <label className="manualGainControl">Громкость в монтаже после нормализации: {Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB).toFixed(0)} dB
            <input type="range" min={I2V_SOUND_GAIN_MIN_DB} max={I2V_SOUND_GAIN_MAX_DB} step="1" value={Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB)} onChange={(e) => {
              updateScene(selectedScene.scene_id, { generated_audio_gain_db: Number(e.target.value), keep_generated_audio: true, generated_audio_policy: "mix_generated_audio_under_master" });
            }} />
          </label>
          <div className="manualVideoInfo">Backend сначала нормализует i2v_sound примерно до -20 LUFS, затем применяет этот слайдер: -12 dB тихий фон, -6 dB слышимый эффект, 0 dB громко, +4/+6 dB сильный эффект, +10 dB максимум для теста.</div>
        </section> : null}

        <div className="manualDirectorButtons">
          {selectedScene.route === "ia2v" ? <button className="clipSB_btn" onClick={() => {
            if (!selectedScene.audio_slice_url) {
              updateScene(selectedScene.scene_id, { error: "Аудио сцены ещё не нарезано" });
              return;
            }
            const nextScene = { ...selectedScene, audio_extracted: true };
            updateScene(selectedScene.scene_id, {
              audio_extracted: true,
              error: "",
              status: resolveManualSceneStatus(nextScene),
            });
          }}>{selectedScene.audio_extracted ? "Переизъять аудио" : "Изъять аудио"}</button> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_slice_url ? <span className="manualAudioReady">Аудио сцены готово</span> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_extracted ? <span className="manualAudioExtracted">Аудио изъято · готово к ia2v</span> : null}
          <button className="clipSB_btn" disabled={["video_queued", "video_running"].includes(String(selectedScene.status || "").toLowerCase())} onClick={() => onCreateVideo(selectedScene)}>
            {["video_queued", "video_running"].includes(String(selectedScene.status || "").toLowerCase()) ? "Генерация идёт" : "Создать видео"}
          </button>
          <button className="clipSB_btn" disabled={selectedSceneIndex <= 0 || !!selectedScene.video_url} onClick={() => onUsePreviousLastFrame(selectedScene)}>Взять последний кадр предыдущей</button>
          <button className="clipSB_btn" disabled={!selectedScene.video_url} onClick={() => {
            const sceneWithoutVideo = { ...selectedScene, video_url: "" };
            updateScene(selectedScene.scene_id, {
              video_url: "",
              video_job_id: "",
              video_error: "",
              video_has_audio: false,
              generated_audio_policy: "",
              generated_audio_gain_db: I2V_SOUND_GAIN_DEFAULT_DB,
              keep_generated_audio: false,
              error: "",
              status: resolveManualSceneStatus(sceneWithoutVideo),
            });
          }}>{selectedScene.video_url ? "Удалить видео" : "Видео нет"}</button>
        </div>
        {selectedScene.error ? <div className="manualError">{selectedScene.error}</div> : null}
        {(["video_queued", "video_running", "video_error"].includes(selectedScene.status)) ? <div className="manualVideoDebug">job: {selectedScene.video_job_id || "—"} · route: {selectedScene.route} · workflow: {selectedScene.video_request_payload_preview?.resolvedWorkflowKey || "—"} · audioSlice: {selectedScene.video_request_payload_preview?.hasAudioSliceUrl ? "yes" : "no"} · keepAudio: {selectedScene.video_request_payload_preview?.keepGeneratedAudio ? "yes" : "no"} · gain: {selectedScene.video_request_payload_preview?.generatedAudioGainDb ?? selectedScene.generated_audio_gain_db ?? "—"} dB</div> : null}
        <section className="manualDirectorAudio">
          <div className="manualAudioMeta">Аудио: {selectedScene.audio_slice_url ? "готово" : "не готово"} | {Number(selectedScene.duration_sec || 0).toFixed(2)} c</div>
          {selectedScene.audio_slice_url ? <audio controls src={selectedScene.audio_slice_url} /> : <div>Аудио сцены ещё не нарезано</div>}
        </section>
      </section> : null}

      {selectedScene ? <section className="manualDirectorMedia"><h3>Media preview</h3>
        {isFirstLastRoute(selectedScene.route) && !selectedScene.video_url ? (
          <div className="manualFirstLastPanel">
            <div className="manualFirstLastSlot">
              <label className="clipSB_btn manualUploadBtn">Первый кадр<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0], "start")} /></label>
              <div className="manualMediaWindow manualMediaWindowSmall">{selectedScene.start_image_preview_url ? <img src={selectedScene.start_image_preview_url} alt="First frame preview" /> : selectedScene.start_image_url ? <img src={selectedScene.start_image_url} alt="First frame preview" /> : selectedScene.image_preview_url ? <img src={selectedScene.image_preview_url} alt="First frame preview" /> : selectedScene.image_url ? <img src={selectedScene.image_url} alt="First frame preview" /> : <div>Нет первого кадра</div>}</div>
            </div>
            <div className="manualFirstLastSlot">
              <label className="clipSB_btn manualUploadBtn">Последний кадр<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0], "end")} /></label>
              <div className="manualMediaWindow manualMediaWindowSmall">{selectedScene.end_image_preview_url ? <img src={selectedScene.end_image_preview_url} alt="Last frame preview" /> : selectedScene.end_image_url ? <img src={selectedScene.end_image_url} alt="Last frame preview" /> : <div>Нет последнего кадра</div>}</div>
            </div>
          </div>
        ) : (
          <>
            <label className="clipSB_btn manualUploadBtn">Upload image<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0], "main")} /></label>
            <div className="manualMediaWindow">{selectedScene.video_url ? (selectedScene.video_url.startsWith("mock://") ? <div className="manualMockReady">Mock video ready</div> : <video key={selectedScene.video_url} controls preload="metadata" src={selectedScene.video_url} />) : selectedScene.image_preview_url ? <img src={selectedScene.image_preview_url} alt="Scene preview" /> : selectedScene.image_url ? <img src={selectedScene.image_url} alt="Scene preview" /> : <div>Нет image/video preview</div>}</div>
          </>
        )}
        {selectedScene.video_url && !selectedScene.video_url.startsWith("mock://") ? <a className="manualVideoLink" href={selectedScene.video_url} target="_blank" rel="noreferrer">Открыть видео напрямую</a> : null}
        {selectedScene.image_upload_status === "uploading" ? <div className="manualVideoInfo">Фото сохраняется на сервер...</div> : null}
        {selectedScene.image_upload_status === "extracting_last_frame" ? <div className="manualVideoInfo">Извлекаем последний кадр предыдущей сцены...</div> : null}
        {selectedScene.image_upload_status === "error" ? <div className="manualError">{selectedScene.image_upload_error || "Ошибка загрузки фото"}</div> : null}
        {isGeneratedSoundRoute(selectedScene.route) && selectedScene.video_url ? <div className="manualVideoInfo">Видео содержит сценический звук. В монтаже он будет подмешан фоном под основную музыку с выбранной громкостью.</div> : null}
        {selectedScene.route === "ia2v" ? <div className="manualVideoInfo">Lip-sync сцена: в финальном монтаже используем основной аудиотрек, звук видео можно игнорировать.</div> : null}
        {isFirstLastRoute(selectedScene.route) ? <div className="manualVideoInfo">First/last инструмент: загрузите первый и последний кадр. После генерации будет показано одно видео; после удаления видео снова появятся два окна кадров.</div> : <div className="manualVideoInfo">Для продолжения сцены можно взять последний кадр предыдущего готового видео как стартовое фото текущей сцены.</div>}
      </section> : null}

    </div>
  </div>;
}
