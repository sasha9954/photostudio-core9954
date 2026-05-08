import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchJson } from "../../services/api.js";
import "./ManualClipDirectorPage.css";

const STORAGE_KEY = "manual_clip_board_active_project";
const ACTIVE_PROJECT_ID_STORAGE_KEY = "manual_clip_board_active_project_id";
const ROUTES = ["ia2v", "i2v", "i2v_sound", "first_last", "first_last_sound"];
const I2V_SOUND_GAIN_DEFAULT_DB = -6;
const I2V_SOUND_GAIN_MIN_DB = -18;
const I2V_SOUND_GAIN_MAX_DB = 10;

function getManualProjectStorageKey(nodeId = "") {
  const safeId = String(nodeId || "default").trim() || "default";
  return `manual_clip_board_project:${safeId}`;
}

function readJsonStorage(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function readManualActiveProject() {
  const active = readJsonStorage(STORAGE_KEY);
  if (active) return active;
  const activeNodeId = String(localStorage.getItem(ACTIVE_PROJECT_ID_STORAGE_KEY) || "").trim();
  if (activeNodeId) return readJsonStorage(getManualProjectStorageKey(activeNodeId));
  return null;
}

function persistManualProject(nextProject = {}) {
  const safeProject = nextProject && typeof nextProject === "object" ? nextProject : {};
  try {
    const serialized = JSON.stringify(safeProject);
    localStorage.setItem(STORAGE_KEY, serialized);
    const nodeId = String(safeProject?.nodeId || "").trim();
    if (nodeId) {
      localStorage.setItem(ACTIVE_PROJECT_ID_STORAGE_KEY, nodeId);
      localStorage.setItem(getManualProjectStorageKey(nodeId), serialized);
    }
  } catch {}
}
const STATUS_VIDEO_READY = "video_ready";

const FIRST_LAST_ROUTES = new Set(["first_last", "first_last_sound"]);

function isFirstLastRoute(route = "") {
  return FIRST_LAST_ROUTES.has(String(route || "").trim());
}


function normalizeStoryBlock(block = {}, idx = 0) {
  const id = String(block?.block_id || block?.id || block?.story_block_id || `block_${idx + 1}`).trim();
  return {
    ...block,
    block_id: id,
    id: String(block?.id || id),
    title_ru: String(block?.title_ru || block?.title || block?.name || id),
    color: String(block?.color || block?.story_block_color || "#8aa4ff"),
    goal_ru: String(block?.goal_ru || block?.story_block_goal_ru || ""),
    reveal_ru: String(block?.reveal_ru || block?.story_block_reveal_ru || ""),
    emotion_ru: String(block?.emotion_ru || block?.story_block_emotion_ru || ""),
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
    route: ROUTES.includes(scene.route) ? scene.route : "ia2v",
    start_sec: start,
    end_sec: end,
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
    story_block_goal_ru: String(scene.story_block_goal_ru || block?.goal_ru || ""),
    story_block_reveal_ru: String(scene.story_block_reveal_ru || block?.reveal_ru || ""),
    story_block_emotion_ru: String(scene.story_block_emotion_ru || block?.emotion_ru || ""),
    original_text: String(scene.original_text || ""),
    translated_text_ru: String(scene.translated_text_ru || ""),
    meaning_hint_ru: String(scene.meaning_hint_ru || ""),
    source_text_en: String(scene.source_text_en || ""),
    adapted_text_en: String(scene.adapted_text_en || ""),
    scene_role_in_block_ru: String(scene.scene_role_in_block_ru || ""),
    block_progress_ru: String(scene.block_progress_ru || ""),
    video_prompt: String(scene.video_prompt || ""),
    negative_prompt: String(scene.negative_prompt || ""),
    sound_prompt: String(scene.sound_prompt || ""),
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

export default function ManualClipDirectorPage() {
  const navigate = useNavigate();
  const videoStartInFlightRef = useRef(new Set());
  const [project, setProject] = useState(null);
  const [selectedSceneId, setSelectedSceneId] = useState("");
  const [isUserNoteEditorOpen, setIsUserNoteEditorOpen] = useState(false);

  useEffect(() => {
    const parsedProject = readManualActiveProject();
    if (!parsedProject) return;
    try {
      const parsed = parsedProject;
      const storyBlocks = Array.isArray(parsed?.story_blocks) ? parsed.story_blocks.map(normalizeStoryBlock) : [];
      const storyBlockLookup = buildStoryBlockLookup(storyBlocks);
      const scenes = Array.isArray(parsed?.scenes) ? parsed.scenes.map((scene, idx) => normalizeScene(scene, idx, storyBlockLookup)) : [];
      setProject({ ...parsed, story_blocks: storyBlocks, scenes });
      setSelectedSceneId(String(parsed?.selectedSceneId || scenes[0]?.scene_id || ""));
    } catch {
      setProject(null);
    }
  }, []);

  const persistProject = (nextProject) => {
    setProject(nextProject);
    persistManualProject(nextProject);
  };

  const storyBlocks = Array.isArray(project?.story_blocks) ? project.story_blocks : [];
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const selectedScene = useMemo(() => scenes.find((s) => s.scene_id === selectedSceneId) || scenes[0] || null, [scenes, selectedSceneId]);
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
  const selectedBlockSceneIndex = useMemo(() => {
    if (!selectedScene?.story_block_id) return 0;
    return scenes.filter((scene) => scene.story_block_id === selectedScene.story_block_id).findIndex((scene) => scene.scene_id === selectedScene.scene_id) + 1;
  }, [scenes, selectedScene]);
  const selectedBlockSceneCount = selectedScene?.story_block_id ? (blockSceneCounts.get(selectedScene.story_block_id) || 0) : 0;
  const selectedSceneActionText = selectedScene ? (selectedScene.scene_goal_ru || selectedScene.prompt_hint_ru || selectedScene.photo_prompt_hint_ru || "") : "";
  const selectedSceneRuText = selectedScene ? (selectedScene.translated_text_ru || selectedScene.short_note || selectedScene.drama_hint || "") : "";
  const selectedSceneOriginalText = selectedScene ? (selectedScene.original_text || selectedScene.adapted_text_en || selectedScene.source_text_en || "") : "";
  const selectedSceneMeaningDetails = selectedScene ? [
    ["Original", selectedSceneOriginalText],
    ["Meaning", selectedScene.meaning_hint_ru],
    ["Цель блока", selectedScene.story_block_goal_ru],
    ["Раскрытие блока", selectedScene.story_block_reveal_ru],
    ["Эмоция блока", selectedScene.story_block_emotion_ru],
    ["Роль сцены в блоке", selectedScene.scene_role_in_block_ru],
    ["Прогресс блока", selectedScene.block_progress_ru],
  ].filter(([, value]) => String(value || "").trim()) : [];
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
      const nextProject = { ...baseProject, scenes: nextScenes };
      persistManualProject(nextProject);
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
    if (runningKey) videoStartInFlightRef.current.add(runningKey);
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

    const routePayload = resolveManualVideoRoutePayload(scene);
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
        soundPrompt: scene.sound_prompt || routePayload.soundPrompt || "",
        sound_prompt: scene.sound_prompt || routePayload.sound_prompt || "",
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
        },
      });
      if (runningKey) videoStartInFlightRef.current.delete(runningKey);
      pollManualSceneVideo(scene.scene_id, jobId, 0);
    } catch (err) {
      if (runningKey) videoStartInFlightRef.current.delete(runningKey);
      updateScene(scene.scene_id, { status: "video_error", video_error: String(err?.message || "video_start_failed"), error: String(err?.message || "video_start_failed") });
    }
  };

  if (!project) return <div className="manualDirectorPage"><div className="manualDirectorEmpty"><h2>Проект режиссёрской доски не найден</h2><p>Сначала откройте AI-разбивку и нажмите «Перейти в режиссёрскую доску».</p><button className="clipSB_btn" onClick={() => navigate("/studio/storyboard")}>Вернуться в студию</button></div></div>;

  return <div className="manualDirectorPage">
    <div className="manualDirectorTopbar">
      <button className="clipSB_btn" onClick={() => navigate("/studio/storyboard")}>Назад к AI-разбивке</button>
      <button className="clipSB_btn" onClick={() => navigate("/studio/manual-clip-audio-preview")}>Прослушать сцены</button>
    </div>
    {storyBlocks.length ? <div className="storyboardBlockStrip">
      {storyBlocks.map((block, idx) => {
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

    <div className="manualDirectorGrid">
      <aside className="manualDirectorScenes">
        {scenes.map((scene, idx) => <button
          key={scene.scene_id}
          className={`manualDirectorSceneItem ${selectedScene?.scene_id === scene.scene_id ? "active" : ""} ${scene.status === STATUS_VIDEO_READY ? "ready" : ""}`}
          style={scene.story_block_color ? { "--storyboard-block-color": scene.story_block_color } : undefined}
          onClick={() => setSelectedSceneId(scene.scene_id)}
        >
          <strong>{idx + 1} сцена</strong><span>{scene.route}</span><span>{Number(scene.start_sec).toFixed(2)}–{Number(scene.end_sec).toFixed(2)} c</span><span className={`manualStatusBadge ${scene.status === STATUS_VIDEO_READY ? "ready" : scene.status === "video_error" ? "error" : (scene.status === "video_running" || scene.status === "video_queued") ? "running" : ""}`}>{getSceneStatusLabel(scene)}</span>
          {scene.story_block_title_ru ? <span className="manualStoryBlockBadge">{scene.story_block_title_ru}</span> : null}
          <small>{truncateText(scene.short_note || scene.meaning_hint_ru || scene.scene_goal_ru || scene.drama_hint || "—", 96)}</small>
        </button>)}
      </aside>

      {selectedScene ? <section className="manualDirectorCenter">
        <h2>Сцена {selectedScene.index}</h2>
        <label>Route
          <select value={selectedScene.route} onChange={(e) => {
            const route = e.target.value;
            updateScene(selectedScene.scene_id, {
              route,
              keep_generated_audio: (route === "i2v_sound" || route === "first_last_sound") ? true : false,
              generated_audio_policy: (route === "i2v_sound" || route === "first_last_sound") ? "mix_generated_audio_under_master" : "",
              generated_audio_gain_db: (route === "i2v_sound" || route === "first_last_sound") ? Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB) : Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
              start_image_url: isFirstLastRoute(route) ? String(selectedScene.start_image_url || selectedScene.image_url || "") : selectedScene.start_image_url,
              image_url: isFirstLastRoute(route) ? String(selectedScene.start_image_url || selectedScene.image_url || "") : selectedScene.image_url,
            });
          }}>{ROUTES.map((route) => <option key={route} value={route}>{route}</option>)}</select>
        </label>

        <div className="manualTimingReadonly">
          <div>Тайминг сцены: {Number(selectedScene.start_sec).toFixed(2)} – {Number(selectedScene.end_sec).toFixed(2)} c</div>
          <div>Длительность: {Number(selectedScene.duration_sec).toFixed(2)} c</div>
        </div>

        {isMeaningSceneVisible(selectedScene) ? <div className="storyboardSceneMeaningCompact">
          <div className="storyboardSceneMeaningHeader">
            <strong>Смысл сцены</strong>
            {selectedScene.story_block_title_ru ? <span style={{ "--storyboard-block-color": selectedScene.story_block_color || "#8aa4ff" }}>{selectedScene.story_block_title_ru}</span> : null}
            {selectedBlockSceneCount ? <em>сцена {selectedBlockSceneIndex || 1} из {selectedBlockSceneCount}</em> : null}
          </div>
          <div className="storyboardSceneMeaningBody">
            {(selectedScene.story_block_position_ru || storyPositionText) ? <p><b>Позиция:</b> {selectedScene.story_block_position_ru || storyPositionText}</p> : null}
            {selectedSceneRuText ? <p><b>RU:</b> {selectedSceneRuText}</p> : null}
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
        {(selectedScene.route === "i2v_sound" || selectedScene.route === "first_last_sound") ? <section className="manualSoundBox">
          <strong>Сценический звук</strong>
          <div className="manualRouteHint">Опишите звук отдельно: скрипка, выстрел, сирена/мигалки, волны, ветер, короткая фраза, шум толпы. Backend добавит это к prompt как sound design, нормализует сценический звук и применит громкость для монтажа.</div>
          <label className="manualPromptBlock">Sound prompt<textarea value={selectedScene.sound_prompt} placeholder="Например: distant police sirens and flashing emergency lights, low background level under the music" onChange={(e) => {
            updateScene(selectedScene.scene_id, { sound_prompt: e.target.value });
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
        {(selectedScene.route === "i2v_sound" || selectedScene.route === "first_last_sound") && selectedScene.video_url ? <div className="manualVideoInfo">Видео содержит сценический звук. В монтаже он будет подмешан фоном под основную музыку с выбранной громкостью.</div> : null}
        {selectedScene.route === "ia2v" ? <div className="manualVideoInfo">Lip-sync сцена: в финальном монтаже используем основной аудиотрек, звук видео можно игнорировать.</div> : null}
        {isFirstLastRoute(selectedScene.route) ? <div className="manualVideoInfo">First/last инструмент: загрузите первый и последний кадр. После генерации будет показано одно видео; после удаления видео снова появятся два окна кадров.</div> : <div className="manualVideoInfo">Для продолжения сцены можно взять последний кадр предыдущего готового видео как стартовое фото текущей сцены.</div>}
      </section> : null}

    </div>
  </div>;
}
