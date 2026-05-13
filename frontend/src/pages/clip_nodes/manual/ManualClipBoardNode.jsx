import React, { useEffect, useMemo, useState } from "react";
import { fetchJson } from "../../../services/api.js";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { NodeShell } from "../comfy/comfyNodeShared";
import "./ManualClipBoardNode.css";
import { buildManualAudioSlicePayload, buildManualClipSampleJson, buildMockSplitJson, buildStoryPrepTemplateText, getDefaultManualClipNodeData, buildStoryBlockLookup, normalizeManualAudio, normalizeScene, parseManualSplitJson, STORY_PREP_TEMPLATE_META, toBool } from "./manualClipBoardDomain";
import {
  canUseLegacyManualProjectStorage,
  getAccountScopedStorageKey,
  getManualClipBoardMaterialStats,
  persistManualClipBoardProject,
  pickBestManualClipBoardProject,
  readManualClipBoardProjectForNode,
  readActiveManualClipBoardProject,
  writeManualClipBoardOpenState,
} from "../manualProjectBackup.js";


const ACTIVE_PROJECT_STORAGE_KEY = "manual_clip_board_active_project";
const ACTIVE_PROJECT_ID_STORAGE_KEY = "manual_clip_board_active_project_id";

function pickStoryBlocksFromModel(model = {}) {
  const modelBlocks = Array.isArray(model?.story_blocks) ? model.story_blocks : [];
  if (modelBlocks.length) return modelBlocks;

  const rawBlocks = Array.isArray(model?.split_chat?.raw_ai_json?.story_blocks)
    ? model.split_chat.raw_ai_json.story_blocks
    : [];
  return rawBlocks;
}

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

function readManualProjectForNode(nodeId = "") {
  const safeId = String(nodeId || "").trim();
  const centralNodeProject = readManualClipBoardProjectForNode(safeId);
  if (centralNodeProject) return centralNodeProject;

  const centralActiveProject = readActiveManualClipBoardProject();
  const activeOwnerId = String(centralActiveProject?.sourceNodeId || centralActiveProject?.nodeId || "").trim();
  if (centralActiveProject && (!safeId || activeOwnerId === safeId)) return centralActiveProject;

  const active = readJsonStorage(getAccountScopedStorageKey(ACTIVE_PROJECT_STORAGE_KEY));
  if (active && (!safeId || String(active?.nodeId || "") === safeId)) return active;
  const scoped = readJsonStorage(getAccountScopedStorageKey(getManualProjectStorageKey(safeId)));
  if (scoped && (!safeId || String(scoped?.nodeId || "") === safeId)) return scoped;
  if (canUseLegacyManualProjectStorage()) {
    const legacyActive = readJsonStorage(ACTIVE_PROJECT_STORAGE_KEY);
    if (legacyActive && (!safeId || String(legacyActive?.nodeId || "") === safeId)) return legacyActive;
    const legacyScoped = readJsonStorage(getManualProjectStorageKey(safeId));
    if (legacyScoped && (!safeId || String(legacyScoped?.nodeId || "") === safeId)) return legacyScoped;
  }
  return null;
}

function persistManualProject(project = {}, options = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  return persistManualClipBoardProject(safeProject, {
    reason: options?.reason || safeProject?.lastPersistReason || "manual_clip_board_node_persist",
    forceReplace: Boolean(options?.forceReplace),
    explicitReset: Boolean(options?.explicitReset),
    allowMaterialLoss: Boolean(options?.allowMaterialLoss),
  });
}

function removeManualProjectForNode(nodeId = "") {
  const safeId = String(nodeId || "").trim();
  try {
    if (safeId) {
      localStorage.removeItem(getAccountScopedStorageKey(getManualProjectStorageKey(safeId)));
      if (canUseLegacyManualProjectStorage()) localStorage.removeItem(getManualProjectStorageKey(safeId));
    }
    const active = readJsonStorage(getAccountScopedStorageKey(ACTIVE_PROJECT_STORAGE_KEY))
      || (canUseLegacyManualProjectStorage() ? readJsonStorage(ACTIVE_PROJECT_STORAGE_KEY) : null);
    if (!safeId || String(active?.nodeId || "") === safeId) {
      localStorage.removeItem(getAccountScopedStorageKey(ACTIVE_PROJECT_STORAGE_KEY));
      localStorage.removeItem(getAccountScopedStorageKey(ACTIVE_PROJECT_ID_STORAGE_KEY));
      if (canUseLegacyManualProjectStorage()) {
        localStorage.removeItem(ACTIVE_PROJECT_STORAGE_KEY);
        localStorage.removeItem(ACTIVE_PROJECT_ID_STORAGE_KEY);
      }
    }
  } catch {}
}

function getSceneTimelineKey(scene = {}) {
  return [
    String(scene.scene_id || ""),
    Number(scene.start_sec || 0).toFixed(3),
    Number(scene.end_sec || 0).toFixed(3),
  ].join("|");
}

function getSceneIdKey(scene = {}) {
  return String(scene.scene_id || "").trim();
}

function hasDirectorWork(scene = {}) {
  return Boolean(
    scene?.image_url
    || scene?.image_preview_url
    || scene?.video_url
    || scene?.video_prompt
    || scene?.negative_prompt
    || scene?.sound_prompt
    || scene?.audio_slice_url
    || scene?.audio_extracted
    || scene?.video_job_id
    || scene?.video_error
    || scene?.video_request_payload_preview
    || scene?.status === "video_ready"
  );
}

function mergeSceneDirectorWork(scene = {}, old = {}) {
  if (!old || typeof old !== "object") return scene;
  return {
    ...scene,
    route: scene.route || old.route || "",
    use_sound_suggestion: toBool(scene.use_sound_suggestion, toBool(old.use_sound_suggestion)),
    contains_vocal_assumption: toBool(scene.contains_vocal_assumption, toBool(old.contains_vocal_assumption)),
    contains_instrumental_assumption: toBool(scene.contains_instrumental_assumption, toBool(old.contains_instrumental_assumption)),
    speech_start_sec: scene.speech_start_sec ?? old.speech_start_sec ?? scene.start_sec ?? 0,
    speech_end_sec: scene.speech_end_sec ?? old.speech_end_sec ?? scene.end_sec ?? 0,
    pre_silence_sec: scene.pre_silence_sec ?? old.pre_silence_sec ?? 0,
    post_silence_sec: scene.post_silence_sec ?? old.post_silence_sec ?? 0,
    source_phrase_ids: Array.isArray(scene.source_phrase_ids) && scene.source_phrase_ids.length ? scene.source_phrase_ids : (Array.isArray(old.source_phrase_ids) ? old.source_phrase_ids : []),
    story_time: scene.story_time || old.story_time || "",
    scene_type: scene.scene_type || old.scene_type || "",
    drama_hint: scene.drama_hint || old.drama_hint || "",
    short_note: scene.short_note || old.short_note || "",
    photo_prompt_hint_ru: scene.photo_prompt_hint_ru || old.photo_prompt_hint_ru || "",
    prompt_hint_ru: scene.prompt_hint_ru || old.prompt_hint_ru || "",
    user_note_ru: scene.user_note_ru || old.user_note_ru || "",
    story_position_ru: scene.story_position_ru || old.story_position_ru || "",
    story_block_id: scene.story_block_id || old.story_block_id || "",
    story_block_title_ru: scene.story_block_title_ru || old.story_block_title_ru || "",
    story_block_color: scene.story_block_color || old.story_block_color || "",
    story_block_position_ru: scene.story_block_position_ru || old.story_block_position_ru || "",
    story_block_goal_ru: scene.story_block_goal_ru || old.story_block_goal_ru || "",
    story_block_reveal_ru: scene.story_block_reveal_ru || old.story_block_reveal_ru || "",
    story_block_emotion_ru: scene.story_block_emotion_ru || old.story_block_emotion_ru || "",
    original_text: scene.original_text || old.original_text || "",
    translated_text_ru: scene.translated_text_ru || old.translated_text_ru || "",
    meaning_hint_ru: scene.meaning_hint_ru || old.meaning_hint_ru || "",
    source_text_en: scene.source_text_en || old.source_text_en || "",
    adapted_text_en: scene.adapted_text_en || old.adapted_text_en || "",
    scene_role_in_block_ru: scene.scene_role_in_block_ru || old.scene_role_in_block_ru || "",
    block_progress_ru: scene.block_progress_ru || old.block_progress_ru || "",
    scene_goal_ru: scene.scene_goal_ru || old.scene_goal_ru || "",
    image_url: old.image_url || scene.image_url || "",
    image_preview_url: old.image_preview_url || scene.image_preview_url || "",
    image_upload_status: old.image_upload_status || scene.image_upload_status || "",
    image_upload_error: old.image_upload_error || scene.image_upload_error || "",
    video_url: old.video_url || scene.video_url || "",
    video_prompt: old.video_prompt || scene.video_prompt || "",
    negative_prompt: old.negative_prompt || scene.negative_prompt || "",
    sound_prompt: old.sound_prompt || scene.sound_prompt || "",
    negative_audio_prompt: old.negative_audio_prompt || scene.negative_audio_prompt || "",
    audio_slice_url: old.audio_slice_url || scene.audio_slice_url || "",
    audio_slice_duration_sec: old.audio_slice_duration_sec || scene.audio_slice_duration_sec || 0,
    audio_extracted: old.audio_extracted ?? scene.audio_extracted ?? false,
    status: old.status || scene.status || "draft",
    error: old.error || scene.error || "",
    video_job_id: old.video_job_id || scene.video_job_id || "",
    video_error: old.video_error || scene.video_error || "",
    video_has_audio: old.video_has_audio ?? scene.video_has_audio ?? false,
    generated_audio_policy: old.generated_audio_policy || scene.generated_audio_policy || "",
    generated_audio_gain_db: Number(old.generated_audio_gain_db ?? scene.generated_audio_gain_db ?? -16),
    keep_generated_audio: old.keep_generated_audio ?? scene.keep_generated_audio ?? false,
    video_request_payload_preview: old.video_request_payload_preview || scene.video_request_payload_preview || null,
  };
}

function getSceneContractKey(scene = {}) {
  return [
    String(scene.scene_id || ""),
    Number(scene.start_sec || 0).toFixed(3),
    Number(scene.end_sec || 0).toFixed(3),
    String(scene.route || ""),
  ].join("|");
}

function mergeDirectorSceneWork(currentScenes = [], existingScenes = []) {
  const existingByTimeline = new Map();
  for (const oldScene of Array.isArray(existingScenes) ? existingScenes : []) {
    const timelineKey = getSceneTimelineKey(oldScene);
    if (timelineKey && !existingByTimeline.has(timelineKey)) existingByTimeline.set(timelineKey, oldScene);
  }

  return (Array.isArray(currentScenes) ? currentScenes : []).map((scene) => {
    const old = existingByTimeline.get(getSceneTimelineKey(scene)) || null;
    return old ? mergeSceneDirectorWork(scene, old) : scene;
  });
}

function clearManualDirectorProjectForNode(nodeId) {
  removeManualProjectForNode(nodeId);
}

function clearManualProjectForNode(nodeId) {
  removeManualProjectForNode(nodeId);
}

function buildManualDirectorChatResetState() {
  return {
    messages: [],
    answers: {},
    questions: [],
    done: false,
    summary: "",
    contract: null,
    status: "idle",
    error: "",
  };
}

function buildManualSplitResetPatch(splitInputValue = "") {
  return {
    split_chat: {
      user_request: splitInputValue || "",
      ai_summary: "",
      raw_ai_json: null,
    },
    scenes: [],
    selectedSceneId: "",
    last_split_source: "",
    split_audio_status: "idle",
    split_audio_error: "",
    split_audio_count: 0,
    ai_split_status: "idle",
    ai_split_error: "",
    json_error: "",
  };
}

async function sliceManualClipAudio(payload) {
  const data = await fetchJson("/api/manual-clip/slice-audio", { method: "POST", body: payload });
  if (data?.ok === false) {
    throw new Error(String(data?.detail || "audio_slice_failed"));
  }
  return data;
}

async function runManualClipAiSplit(payload) {
  return fetchJson("/api/manual-clip/ai-split", { method: "POST", body: payload });
}
async function runManualClipDirectorChat(payload) {
  return fetchJson("/api/manual-clip/director-chat", { method: "POST", body: payload });
}

export default function ManualClipBoardNode({ id, data }) {
  const navigate = useNavigate();
  const patch = (p) => data?.onPatchNodeData?.(id, p);
  const model = { ...getDefaultManualClipNodeData(), ...(data || {}) };
  const [splitInput, setSplitInput] = useState(model?.split_chat?.user_request || "");
  const [directorUserMessage, setDirectorUserMessage] = useState("");
  const scenes = Array.isArray(model.scenes) ? model.scenes : [];
  const connectedAudio = data?.connectedInputs?.audio_in || data?.connectedAudio || data?.audioInput || null;
  const normalizedConnectedAudio = normalizeManualAudio(connectedAudio);
  const effectiveAudio = normalizedConnectedAudio?.url ? normalizedConnectedAudio : model.audio;
  const aiScenes = Array.isArray(model?.split_chat?.raw_ai_json?.scenes) ? model.split_chat.raw_ai_json.scenes : [];
  const planKind = model.project_kind === "story" ? "истории" : "клипа";
  const directorChat = model.manual_director_chat || {};
  const directorContractMissing = model.split_source === "ai" && model.manual_director_required === true && directorChat.done !== true;

  useEffect(() => {
    const storedProject = readManualProjectForNode(id);
    const storedScenes = Array.isArray(storedProject?.scenes) ? storedProject.scenes : [];
    if (!storedScenes.length) return;

    const currentScenes = Array.isArray(data?.scenes) ? data.scenes : [];
    const mergedScenes = currentScenes.length ? mergeDirectorSceneWork(currentScenes, storedScenes) : storedScenes;
    const currentSignature = JSON.stringify(currentScenes);
    const mergedSignature = JSON.stringify(mergedScenes);
    if (currentSignature === mergedSignature && currentScenes.length) return;

    patch({
      step: storedProject?.step || data?.step || "scene_plan_ready",
      mode: storedProject?.mode || data?.mode || model.mode,
      format: storedProject?.format || data?.format || model.format,
      audio: storedProject?.audio || data?.audio || model.audio,
      split_chat: storedProject?.split_chat || data?.split_chat || model.split_chat,
      project_kind: storedProject?.project_kind || data?.project_kind || model.project_kind,
      last_split_source: storedProject?.last_split_source || data?.last_split_source || model.last_split_source,
      story_blocks: storedProject?.story_blocks || data?.story_blocks || model.story_blocks || [],
      scenes: mergedScenes,
      selectedSceneId: storedProject?.selectedSceneId || data?.selectedSceneId || mergedScenes[0]?.scene_id || "",
      split_audio_status: storedProject?.split_audio_status || data?.split_audio_status || model.split_audio_status,
      split_audio_error: storedProject?.split_audio_error || data?.split_audio_error || model.split_audio_error,
      split_audio_count: storedProject?.split_audio_count ?? data?.split_audio_count ?? model.split_audio_count,
    });
  // Run once on mount: this syncs saved Manual Director work back into the graph node.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onRunDirectorChat = async (forceFinalize = false) => {
    const isStartingNewDirectorDialog = !forceFinalize
      && String(directorUserMessage || "").trim()
      && (!Array.isArray(directorChat.messages) || directorChat.messages.length === 0);
    if (isStartingNewDirectorDialog) {
      clearManualDirectorProjectForNode(id);
      patch(buildManualSplitResetPatch(splitInput));
    }
    patch({ manual_director_chat: { ...directorChat, status: "running", error: "" } });
    try {
      const payload = {
        audio_url: effectiveAudio?.url || "",
        audio_filename: effectiveAudio?.filename || "",
        audio_duration_sec: Number(effectiveAudio?.duration_sec || 0),
        project_kind: model.project_kind,
        format: model.format,
        split_settings: model.split_settings || {},
        user_message: forceFinalize ? "__finalize__" : directorUserMessage,
        messages: Array.isArray(directorChat.messages) ? directorChat.messages : [],
        answers: directorChat.answers || {},
      };
      const response = await runManualClipDirectorChat(payload);
      if (response?.ok === false) throw new Error(String(response?.detail || "director_chat_failed"));
      const updatedMessages = [
        ...(Array.isArray(directorChat.messages) ? directorChat.messages : []),
        ...(directorUserMessage ? [{ role: "user", content: directorUserMessage }] : []),
        ...(response?.assistant_message ? [{ role: "assistant", content: response.assistant_message }] : []),
      ];
      const prevContract = directorChat?.contract || null;
      const nextContract = response?.contract || null;
      const nextDone = Boolean(response?.done);
      const contractChanged = nextDone && JSON.stringify(prevContract) !== JSON.stringify(nextContract);
      patch({
        manual_director_chat: {
          messages: updatedMessages,
          answers: response?.answers || {},
          questions: Array.isArray(response?.questions) ? response.questions : [],
          done: Boolean(response?.done),
          summary: String(response?.summary || ""),
          contract: response?.contract || null,
          status: "done",
          error: "",
        },
        ...(contractChanged ? buildManualSplitResetPatch(splitInput) : {}),
      });
      setDirectorUserMessage("");
    } catch (error) {
      patch({ manual_director_chat: { ...directorChat, status: "error", error: String(error?.message || "director_chat_failed") } });
    }
  };

  const onAudioUpload = (file) => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const audioEl = new Audio();
    audioEl.preload = "metadata";

    audioEl.onloadedmetadata = () => {
      const durationSec = Number(audioEl.duration || 0);
      patch({
        step: "audio_loaded",
        audio_source: "local_upload",
        audio: {
          url,
          filename: file.name,
          duration_sec: Number(durationSec.toFixed(3)),
          duration_ms: Math.round(durationSec * 1000),
        },
      });
    };

    audioEl.onerror = () => {
      patch({
        step: "audio_loaded",
        audio_source: "local_upload",
        audio: {
          url,
          filename: file.name,
          duration_sec: 0,
          duration_ms: 0,
        },
      });
    };

    audioEl.src = url;
  };

  const onRunAiSplit = async () => {
    if (model.manual_director_required && (!directorChat.done || !directorChat.contract)) {
      patch({
        ai_split_status: "error",
        ai_split_error: "Сначала соберите режиссёрский контракт.",
        json_error: "Сначала соберите режиссёрский контракт.",
      });
      return;
    }
    patch({ ai_split_status: "running", ai_split_error: "" });
    try {
      clearManualProjectForNode(id);
      const payload = {
        audio_url: effectiveAudio?.url || "",
        audio_filename: effectiveAudio?.filename || "",
        audio_duration_sec: Number(effectiveAudio?.duration_sec || 0),
        project_kind: model.project_kind,
        format: model.format,
        split_settings: model.split_settings || {},
        user_request: splitInput || "",
        director_contract: model?.manual_director_chat?.contract || {},
      };
      const data = await runManualClipAiSplit(payload);
      if (data?.ok === false || !data?.split_json) {
        throw new Error(String(data?.detail || data?.hint || "ai_split_failed"));
      }
      const splitJson = data.split_json;
      patch({
        step: "split_chat_ready",
        last_split_source: "ai",
        scenes: [],
        selectedSceneId: "",
        split_audio_status: "idle",
        split_audio_error: "",
        split_audio_count: 0,
        split_chat: { user_request: splitInput, ai_summary: splitJson.global_hint || directorChat.summary || "", raw_ai_json: splitJson },
        json_error: "",
        ai_split_status: "done",
        ai_split_error: "",
      });
    } catch (error) {
      const msg = `AI-разбивка не удалась: ${String(error?.message || "unknown_error")}`;
      patch({ ai_split_status: "error", ai_split_error: msg, json_error: msg });
    }
  };

  const onRunJsonSplit = () => {
    const parsed = parseManualSplitJson(model.json_input);
    if (!parsed.ok) {
      patch({ json_error: parsed.error });
      return;
    }

    clearManualProjectForNode(id);
    patch({
      step: "split_chat_ready",
      last_split_source: "json",
      project_kind: parsed.splitJson.project_kind || model.project_kind,
      format: parsed.splitJson.format || model.format,
      scenes: [],
      selectedSceneId: "",
      split_audio_status: "idle",
      split_audio_error: "",
      split_audio_count: 0,
      split_chat: {
        user_request: "JSON import",
        ai_summary: parsed.splitJson.global_hint || "Разбивка загружена из JSON.",
        raw_ai_json: parsed.splitJson,
      },
      json_error: "",
    });
  };

  const onInsertSampleJson = () => {
    const sample = buildManualClipSampleJson({
      projectKind: model.project_kind,
      durationSec: effectiveAudio?.duration_sec || 56,
      format: model.format,
    });
    patch({ json_input: JSON.stringify(sample, null, 2), json_error: "" });
  };

  const onCopySampleJson = () => {
    const sample = buildManualClipSampleJson({
      projectKind: model.project_kind,
      durationSec: effectiveAudio?.duration_sec || 56,
      format: model.format,
    });
    navigator.clipboard?.writeText(JSON.stringify(sample, null, 2));
  };

  const onBuildScenes = async () => {
    const splitJson = model?.split_chat?.raw_ai_json;
    const rawScenes = Array.isArray(splitJson?.scenes) ? splitJson.scenes : [];
    const storyBlocks = Array.isArray(splitJson?.story_blocks) ? splitJson.story_blocks : [];
    const storyBlockLookup = buildStoryBlockLookup(storyBlocks);
    const normalized = rawScenes.map((s, idx) => {
      const scene = normalizeScene(s, idx, storyBlockLookup);
      return {
        ...scene,
        video_prompt: "",
        negative_prompt: "",
        sound_prompt: "",
      };
    });
    let mergedScenes = normalized;
    let splitAudioError = "";
    let splitAudioStatus = "idle";
    let splitAudioCount = 0;

    if (effectiveAudio?.url) {
      splitAudioStatus = "slicing";
      try {
        const payload = buildManualAudioSlicePayload({ audio: effectiveAudio, splitJson });
        const sliced = await sliceManualClipAudio(payload);
        const byId = new Map((Array.isArray(sliced?.scenes) ? sliced.scenes : []).map((scene) => [String(scene?.scene_id || ""), scene]));
        mergedScenes = normalized.map((scene) => {
          const fromBackend = byId.get(scene.scene_id);
          if (!fromBackend) return scene;
          return {
            ...scene,
            audio_slice_url: String(fromBackend.audio_slice_url || ""),
            audio_slice_duration_sec: Number(fromBackend.audio_slice_duration_sec || 0),
          };
        });
        splitAudioStatus = "done";
        splitAudioCount = mergedScenes.filter((scene) => !!scene.audio_slice_url).length;
      } catch (error) {
        splitAudioStatus = "error";
        splitAudioError = String(error?.message || "audio_slice_failed");
      }
    }

    const nextProjectSnapshot = {
      nodeId: id,
      mode: model.mode,
      format: model.format,
      audio: effectiveAudio,
      split_chat: model.split_chat,
      project_kind: model.project_kind,
      last_split_source: model.last_split_source,
      step: "scene_plan_ready",
      prep_template_meta: STORY_PREP_TEMPLATE_META,
      story_blocks: storyBlocks,
      scenes: mergedScenes,
      selectedSceneId: mergedScenes[0]?.scene_id || "",
      split_audio_status: splitAudioStatus,
      split_audio_error: splitAudioError,
      split_audio_count: splitAudioCount,
    };
    persistManualProject(nextProjectSnapshot);

    patch({
      step: "scene_plan_ready",
      prep_template_meta: STORY_PREP_TEMPLATE_META,
      story_blocks: storyBlocks,
      scenes: mergedScenes,
      selectedSceneId: mergedScenes[0]?.scene_id || "",
      split_audio_status: splitAudioStatus,
      split_audio_error: splitAudioError,
      split_audio_count: splitAudioCount,
    });
  };

  const onCopyProjectJson = () => {
    const projectSnapshot = {
      nodeId: id,
      mode: model.mode,
      format: model.format,
      audio: effectiveAudio,
      split_chat: model.split_chat,
      project_kind: model.project_kind,
      last_split_source: model.last_split_source,
      step: model.step,
      prep_template_meta: STORY_PREP_TEMPLATE_META,
      story_blocks: pickStoryBlocksFromModel(model),
      scenes,
      selectedSceneId: model.selectedSceneId || scenes[0]?.scene_id || "",
      split_audio_status: model.split_audio_status,
      split_audio_error: model.split_audio_error,
      split_audio_count: model.split_audio_count,
    };
    navigator.clipboard?.writeText(JSON.stringify(projectSnapshot, null, 2));
  };

  const storyPrepProject = useMemo(() => ({
    ...model,
    audio: effectiveAudio,
    story_blocks: pickStoryBlocksFromModel(model),
    scenes: scenes.length ? scenes : (Array.isArray(model.split_chat?.raw_ai_json?.scenes) ? model.split_chat.raw_ai_json.scenes : []),
    global_hint: model.split_chat?.raw_ai_json?.global_hint || model.split_chat?.ai_summary || model.global_hint || "",
  }), [model, effectiveAudio, scenes]);
  const [storyPrepTemplateText, setStoryPrepTemplateText] = useState("");
  const [isStoryPrepExpanded, setIsStoryPrepExpanded] = useState(false);

  useEffect(() => {
    setStoryPrepTemplateText(buildStoryPrepTemplateText(storyPrepProject));
  }, [storyPrepProject]);

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

  const canBuildScenes = !!model?.split_chat?.raw_ai_json;
  const canOpenBoard = scenes.length > 0;

  const updateSplitSettings = (key, value) => {
    patch({ split_settings: { ...(model.split_settings || {}), [key]: value } });
  };

  const onOpenDirectorBoard = () => {
    let nextScenes = scenes;
    let existingProject = null;
    try {
      existingProject = readManualProjectForNode(id);
      if (existingProject?.nodeId === id) {
        const existingScenes = Array.isArray(existingProject?.scenes) ? existingProject.scenes : [];
        nextScenes = mergeDirectorSceneWork(scenes, existingScenes);
      }
    } catch {
      nextScenes = scenes;
      existingProject = null;
    }

    const payload = {
      nodeId: id,
      mode: model.mode,
      format: model.format,
      audio: effectiveAudio,
      split_chat: model.split_chat,
      project_kind: model.project_kind,
      last_split_source: model.last_split_source,
      prep_template_meta: STORY_PREP_TEMPLATE_META,
      story_blocks: pickStoryBlocksFromModel(model),
      scenes: nextScenes,
    };
    const protectedPayload = pickBestManualClipBoardProject([payload, existingProject]) || payload;
    const persisted = persistManualProject(protectedPayload, { reason: "open_manual_clip_board_node" });
    console.debug("[manual clip board node open]", {
      persisted,
      protectedByExisting: protectedPayload === existingProject,
      stats: getManualClipBoardMaterialStats(protectedPayload),
    });
    patch({
      scenes: nextScenes,
      selectedSceneId: nextScenes[0]?.scene_id || model.selectedSceneId || "",
      step: scenes.length ? "scene_plan_ready" : model.step,
    });
    writeManualClipBoardOpenState({
      isOpen: true,
      sourceNodeId: id,
      selectedSceneId: String(protectedPayload?.selectedSceneId || protectedPayload?.scenes?.[0]?.scene_id || "").trim(),
      project_id: String(protectedPayload?.project_id || protectedPayload?.projectId || "").trim(),
      input_signature: String(protectedPayload?.input_signature || protectedPayload?.inputSignature || "").trim(),
      routePath: "/studio/storyboard",
      updatedAt: Date.now(),
    });
    navigate("/studio/storyboard", {
      state: { openManualDirectorBoard: true, sourceNodeId: id, director_board: protectedPayload, project: protectedPayload },
    });
  };

  return <NodeShell title="AI-разбивка клипа" onClose={() => data?.onRemoveNode?.(id)} icon={<span>✂️</span>} className="clipSB_nodeStoryboard manualClipBoardNode">
    <div className="manualClipBoardNode_body">
      {model.step === "empty" ? <div className="manualLockedState">
        <h3>Подключите аудио-ноду или загрузите аудио вручную</h3>
        <label className="clipSB_btn">
          Загрузить аудио
          <input type="file" accept="audio/*" onChange={(e) => onAudioUpload(e.target.files?.[0])} hidden />
        </label>
      </div> : <div>
        <div className="manualHeaderRow">
          <div className="manualChip">Аудио: {model.audio_source === "connected_audio_node" ? "подключено" : model.audio_source === "local_upload" ? "локальное" : effectiveAudio?.url ? "готово" : "пусто"}</div>
          <div className="manualChip">Разбивка: {model.split_chat?.raw_ai_json ? "готово" : "пусто"}</div>
          <div className="manualChip">Сцен: {scenes.length}</div>
          <div className="manualChip">Режим: {model.project_kind === "story" ? "История" : "Клип"}</div>
          <div className="manualChip">Формат:
            <select value={model.format} onChange={(e) => patch({ format: e.target.value })}><option>9:16</option><option>16:9</option><option>1:1</option></select>
          </div>
          <div className="manualChip">Статус: {model.step}</div>
          <button className="clipSB_btn" onClick={() => {
            clearManualProjectForNode(id);
            patch(getDefaultManualClipNodeData());
          }}>Сбросить</button>
        </div>

        <div className="manualSplitWorkspace">
          <section className="manualPanel manualPanelDraft">
            <h4>Черновик разбивки</h4>
            <p>Здесь хранится мини-задача для AI-разбивщика.</p>
            <label>Тип проекта<select value={model.project_kind} onChange={(e) => patch({ project_kind: e.target.value })}><option value="clip">Клип</option><option value="story">История</option></select></label>
            <label>Формат<select value={model.format} onChange={(e) => patch({ format: e.target.value })}><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
            <label>Цель сцен<select value={model.split_settings?.target_scene_count || "auto"} onChange={(e) => updateSplitSettings("target_scene_count", e.target.value)}><option value="auto">auto</option><option value="8">8</option><option value="10">10</option><option value="12">12</option><option value="16">16</option></select></label>
            <label>Lip-sync<select value={model.split_settings?.lipsync_ratio || "auto"} onChange={(e) => updateSplitSettings("lipsync_ratio", e.target.value)}><option value="auto">auto</option><option value="30%">30%</option><option value="50%">50%</option><option value="70%">70%</option></select></label>
            <label>Маршрут<select value={model.split_settings?.route_preference || "mixed"} onChange={(e) => updateSplitSettings("route_preference", e.target.value)}><option value="mixed">mixed</option><option value="mostly_i2v">mostly_i2v</option><option value="mostly_ia2v">mostly_ia2v</option><option value="with_i2v_sound">with_i2v_sound</option></select></label>
            <label>Нарезка<select value={model.split_settings?.cutting_style || "mixed_phrase"} onChange={(e) => updateSplitSettings("cutting_style", e.target.value)}><option value="mixed_phrase">смешанная по фразам</option><option value="longer_lipsync">lip-sync длиннее 5–7 сек</option><option value="short_visuals">простые видео короче 3–4 сек</option></select></label>
          </section>

          <section className="manualPanel manualPanelAi">
            <h4>AI/JSON разбивка</h4>
            <label>Способ разбора<select value={model.split_source} onChange={(e) => patch({ split_source: e.target.value })}><option value="ai">AI</option><option value="json">JSON</option></select></label>
            {model.split_source === "ai" ? <>
              <div className="manualAiSummary">
                <strong>AI-консультация</strong>
                <div>{directorChat?.messages?.[directorChat.messages.length - 1]?.content || "Опишите задумку, и AI уточнит детали для режиссёрского контракта."}</div>
                {Array.isArray(directorChat.questions) && directorChat.questions.length > 0 ? <div>
                  {directorChat.questions.map((q) => <div key={q.id}>• {q.label}</div>)}
                </div> : null}
                <textarea className="manualAiTextarea" value={directorUserMessage} onChange={(e) => setDirectorUserMessage(e.target.value)} placeholder="Ответьте на вопросы AI-консультации..." />
                <div className="manualActionsRow">
                  <button className="clipSB_btn" onClick={() => onRunDirectorChat(false)} disabled={directorChat.status === "running"}>Отправить AI</button>
                  <button className="clipSB_btn" onClick={() => onRunDirectorChat(true)} disabled={directorChat.status === "running"}>Собрать режиссёрский контракт</button>
                  <button className="clipSB_btn" onClick={() => {
                    clearManualDirectorProjectForNode(id);
                    patch({
                      manual_director_chat: buildManualDirectorChatResetState(),
                      ...buildManualSplitResetPatch(""),
                    });
                    setSplitInput("");
                    setDirectorUserMessage("");
                  }} disabled={directorChat.status === "running"}>Новая AI-консультация</button>
                </div>
                {directorChat.error ? <div className="manualJsonError">{directorChat.error}</div> : null}
                {directorChat.done ? <div>
                  <div><strong>Контракт собран</strong></div>
                  <div>{directorChat.summary || "Режиссёрский контракт готов."}</div>
                </div> : <small>Сначала соберите режиссёрский контракт, чтобы AI не придумывал сценарий сам.</small>}
              </div>
              <textarea className="manualAiTextarea" value={splitInput} onChange={(e) => setSplitInput(e.target.value)} placeholder="Например: использовать lip-sync / i2v / i2v_sound примерно 40/40/20. Lip-sync подольше 5–7 сек на чистых соседних фразах, простые видео 3–4 сек. Строго резать по концам фраз/паузам, не внутри слов." />
              <button className="clipSB_btn" onClick={onRunAiSplit} disabled={model.ai_split_status === "running" || directorContractMissing}>Разобрать при помощи AI</button>
              {directorContractMissing ? <small>AI-разбивка станет доступна после режиссёрского контракта.</small> : null}
              {model.ai_split_status === "running" ? <div>AI разбирает аудио...</div> : null}
              {model.ai_split_error ? <div className="manualJsonError">{model.ai_split_error}</div> : null}
              {model.split_chat?.raw_ai_json ? <div className="manualAiSummary">{model.split_chat?.ai_summary}</div> : <div className="manualAiSummary">AI-разбивка появится после запуска по контракту.</div>}
              <small>AI создаёт только фразовую разбивку и краткую драматургию. Доступные clip routes: ia2v/lip-sync, i2v, i2v_sound. First/last не используется как route; продолжение через последний кадр делается вручную в доске.</small>
              <button className="clipSB_btn" onClick={() => {
                const ai = buildMockSplitJson({ projectKind: model.project_kind, splitSettings: model.split_settings, format: model.format, durationSec: effectiveAudio?.duration_sec || 24 });
                patch({ step: "split_chat_ready", last_split_source: "ai", split_chat: { user_request: splitInput, ai_summary: ai.global_hint, raw_ai_json: ai }, json_error: "" });
              }} style={{ display: "none" }}>Mock split</button>
</> : <>
              <div className="manualActionsRow">
                <button className="clipSB_btn" onClick={onInsertSampleJson}>Вставить образец JSON</button>
                <button className="clipSB_btn" onClick={onCopySampleJson}>Скопировать образец JSON</button>
              </div>
              <small>Образец показывает структуру. Для реального клипа/истории заполните scenes на всю длину аудио.</small>
              <textarea className="manualJsonTextarea" value={model.json_input || ""} onChange={(e) => patch({ json_input: e.target.value })} placeholder="Вставьте JSON разбивки..." />
              <button className="clipSB_btn" onClick={onRunJsonSplit}>Разобрать при помощи JSON</button>
              {model.json_error ? <div className="manualJsonError">{model.json_error}</div> : null}
            </>}
          </section>

          <section className="manualPanel manualPanelAudio">
            <h4>Аудио-разбор</h4>
            <div>filename: {effectiveAudio?.filename || "—"}</div>
            <div>duration_sec: {Number(effectiveAudio?.duration_sec || 0).toFixed(2)}</div>
            <div>split_type: phrase_based</div>
            <div>candidate phrase boundaries: [0.00, 3.65, 8.20, 12.80]</div>
            <div>Анализ будет подключен позже</div>
          </section>

          <section className="manualPanel manualPanelPlan manualSplitPlan">
            <h4>План {planKind}</h4>
            {aiScenes.length === 0 ? <div>План появится после AI-разбивки или разбора JSON.</div> : <>
              <div className="manualPlanRows">{aiScenes.map((s, idx) => <div key={`${s.scene_id || idx}-${idx}`} className="manualPlanRow">{(s.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`).toUpperCase()} | {Number(s.start_sec || 0).toFixed(2)}–{Number(s.end_sec || 0).toFixed(2)} | {s.route || "ia2v"} | {s.quality || "check"} | {s.drama_hint || s.short_note || s.scene_goal_ru || "—"}</div>)}</div>
              <div className="manualActionsRow">
                <button className="clipSB_btn" onClick={onBuildScenes} disabled={!canBuildScenes}>Собрать сцены</button>
              </div>
            </>}
            {model.split_audio_error ? <div className="manualJsonError">Сцены собраны, но аудио-нарезка не удалась: {model.split_audio_error}</div> : null}
            {model.split_audio_status === "done" ? <div>Аудио сцен нарезано: {Number(model.split_audio_count || 0)}</div> : null}
            {scenes.length > 0 ? <div className="manualActionsRow">
              <button className="clipSB_btn" onClick={onCopyProjectJson}>Скопировать JSON проекта</button>
              <button className="clipSB_btn" onClick={onOpenDirectorBoard} disabled={!canOpenBoard}>Перейти в режиссёрскую доску</button>
            </div> : null}
          </section>

          <section className="manualPanel storyPrepTemplatePanel">
            <div className="storyPrepTemplateHeader">
              <div className="storyPrepTemplateTitle">
                <h4>Шаблон подготовки сюжета</h4>
                <small>Динамически строится из текущего проекта, блоков, сцен, таймингов и фраз.</small>
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
        </div>
      </div>}
    </div>
    <Handle type="target" position={Position.Left} id="audio_in" />
    <Handle type="source" position={Position.Right} id="manual_clip_board_out" />
  </NodeShell>;
}
