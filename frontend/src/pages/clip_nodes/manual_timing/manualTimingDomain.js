import { CHATGPT_STORY_SPLIT_TASK, STORY_PREP_TEMPLATE_META } from "../manual/manualClipBoardDomain.js";
import {
  canUseLegacyManualProjectStorage,
  getAccountScopedStorageKey,
  MANUAL_TIMING_ACTIVE_PROJECT_KEY,
  MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY,
  getManualTimingProjectStorageKey,
  readManualProjectJsonStorage,
  unwrapManualProjectBackupJson,
} from "../manualProjectBackup.js";
export { MANUAL_TIMING_ACTIVE_PROJECT_KEY, MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY };
export const MANUAL_TIMING_MODE = "manual_timing";
export const MANUAL_TIMING_STORY_VOICEOVER_MODE = "story_voiceover";
export const MANUAL_TIMING_STORY_PROJECT_KIND = "story";
export const MANUAL_TIMING_MUSIC_CLIP_MODE = "music_clip";
export const MANUAL_TIMING_MUSIC_CLIP_PROJECT_KIND = "clip";
export const MANUAL_TIMING_PODCAST_DIALOGUE_MODE = "podcast_dialogue";
export const MANUAL_TIMING_PODCAST_DIALOGUE_PROJECT_KIND = "podcast";

export const MANUAL_TIMING_SECTIONS = ["intro", "verse", "chorus", "bridge", "instrumental", "outro"];
export const MANUAL_TIMING_ROUTES = ["ia2v", "i2v", "i2v_sound", "i2v_text"];
export const MANUAL_TIMING_ENERGY = ["soft", "mid", "high"];


export const MANUAL_TIMING_AI_WORKFLOW_TYPE = "manual_timing_ai_pipeline";
export const MANUAL_TIMING_AI_WORKFLOW_VERSION = "1.0";
export const MANUAL_TIMING_AI_PASS_VERSION = "1.0";
export const MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE = "photostudio_manual_timing_current_project_backup";

export const MANUAL_TIMING_AI_PASS_STAGES = [
  {
    pass_type: "semantic_story_cut",
    pass_name_ru: "Смысловая нарезка",
    stage_label_ru: "1 · Смысловая нарезка",
    copy_label_ru: "Скопировать смысловую нарезку",
    apply_label_ru: "Применить смысловую нарезку",
    requires: [],
    unlocks: ["story_bible"],
    next_stage: "story_bible",
    activation_phrase: "SEMANTIC_STORY_CUT_DONE",
    result_field: "manual_timing_pass_result",
  },
  {
    pass_type: "story_bible",
    pass_name_ru: "Библия истории",
    stage_label_ru: "2 · Библия истории",
    copy_label_ru: "Скопировать библию истории",
    apply_label_ru: "Применить библию истории",
    requires: ["semantic_story_cut"],
    unlocks: ["block_storyboard"],
    next_stage: "block_storyboard",
    activation_phrase: "STORY_BIBLE_DONE",
    result_field: "manual_timing_pass_result",
  },
  {
    pass_type: "block_storyboard",
    pass_name_ru: "Блочная раскадровка",
    stage_label_ru: "3 · Блочная раскадровка",
    copy_label_ru: "Скопировать блочную раскадровку",
    apply_label_ru: "Применить блочную раскадровку",
    requires: ["semantic_story_cut", "story_bible"],
    unlocks: ["director_board"],
    next_stage: "director_board",
    activation_phrase: "BLOCK_STORYBOARD_DONE",
    result_field: "manual_timing_pass_result",
  },
];

export const MANUAL_TIMING_AI_PASS_BY_TYPE = MANUAL_TIMING_AI_PASS_STAGES.reduce((acc, stage) => {
  acc[stage.pass_type] = stage;
  return acc;
}, {});


export function isManualTimingAiRequestJson(raw = {}) {
  const passStatus = String(raw?.manual_timing_pass?.status || "").trim();
  const hasRequiredResult = Boolean(raw?.manual_timing_pass_result_required);
  const hasActualResult = Boolean(raw?.manual_timing_pass_result || raw?.manualTimingPassResult);
  return passStatus === "ready_for_ai" && hasRequiredResult && !hasActualResult;
}

export function validateManualTimingPassResultActivation(importedObject = {}, clickedPassType = "") {
  const stage = MANUAL_TIMING_AI_PASS_BY_TYPE[clickedPassType];
  if (!stage?.activation_phrase) return { ok: true, errors: [] };

  const result = importedObject?.manual_timing_pass_result || importedObject?.manualTimingPassResult || {};
  const completedPassType = String(result?.completed_pass_type || result?.completedPassType || "").trim();
  const activationPhrase = String(result?.activation_phrase || result?.activationPhrase || "").trim();

  const errors = [];

  if (completedPassType !== clickedPassType) {
    errors.push(`manual_timing_pass_result.completed_pass_type должен быть ${clickedPassType}`);
  }

  if (activationPhrase !== stage.activation_phrase) {
    errors.push(`Нужен activation_phrase: ${stage.activation_phrase}`);
  }

  return {
    ok: errors.length === 0,
    errors,
  };
}

export function normalizeManualTimingWorkflow(workflow = {}, completedFallback = []) {
  const rawCompleted = Array.isArray(workflow?.completed_stages) ? workflow.completed_stages : completedFallback;
  const completedStages = [...new Set((Array.isArray(rawCompleted) ? rawCompleted : [])
    .map((stage) => String(stage || "").trim())
    .filter((stage) => Boolean(MANUAL_TIMING_AI_PASS_BY_TYPE[stage]))
  )];
  const currentStage = String(workflow?.current_stage || "").trim()
    || (completedStages.includes("block_storyboard") ? "director_board"
      : completedStages.includes("story_bible") ? "block_storyboard"
        : completedStages.includes("semantic_story_cut") ? "story_bible"
          : "semantic_story_cut");
  const lockedStages = MANUAL_TIMING_AI_PASS_STAGES
    .filter((stage) => !completedStages.includes(stage.pass_type)
      && stage.requires.some((required) => !completedStages.includes(required)))
    .map((stage) => stage.pass_type);
  return {
    workflow_type: MANUAL_TIMING_AI_WORKFLOW_TYPE,
    workflow_version: MANUAL_TIMING_AI_WORKFLOW_VERSION,
    current_stage: currentStage,
    completed_stages: completedStages,
    locked_stages: lockedStages,
  };
}

export function buildManualTimingWorkflowForPass(project = {}, passType = "semantic_story_cut") {
  const workflow = normalizeManualTimingWorkflow(project?.manual_timing_workflow);
  const passMeta = MANUAL_TIMING_AI_PASS_BY_TYPE[passType] || MANUAL_TIMING_AI_PASS_BY_TYPE.semantic_story_cut;
  return {
    workflow: {
      ...workflow,
      current_stage: workflow.current_stage || passMeta.pass_type,
    },
    pass: {
      pass_type: passMeta.pass_type,
      pass_name_ru: passMeta.pass_name_ru,
      pass_version: MANUAL_TIMING_AI_PASS_VERSION,
      status: "ready_for_ai",
      requires: [...passMeta.requires],
      unlocks: [...passMeta.unlocks],
    },
  };
}

export function completeManualTimingWorkflowStage(project = {}, passType = "") {
  const stage = MANUAL_TIMING_AI_PASS_BY_TYPE[passType];
  const workflow = normalizeManualTimingWorkflow(project?.manual_timing_workflow);
  if (!stage) return workflow;
  const completedStages = [...new Set([...workflow.completed_stages, stage.pass_type])];
  return normalizeManualTimingWorkflow({
    ...workflow,
    current_stage: stage.next_stage,
    completed_stages: completedStages,
  });
}

export const MANUAL_TIMING_UNKNOWN_STORY_BLOCK = {
  block_id: "block_unknown",
  title_ru: "Без блока",
  summary_ru: "",
  block_goal_ru: "",
  block_reveal_ru: "",
  block_emotion_ru: "",
  global_story_context_ru: "",
  block_place_in_global_arc_ru: "",
  inherits_style_lock_ru: "",
  inherits_world_lock_ru: "",
  inherits_atmosphere_ru: "",
  inherits_continuity_rules_ru: "",
  block_visual_bible_ru: "",
  block_style_lock_ru: "",
  block_location_lock_ru: "",
  block_time_of_day_ru: "",
  block_color_palette_ru: "",
  block_camera_language_ru: "",
  block_continuity_rules_ru: "",
  block_storyboard_summary_ru: "",
  block_reference_frame_prompt_en: "",
  color: "#64748B",
  scene_ids: [],
  start_sec: 0,
  end_sec: 0,
};

export const MANUAL_TIMING_PROJECT_STORY_BIBLE_FIELDS = [
  "project_story_summary_ru",
  "project_core_theme_ru",
  "project_drama_arc_ru",
  "project_visual_bible_ru",
  "project_style_lock_ru",
  "project_world_lock_ru",
  "project_character_identity_lock_ru",
  "project_location_lock_ru",
  "project_time_progression_ru",
  "project_atmosphere_lock_ru",
  "project_camera_language_ru",
  "project_color_progression_ru",
  "project_continuity_rules_ru",
  "project_must_keep_same_ru",
  "project_allowed_variation_ru",
  "project_reference_prompt_en",
];

export function pickManualTimingProjectStoryBibleFields(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  return MANUAL_TIMING_PROJECT_STORY_BIBLE_FIELDS.reduce((acc, key) => {
    acc[key] = String(safeProject?.[key] || "");
    return acc;
  }, {});
}

export const MANUAL_TIMING_BLOCK_STORYBOARD_BLOCK_FIELDS = [
  "block_visual_bible_ru",
  "block_style_lock_ru",
  "block_location_lock_ru",
  "block_time_of_day_ru",
  "block_color_palette_ru",
  "block_camera_language_ru",
  "block_continuity_rules_ru",
  "block_storyboard_summary_ru",
  "block_reference_frame_prompt_en",
];

export const MANUAL_TIMING_BLOCK_STORYBOARD_SCENE_FIELDS = [
  "storyboard_frame_role_ru",
  "source_image_prompt_en",
  "source_image_prompt_ru",
  "source_image_negative_prompt_en",
  "i2v_prompt_en",
  "i2v_negative_prompt_en",
  "composition_ru",
  "camera_angle_ru",
  "subject_lock_ru",
  "background_lock_ru",
  "continuity_from_previous_scene_ru",
  "must_keep_same_ru",
  "allowed_variation_ru",
];

const MANUAL_TIMING_SECTION_LABELS_RU = {
  intro: "вступление",
  verse: "куплет",
  chorus: "припев",
  bridge: "бридж",
  instrumental: "проигрыш",
  outro: "финал",
};

function sectionLabelRu(section = "") {
  const key = String(section || "").trim().toLowerCase();
  return MANUAL_TIMING_SECTION_LABELS_RU[key] || key || "не указана";
}

export function readManualTimingJsonStorage(key) {
  return readManualProjectJsonStorage(key);
}

function isValidManualTimingProject(project) {
  const runtimeType = String(project?.project_runtime_type || "").trim();
  if (runtimeType && runtimeType !== "manual_timing") return false;
  if (
    project?.board_mode !== undefined
    || project?.quick_board === true
    || project?.ownerNodeType !== undefined
  ) return false;
  return true;
}

export function readManualTimingProjectForNode(nodeId = "") {
  const safeId = String(nodeId || "").trim();

  function pickProject(project, source) {
    console.info("[MANUAL TIMING READ PICKED]", {
      source,
      nodeId: String(project?.nodeId || "").trim(),
      candidateNodeId: String(project?.nodeId || project?.sourceNodeId || project?.ownerNodeId || "").trim(),
      projectId: String(project?.project_id || project?.projectId || "").trim(),
      scenesCount: Array.isArray(project?.scenes) ? project.scenes.length : 0,
      audioDurationSec: Number(project?.audio_duration_sec || project?.audioDurationSec || 0),
    });
    return project;
  }

  function readCandidate(source, key) {
    const project = readManualTimingJsonStorage(key);
    const candidateNodeId = String(
      project?.nodeId || project?.sourceNodeId || project?.ownerNodeId || ""
    ).trim();
    if (!project || (safeId && candidateNodeId !== safeId)) return null;
    if (isValidManualTimingProject(project)) return pickProject(project, source);
    console.warn("[MANUAL TIMING READ REJECTED_NON_TIMING_PROJECT]", {
      source,
      runtimeType: String(project?.project_runtime_type || "").trim(),
      projectId: String(project?.project_id || project?.projectId || "").trim(),
      nodeId: String(project?.nodeId || "").trim(),
      candidateNodeId: String(project?.nodeId || project?.sourceNodeId || project?.ownerNodeId || "").trim(),
      boardMode: project?.board_mode,
      quickBoard: project?.quick_board === true,
    });
    return null;
  }

  const readOrder = safeId
    ? [
      ["scoped", getAccountScopedStorageKey(getManualTimingProjectStorageKey(safeId))],
      ["active", getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_KEY)],
    ]
    : [
      ["active", getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_KEY)],
    ];

  if (canUseLegacyManualProjectStorage()) {
    if (safeId) {
      readOrder.push(["legacyScoped", getManualTimingProjectStorageKey(safeId)]);
      readOrder.push(["legacyActive", MANUAL_TIMING_ACTIVE_PROJECT_KEY]);
    } else {
      readOrder.push(["legacyActive", MANUAL_TIMING_ACTIVE_PROJECT_KEY]);
    }
  }

  for (const [source, key] of readOrder) {
    const project = readCandidate(source, key);
    if (project) return project;
  }

  return null;
}

export function persistManualTimingProject(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const runtimeType = String(safeProject?.project_runtime_type || "").trim();
  if (runtimeType && runtimeType !== "manual_timing") {
    console.warn("[MANUAL TIMING PERSIST REJECTED RUNTIME TYPE]", {
      runtimeType,
      projectId: String(safeProject?.project_id || safeProject?.projectId || "").trim(),
    });
    return;
  }
  const projectToPersist = {
    ...safeProject,
    project_runtime_type: "manual_timing",
  };
  try {
    const serialized = JSON.stringify(projectToPersist);
    localStorage.setItem(getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_KEY), serialized);
    const nodeId = String(projectToPersist?.nodeId || "").trim();
    if (nodeId) {
      localStorage.setItem(getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY), nodeId);
      localStorage.setItem(getAccountScopedStorageKey(getManualTimingProjectStorageKey(nodeId)), serialized);
    }
  } catch {}
}

export function removeManualTimingProjectForNode(nodeId = "") {
  const safeId = String(nodeId || "").trim();
  try {
    if (safeId) {
      localStorage.removeItem(getAccountScopedStorageKey(getManualTimingProjectStorageKey(safeId)));
      if (canUseLegacyManualProjectStorage()) localStorage.removeItem(getManualTimingProjectStorageKey(safeId));
    }
    const active = readManualTimingJsonStorage(getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_KEY))
      || (canUseLegacyManualProjectStorage() ? readManualTimingJsonStorage(MANUAL_TIMING_ACTIVE_PROJECT_KEY) : null);
    if (!safeId || String(active?.nodeId || "") === safeId) {
      localStorage.removeItem(getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_KEY));
      localStorage.removeItem(getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY));
      if (canUseLegacyManualProjectStorage()) {
        localStorage.removeItem(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
        localStorage.removeItem(MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY);
      }
    }
  } catch {}
}

export function getDefaultManualTimingNodeData() {
  return {
    mode: MANUAL_TIMING_MODE,
    project_mode: "",
    project_kind: "",
    format: "9:16",
    aspect_ratio: "9:16",
    format_locked: false,
    audio: {
      url: "",
      filename: "",
      duration_sec: 0,
      duration_ms: 0,
    },
    audio_source: "",
    vocal_asr_source: null,
    vocal_asr_language: "auto",
    vocal_asr_split_preset: "song_lines",
    vocal_asr_gaps: [],
    timing_status: "empty",
    markers: [],
    story_blocks: [MANUAL_TIMING_UNKNOWN_STORY_BLOCK],
    audio_phrases: [],
    scenes: [],
    manual_timing_workflow: normalizeManualTimingWorkflow(),
    manual_scene_edits: false,
    manualSceneEdits: false,
    lastManualEditReason: "",
    ...pickManualTimingProjectStoryBibleFields(),
    selectedSceneId: "",
    updatedAt: 0,
  };
}


function pickManualTimingModeAndKind(raw = {}, base = {}) {
  const rawMode = String(raw?.project_mode || raw?.projectMode || "").trim();
  const rawKind = String(raw?.project_kind || raw?.projectKind || "").trim();
  if (rawMode || rawKind) return { project_mode: rawMode, project_kind: rawKind };

  const baseMode = String(base?.project_mode || base?.projectMode || "").trim();
  const baseKind = String(base?.project_kind || base?.projectKind || "").trim();
  if (baseMode || baseKind) return { project_mode: baseMode, project_kind: baseKind };

  const splitType = String(raw?.split_type || raw?.splitType || "").toLowerCase();
  const task = typeof raw?.chatgpt_task === "string" ? raw.chatgpt_task : JSON.stringify(raw?.chatgpt_task || "");
  if (splitType.includes("clip") || task.includes("Music Clip Pass") || Array.isArray(raw?.song_blocks)) {
    return {
      project_mode: MANUAL_TIMING_MUSIC_CLIP_MODE,
      project_kind: MANUAL_TIMING_MUSIC_CLIP_PROJECT_KIND,
    };
  }

  if (splitType.includes("podcast") || task.includes("Podcast / Dialogue Pass") || Array.isArray(raw?.speakers) || Array.isArray(raw?.topic_blocks)) {
    return {
      project_mode: MANUAL_TIMING_PODCAST_DIALOGUE_MODE,
      project_kind: MANUAL_TIMING_PODCAST_DIALOGUE_PROJECT_KIND,
    };
  }

  const hasStoryImportShape = splitType.includes("story")
    || splitType.includes("asr")
    || task.includes("Story Pass")
    || task.includes("story_blocks")
    || task.includes("ASR")
    || Array.isArray(raw?.story_blocks)
    || Array.isArray(raw?.storyBlocks)
    || Array.isArray(raw?.audio_phrases)
    || Array.isArray(raw?.audioPhrases);

  if (hasStoryImportShape) {
    return {
      project_mode: MANUAL_TIMING_STORY_VOICEOVER_MODE,
      project_kind: MANUAL_TIMING_STORY_PROJECT_KIND,
    };
  }

  return { project_mode: "", project_kind: "" };
}

export function getManualTimingProjectKindForMode(mode = "") {
  const value = String(mode || "").trim();
  if (value === MANUAL_TIMING_STORY_VOICEOVER_MODE) return MANUAL_TIMING_STORY_PROJECT_KIND;
  if (value === MANUAL_TIMING_MUSIC_CLIP_MODE) return MANUAL_TIMING_MUSIC_CLIP_PROJECT_KIND;
  if (value === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) return MANUAL_TIMING_PODCAST_DIALOGUE_PROJECT_KIND;
  return "";
}

export function normalizeManualTimingAudio(audio = null) {
  if (!audio || typeof audio !== "object") return { url: "", filename: "", duration_sec: 0, duration_ms: 0, mime_type: "", source: "" };
  const url = String(audio.url || audio.value || audio.href || "").trim();
  const filename = String(audio.filename || audio.fileName || audio.name || audio.preview || audio.meta?.filename || "").trim();
  const mime_type = String(audio.mime_type || audio.mimeType || audio.type || audio.meta?.mime_type || audio.meta?.mimeType || "").trim();
  const source = String(audio.source || audio.audio_source || audio.audioSource || audio.meta?.source || "").trim();
  const durationSecRaw = Number(
    audio.duration_sec
    ?? audio.durationSec
    ?? audio.audioDurationSec
    ?? audio.duration
    ?? audio.meta?.duration_sec
    ?? audio.meta?.durationSec
    ?? audio.meta?.audioDurationSec
    ?? audio.meta?.duration
    ?? 0
  );
  const durationMsRaw = Number(audio.duration_ms ?? audio.durationMs ?? audio.meta?.duration_ms ?? 0);
  const duration_sec = Number.isFinite(durationSecRaw) ? Number(durationSecRaw.toFixed(3)) : 0;
  const duration_ms = Number.isFinite(durationMsRaw) && durationMsRaw > 0 ? Math.round(durationMsRaw) : Math.round(duration_sec * 1000);
  return { url, filename, duration_sec, duration_ms, mime_type, source };
}

export function getManualTimingAudioSignature(projectOrAudio = {}) {
  const value = projectOrAudio && typeof projectOrAudio === "object" ? projectOrAudio : {};
  const audioCandidate = value.audio || value.audio_metadata || value.audioMetadata || value;
  const audio = normalizeManualTimingAudio({
    ...audioCandidate,
    url: audioCandidate?.url || value.audio_url || value.audioUrl || value.source_audio_url || value.sourceAudioUrl,
    filename: audioCandidate?.filename || audioCandidate?.name || value.audio_filename || value.audioFilename || value.audio_name || value.audioName,
    name: audioCandidate?.name || audioCandidate?.filename || value.audio_name || value.audioName || value.audio_filename || value.audioFilename,
    duration_sec: audioCandidate?.duration_sec
      ?? audioCandidate?.durationSec
      ?? value.audio_duration_sec
      ?? value.audioDurationSec
      ?? value.duration_sec
      ?? value.durationSec,
  });
  const audioName = String(
    audioCandidate?.filename
    || audioCandidate?.name
    || value.audio_filename
    || value.audioFilename
    || value.audio_name
    || value.audioName
    || audio.filename
    || ""
  ).trim();
  const signature = {
    audio_url: String(audio.url || "").trim(),
    audio_name: audioName,
    audio_duration_sec: roundTimingSec(audio.duration_sec),
    project_mode: String(value.project_mode || value.projectMode || "").trim(),
    project_kind: String(value.project_kind || value.projectKind || "").trim(),
    format: String(value.format || value.aspect_ratio || value.aspectRatio || "").trim(),
    aspect_ratio: String(value.aspect_ratio || value.aspectRatio || value.format || "").trim(),
  };
  return JSON.stringify(signature);
}

export function roundTimingSec(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return 0;
  return Number(n.toFixed(3));
}

export function formatTimingSec(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0:00.000";
  const minutes = Math.floor(n / 60);
  const seconds = Math.floor(n % 60);
  const millis = Math.round((n - Math.floor(n)) * 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function normalizeManualTimingSection(section = "") {
  const value = String(section || "").trim().toLowerCase();
  return MANUAL_TIMING_SECTIONS.includes(value) ? value : "verse";
}

export function normalizeManualTimingRoute(route = "") {
  const value = String(route || "").trim().toLowerCase();
  return MANUAL_TIMING_ROUTES.includes(value) ? value : "i2v";
}

export function normalizeManualTimingEnergy(energy = "") {
  const value = String(energy || "").trim().toLowerCase();
  return MANUAL_TIMING_ENERGY.includes(value) ? value : "mid";
}

export function getSectionDefaults(section = "") {
  const normalized = normalizeManualTimingSection(section);
  if (normalized === "intro" || normalized === "instrumental" || normalized === "outro") {
    return {
      section: normalized,
      route: "i2v",
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
    };
  }
  return {
    section: normalized,
    route: "ia2v",
    contains_vocal: true,
    contains_vocal_assumption: true,
    contains_instrumental_assumption: false,
  };
}


function normalizeStoryBlockColor(value = "") {
  const raw = String(value || "").trim();
  return /^#[0-9a-f]{6}$/i.test(raw) || /^#[0-9a-f]{3}$/i.test(raw) ? raw : MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color;
}

export function normalizeManualTimingAudioPhrases(audioPhrases = []) {
  const rawPhrases = Array.isArray(audioPhrases) ? audioPhrases : [];
  const seen = new Set();

  return rawPhrases
    .map((phrase, idx) => {
      const rawId = String(phrase?.phrase_id || phrase?.phraseId || phrase?.id || "").trim();
      const phrase_id = rawId || `manual_missing_${String(idx + 1).padStart(3, "0")}`;
      if (seen.has(phrase_id)) return null;
      seen.add(phrase_id);

      const start = roundTimingSec(phrase?.start_sec ?? phrase?.startSec ?? phrase?.start ?? 0);
      const end = roundTimingSec(phrase?.end_sec ?? phrase?.endSec ?? phrase?.end ?? start);
      if (!(end > start)) return null;

      return {
        phrase_id,
        start_sec: start,
        end_sec: end,
        text_original: String(phrase?.text_original || phrase?.textOriginal || phrase?.original_text || phrase?.originalText || ""),
        original_text: String(phrase?.original_text || phrase?.originalText || phrase?.text_original || phrase?.textOriginal || ""),
        text: String(phrase?.text || ""),
        text_en: String(phrase?.text_en || phrase?.textEn || ""),
        text_de: String(phrase?.text_de || phrase?.textDe || ""),
        text_fr: String(phrase?.text_fr || phrase?.textFr || ""),
        text_ru: String(phrase?.text_ru || phrase?.textRu || phrase?.translation_ru || phrase?.translationRu || ""),
        translation_ru: String(phrase?.translation_ru || phrase?.translationRu || phrase?.text_ru || phrase?.textRu || ""),
        meaning_ru: String(phrase?.meaning_ru || phrase?.meaningRu || ""),
        status: String(phrase?.status || "needs_transcription"),
        assignment_status: String(phrase?.assignment_status || phrase?.assignmentStatus || (String(phrase?.status || "needs_transcription") === "needs_transcription" ? "unassigned" : "")),
        confidence: Number.isFinite(Number(phrase?.confidence)) ? Number(Number(phrase.confidence).toFixed(4)) : 0,
        source: String(phrase?.source || phrase?.timing_source || phrase?.timingSource || (String(phrase?.status || "") === "asr_raw" ? "asr" : "")),
        source_language: String(phrase?.source_language || phrase?.sourceLanguage || phrase?.language || ""),
        language: String(phrase?.language || phrase?.source_language || phrase?.sourceLanguage || ""),
        note_ru: String(phrase?.note_ru || phrase?.noteRu || ""),
      };
    })
    .filter(Boolean);
}

export function normalizeManualTimingSourcePhraseIds(value = []) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((id) => String(id || "").trim()).filter(Boolean))];
}

export const MANUAL_TIMING_NEEDS_TRANSCRIPTION_RULE_RU = "Если audio_phrases содержит status='needs_transcription' или assignment_status='unassigned', это пропущенные фразы аудио. Не удаляй их. Нужно распознать/перевести эти фразы по аудио или предоставленному тексту, заполнить text_en, text_ru, meaning_ru и решить, куда их вставить: в предыдущую сцену, следующую сцену или отдельную новую сцену. Не менять тайминги без явного указания пользователя; если нужна новая сцена, предложить это явно.";
export const MANUAL_TIMING_ASR_SOURCE_OF_TRUTH_RULE_RU = "Локальный ASR/faster-whisper не должен понимать смысл истории и не должен делать финальные story_blocks: он возвращает только audio_phrases, word timestamps и точные start_sec/end_sec речи. Gap-aware builder локально собирает scenes с source_phrase_ids, continuous coverage от 0 до audio_duration_sec, speech_start_sec/speech_end_sec, pre_silence_sec/post_silence_sec и технические draft blocks. LLM Story Pass делает только перевод, meaning_hint_ru, story_blocks по смыслу, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru, scene_role_in_block_ru и block_progress_ru; video_prompt, negative_prompt, sound_prompt оставить пустыми. Не менять audio_phrases, scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec и source_phrase_ids. Если кажется, что scenes нужно объединить или разделить, не делать это самостоятельно: заполнить user_note_ru с предложением, а тайминги оставить без изменений.";

export function buildManualTimingChatGptTask(hasUnresolvedAudioPhrases = false, hasAsrAudioPhrases = false) {
  const rules = Array.isArray(CHATGPT_STORY_SPLIT_TASK.rules_ru) ? CHATGPT_STORY_SPLIT_TASK.rules_ru : [];
  const nextRules = [...rules];
  if (hasUnresolvedAudioPhrases && !nextRules.includes(MANUAL_TIMING_NEEDS_TRANSCRIPTION_RULE_RU)) {
    nextRules.push(MANUAL_TIMING_NEEDS_TRANSCRIPTION_RULE_RU);
  }
  if (hasAsrAudioPhrases && !nextRules.includes(MANUAL_TIMING_ASR_SOURCE_OF_TRUTH_RULE_RU)) {
    nextRules.push(MANUAL_TIMING_ASR_SOURCE_OF_TRUTH_RULE_RU);
  }
  if (nextRules.length === rules.length) return CHATGPT_STORY_SPLIT_TASK;
  return {
    ...CHATGPT_STORY_SPLIT_TASK,
    rules_ru: nextRules,
  };
}

function rangesIntersect(aStart, aEnd, bStart, bEnd) {
  return Number(aStart) < Number(bEnd) - 0.001 && Number(aEnd) > Number(bStart) + 0.001;
}

function isManualTimingSilenceSceneLike(scene = {}) {
  return Boolean(scene?.is_silence || scene?.isSilence || scene?.is_virtual_silence || scene?.isVirtualSilence || scene?.scene_type === "manual_silence" || scene?.source_kind === "silence" || scene?.sourceKind === "silence");
}

function getManualTimingSceneSourceStartSec(scene = {}) {
  if (isManualTimingSilenceSceneLike(scene)) return null;
  const explicit = Number(scene?.source_start_sec ?? scene?.sourceStartSec);
  if (Number.isFinite(explicit)) return roundTimingSec(explicit);
  return roundTimingSec(scene?.start_sec);
}

function getManualTimingSceneSourceEndSec(scene = {}) {
  if (isManualTimingSilenceSceneLike(scene)) return null;
  const explicit = Number(scene?.source_end_sec ?? scene?.sourceEndSec);
  if (Number.isFinite(explicit)) return roundTimingSec(explicit);
  const sourceStart = getManualTimingSceneSourceStartSec(scene);
  return roundTimingSec(Number(sourceStart || 0) + Math.max(0, Number(scene?.end_sec || 0) - Number(scene?.start_sec || 0)));
}

export function getManualTimingPhrasesForScene(audioPhrases = [], scene = null) {
  if (!scene || isManualTimingSilenceSceneLike(scene)) return [];
  const sceneStart = Number(getManualTimingSceneSourceStartSec(scene));
  const sceneEnd = Number(getManualTimingSceneSourceEndSec(scene));
  if (!(sceneEnd > sceneStart)) return [];
  return normalizeManualTimingAudioPhrases(audioPhrases).filter((phrase) => rangesIntersect(phrase.start_sec, phrase.end_sec, sceneStart, sceneEnd));
}

export function normalizeManualTimingStoryBlocks(storyBlocks = []) {
  const rawBlocks = Array.isArray(storyBlocks) ? storyBlocks : [];
  const seen = new Set();
  const blocks = rawBlocks
    .map((block, idx) => {
      const rawId = String(block?.block_id || block?.blockId || block?.id || "").trim();
      const block_id = rawId || (idx === 0 ? MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id : `block_${idx + 1}`);
      if (seen.has(block_id)) return null;
      seen.add(block_id);
      return {
        block_id,
        title_ru: String(block?.title_ru || block?.titleRu || block?.title || (block_id === MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id ? MANUAL_TIMING_UNKNOWN_STORY_BLOCK.title_ru : block_id)),
        summary_ru: String(block?.summary_ru || block?.summaryRu || block?.summary || ""),
        block_goal_ru: String(block?.block_goal_ru || block?.blockGoalRu || block?.goal_ru || ""),
        block_reveal_ru: String(block?.block_reveal_ru || block?.blockRevealRu || block?.reveal_ru || ""),
        block_emotion_ru: String(block?.block_emotion_ru || block?.blockEmotionRu || block?.emotion_ru || ""),
        global_story_context_ru: String(block?.global_story_context_ru || block?.globalStoryContextRu || ""),
        block_place_in_global_arc_ru: String(block?.block_place_in_global_arc_ru || block?.blockPlaceInGlobalArcRu || ""),
        inherits_style_lock_ru: String(block?.inherits_style_lock_ru || block?.inheritsStyleLockRu || ""),
        inherits_world_lock_ru: String(block?.inherits_world_lock_ru || block?.inheritsWorldLockRu || ""),
        inherits_atmosphere_ru: String(block?.inherits_atmosphere_ru || block?.inheritsAtmosphereRu || ""),
        inherits_continuity_rules_ru: String(block?.inherits_continuity_rules_ru || block?.inheritsContinuityRulesRu || ""),
        block_visual_bible_ru: String(block?.block_visual_bible_ru || block?.blockVisualBibleRu || ""),
        block_style_lock_ru: String(block?.block_style_lock_ru || block?.blockStyleLockRu || ""),
        block_location_lock_ru: String(block?.block_location_lock_ru || block?.blockLocationLockRu || ""),
        block_time_of_day_ru: String(block?.block_time_of_day_ru || block?.blockTimeOfDayRu || ""),
        block_color_palette_ru: String(block?.block_color_palette_ru || block?.blockColorPaletteRu || ""),
        block_camera_language_ru: String(block?.block_camera_language_ru || block?.blockCameraLanguageRu || ""),
        block_continuity_rules_ru: String(block?.block_continuity_rules_ru || block?.blockContinuityRulesRu || ""),
        block_storyboard_summary_ru: String(block?.block_storyboard_summary_ru || block?.blockStoryboardSummaryRu || ""),
        block_reference_frame_prompt_en: String(block?.block_reference_frame_prompt_en || block?.blockReferenceFramePromptEn || ""),
        color: normalizeStoryBlockColor(block?.color || (block_id === MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id ? MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color : "")),
        scene_ids: Array.isArray(block?.scene_ids || block?.sceneIds)
          ? (block?.scene_ids || block?.sceneIds).map((id) => String(id || "").trim()).filter(Boolean)
          : [],
        start_sec: roundTimingSec(block?.start_sec ?? block?.startSec ?? 0),
        end_sec: roundTimingSec(block?.end_sec ?? block?.endSec ?? 0),
      };
    })
    .filter(Boolean);

  if (!blocks.some((block) => block.block_id === MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id)) {
    blocks.push({ ...MANUAL_TIMING_UNKNOWN_STORY_BLOCK });
  }
  return blocks.length ? blocks : [{ ...MANUAL_TIMING_UNKNOWN_STORY_BLOCK }];
}


export function deriveStoryBlockRangeFromScenes(block, scenes = []) {
  const blockId = String(block?.block_id || "").trim();
  const safeScenes = Array.isArray(scenes) ? scenes : [];
  const sceneIds = Array.isArray(block?.scene_ids) ? block.scene_ids.map((sceneId) => String(sceneId || "").trim()).filter(Boolean) : [];

  let blockScenes = [];

  if (sceneIds.length) {
    blockScenes = sceneIds
      .map((sceneId) => safeScenes.find((scene) => String(scene?.scene_id || "") === String(sceneId)))
      .filter((scene) => scene && (!blockId || !scene?.story_block_id || String(scene.story_block_id || "") === blockId));
  }

  if (!blockScenes.length) {
    blockScenes = safeScenes.filter((scene) => String(scene?.story_block_id || "") === blockId);
  }

  if (!blockScenes.length) return null;

  const starts = blockScenes.map((scene) => Number(scene?.start_sec || 0)).filter(Number.isFinite);
  const ends = blockScenes.map((scene) => Number(scene?.end_sec || 0)).filter(Number.isFinite);
  if (!starts.length || !ends.length) return null;

  return {
    ...block,
    start_sec: roundTimingSec(Math.min(...starts)),
    end_sec: roundTimingSec(Math.max(...ends)),
    scene_ids: blockScenes.map((scene) => scene.scene_id),
    scene_count: blockScenes.length,
  };
}

export function syncManualTimingStoryBlocksWithScenes(storyBlocks = [], scenes = []) {
  const normalizedBlocks = normalizeManualTimingStoryBlocks(storyBlocks);
  const safeScenes = Array.isArray(scenes) ? scenes : [];

  return normalizedBlocks.map((block) => {
    const derived = deriveStoryBlockRangeFromScenes(block, safeScenes);
    if (derived) return derived;
    return {
      ...block,
      scene_ids: [],
      start_sec: 0,
      end_sec: 0,
      scene_count: 0,
    };
  });
}

export function hydrateManualTimingScenesWithStoryBlocks(scenes = [], storyBlocks = []) {
  const blocks = normalizeManualTimingStoryBlocks(storyBlocks);
  const blockById = new Map(blocks.map((block) => [String(block.block_id), block]));
  const blockIdBySceneId = new Map();
  blocks.forEach((block) => {
    (Array.isArray(block.scene_ids) ? block.scene_ids : []).forEach((sceneId) => {
      const safeSceneId = String(sceneId || "").trim();
      if (safeSceneId && !blockIdBySceneId.has(safeSceneId)) blockIdBySceneId.set(safeSceneId, block.block_id);
    });
  });

  return (Array.isArray(scenes) ? scenes : []).map((scene) => {
    const sceneId = String(scene?.scene_id || "").trim();
    const storyBlockId = String(scene?.story_block_id || blockIdBySceneId.get(sceneId) || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id).trim() || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id;
    const block = blockById.get(storyBlockId) || blockById.get(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id) || MANUAL_TIMING_UNKNOWN_STORY_BLOCK;
    const sceneIds = Array.isArray(block.scene_ids) ? block.scene_ids.map((id) => String(id || "").trim()).filter(Boolean) : [];
    const positionIdx = sceneIds.indexOf(sceneId);
    const computedPosition = positionIdx >= 0 && sceneIds.length
      ? `сцена ${positionIdx + 1} из ${sceneIds.length} в блоке`
      : "";
    return {
      ...scene,
      story_block_id: storyBlockId,
      story_block_title_ru: String(scene?.story_block_title_ru || block.title_ru || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.title_ru),
      story_block_color: normalizeStoryBlockColor(scene?.story_block_color || block.color || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color),
      story_block_position_ru: String(scene?.story_block_position_ru || computedPosition),
      story_block_goal_ru: String(scene?.story_block_goal_ru || block.block_goal_ru || ""),
      story_block_reveal_ru: String(scene?.story_block_reveal_ru || block.block_reveal_ru || ""),
      story_block_emotion_ru: String(scene?.story_block_emotion_ru || block.block_emotion_ru || ""),
    };
  });
}

export function normalizeManualTimingMarkers(markers = [], durationSec = 0) {
  const duration = roundTimingSec(durationSec);
  const values = (Array.isArray(markers) ? markers : [])
    .map((value) => roundTimingSec(value))
    .filter((value) => Number.isFinite(value) && value >= 0 && (!duration || value <= duration));

  values.push(0);
  if (duration > 0) values.push(duration);

  const sorted = [...new Set(values.map((value) => value.toFixed(3)))]
    .map((value) => Number(value))
    .sort((a, b) => a - b);

  const deduped = [];
  for (const value of sorted) {
    const prev = deduped[deduped.length - 1];
    if (prev === undefined || Math.abs(value - prev) >= 0.001) deduped.push(value);
  }
  return deduped;
}

function buildScenePreserveMaps(existingScenes = []) {
  const byId = new Map();
  const byTimeline = new Map();
  (Array.isArray(existingScenes) ? existingScenes : []).forEach((scene) => {
    const id = String(scene?.scene_id || "").trim();
    if (id && !byId.has(id)) byId.set(id, scene);
    const key = `${roundTimingSec(scene?.start_sec).toFixed(3)}|${roundTimingSec(scene?.end_sec).toFixed(3)}`;
    if (key !== "0.000|0.000" && !byTimeline.has(key)) byTimeline.set(key, scene);
  });
  return { byId, byTimeline };
}

export function getRangeOverlap(aStart, aEnd, bStart, bEnd) {
  return Math.max(0, Math.min(aEnd, bEnd) - Math.max(aStart, bStart));
}

export function findBestSceneByTimelineOverlap(start, end, existingScenes = [], usedOldSceneIds = new Set()) {
  const newDuration = Math.max(0.001, end - start);
  let best = null;

  (Array.isArray(existingScenes) ? existingScenes : []).forEach((scene) => {
    const oldId = String(scene?.scene_id || "");
    if (oldId && usedOldSceneIds.has(oldId)) return;

    const oldStart = Number(scene?.start_sec || 0);
    const oldEnd = Number(scene?.end_sec || 0);
    if (!(oldEnd > oldStart)) return;

    const overlap = getRangeOverlap(start, end, oldStart, oldEnd);
    if (overlap <= 0) return;

    const oldDuration = Math.max(0.001, oldEnd - oldStart);
    const newRatio = overlap / newDuration;
    const oldRatio = overlap / oldDuration;

    const score = Math.max(newRatio, oldRatio);

    if (!best || score > best.score) {
      best = { scene, score, newRatio, oldRatio, overlap };
    }
  });

  if (!best) return null;

  // Порог: сцена считается той же самой, если overlap достаточно большой.
  // Для чуть сдвинутых границ подходит.
  // Для новой короткой сцены после split не нужно воровать чужой текст бездумно.
  if (best.score < 0.55) return null;

  return best;
}

const MANUAL_TIMING_SPLIT_REVIEW_NOTE_RU = "Новая сцена после разреза — проверь текст/смысл";

export function buildManualTimingScenesFromMarkers(markers = [], existingScenes = [], options = {}) {
  const duration = Number(options.durationSec || 0);
  const safeMarkers = normalizeManualTimingMarkers(markers, duration);
  const safeExistingScenes = Array.isArray(existingScenes) ? existingScenes : [];
  const { byId, byTimeline } = buildScenePreserveMaps(safeExistingScenes);
  const scenes = [];
  const usedOldSceneIds = new Set();
  const sceneCountChanged = safeExistingScenes.length !== Math.max(0, safeMarkers.length - 1);
  const canUseIdFallback = options.allowIdFallback === true && !sceneCountChanged;

  for (let i = 0; i < safeMarkers.length - 1; i += 1) {
    const start = roundTimingSec(safeMarkers[i]);
    const end = roundTimingSec(safeMarkers[i + 1]);
    if (!(end > start)) continue;
    const sceneId = `seg_${String(i + 1).padStart(2, "0")}`;
    const timelineKey = `${start.toFixed(3)}|${end.toFixed(3)}`;
    let old = null;
    let matchType = "none";
    let overlapMatch = null;

    const exactOld = byTimeline.get(timelineKey) || null;
    if (exactOld) {
      old = exactOld;
      matchType = "exact";
    } else {
      overlapMatch = findBestSceneByTimelineOverlap(start, end, safeExistingScenes, usedOldSceneIds);
      if (overlapMatch?.scene) {
        old = overlapMatch.scene;
        matchType = "overlap";
      }
    }

    if (!old && canUseIdFallback) {
      old = byId.get(sceneId) || null;
      if (old) matchType = "id";
    }

    const shouldCarryTechnicalFields = Boolean(old);
    const canCarryStoryFields =
      matchType === "exact"
      || matchType === "id"
      || (
        matchType === "overlap"
        && overlapMatch
        && overlapMatch.newRatio >= 0.82
        && overlapMatch.oldRatio >= 0.82
      );
    if (old?.scene_id) usedOldSceneIds.add(String(old.scene_id));
    old = old || {};
    const needsSplitReviewNote = matchType === "overlap" && !canCarryStoryFields;
    const section = normalizeManualTimingSection(shouldCarryTechnicalFields ? old.section : (i === 0 ? "intro" : "verse"));
    const defaults = getSectionDefaults(section);
    const route = normalizeManualTimingRoute(shouldCarryTechnicalFields ? old.route : defaults.route);
    const containsVocal = shouldCarryTechnicalFields && typeof old.contains_vocal === "boolean"
      ? old.contains_vocal
      : Boolean((shouldCarryTechnicalFields ? old.contains_vocal_assumption : undefined) ?? defaults.contains_vocal);
    const containsInstrumental = shouldCarryTechnicalFields && typeof old.contains_instrumental === "boolean"
      ? old.contains_instrumental
      : Boolean((shouldCarryTechnicalFields ? old.contains_instrumental_assumption : undefined) ?? !containsVocal);
    const userNoteRu = canCarryStoryFields
      ? String(old.user_note_ru || old.user_notes_ru || "")
      : (needsSplitReviewNote ? MANUAL_TIMING_SPLIT_REVIEW_NOTE_RU : "");
    const speechStart = shouldCarryTechnicalFields
      ? roundTimingSec(old.speech_start_sec ?? old.speechStartSec ?? start)
      : start;
    const speechEnd = shouldCarryTechnicalFields
      ? roundTimingSec(old.speech_end_sec ?? old.speechEndSec ?? end)
      : end;
    const preSilence = roundTimingSec(Math.max(0, speechStart - start));
    const postSilence = roundTimingSec(Math.max(0, end - speechEnd));

    scenes.push({
      scene_id: options.preserveSceneIds ? String(old.scene_id || sceneId) : sceneId,
      index: i + 1,
      start_sec: start,
      end_sec: end,
      duration_sec: roundTimingSec(end - start),
      source_kind: String(old.source_kind || old.sourceKind || "audio"),
      source_start_sec: old.source_start_sec ?? old.sourceStartSec ?? null,
      source_end_sec: old.source_end_sec ?? old.sourceEndSec ?? null,
      is_silence: Boolean(old.is_silence || old.isSilence || old.scene_type === "manual_silence"),
      composer_source_kind: String(old.composer_source_kind || old.composerSourceKind || ""),
      composer_source_audio_id: String(old.composer_source_audio_id || old.composerSourceAudioId || ""),
      composer_source_audio_name: String(old.composer_source_audio_name || old.composerSourceAudioName || ""),
      composer_source_start_sec: old.composer_source_start_sec ?? old.composerSourceStartSec ?? null,
      composer_source_end_sec: old.composer_source_end_sec ?? old.composerSourceEndSec ?? null,
      composer_block_id: String(old.composer_block_id || old.composerBlockId || ""),
      composer_block_type: String(old.composer_block_type || old.composerBlockType || ""),
      composer_block_label: String(old.composer_block_label || old.composerBlockLabel || ""),
      composer_role_label: String(old.composer_role_label || old.composerRoleLabel || ""),
      composer_saved_clip_id: String(old.composer_saved_clip_id || old.composerSavedClipId || ""),
      composer_saved_clip_label: String(old.composer_saved_clip_label || old.composerSavedClipLabel || ""),
      speech_start_sec: speechStart,
      speech_end_sec: speechEnd,
      pre_silence_sec: preSilence,
      post_silence_sec: postSilence,
      section,
      route,
      contains_vocal: containsVocal,
      contains_vocal_assumption: Boolean((shouldCarryTechnicalFields ? old.contains_vocal_assumption : undefined) ?? containsVocal),
      contains_instrumental_assumption: Boolean((shouldCarryTechnicalFields ? old.contains_instrumental_assumption : undefined) ?? containsInstrumental),
      use_sound_suggestion: shouldCarryTechnicalFields ? Boolean(old.use_sound_suggestion || false) : false,
      energy: normalizeManualTimingEnergy(shouldCarryTechnicalFields ? old.energy : "mid"),
      quality: String(old.quality || "manual_draft"),
      boundary_reason: String(old.boundary_reason || "manual_marker"),
      transition_out: String(old.transition_out || "manual_cut"),
      story_time: canCarryStoryFields ? String(old.story_time || "") : "",
      scene_type: String(old.scene_type || ""),
      drama_hint: canCarryStoryFields ? String(old.drama_hint || "") : "",
      short_note: canCarryStoryFields ? String(old.short_note || "") : "",
      scene_goal_ru: canCarryStoryFields ? String(old.scene_goal_ru || "") : "",
      photo_prompt_hint_ru: canCarryStoryFields ? String(old.photo_prompt_hint_ru || "") : "",
      prompt_hint_ru: canCarryStoryFields ? String(old.prompt_hint_ru || old.photo_prompt_hint_ru || "") : "",
      story_position_ru: canCarryStoryFields ? String(old.story_position_ru || old.story_time || "") : "",
      user_note_ru: userNoteRu,
      source_phrase_ids: canCarryStoryFields ? normalizeManualTimingSourcePhraseIds(old.source_phrase_ids || old.sourcePhraseIds) : [],
      story_block_id: String(shouldCarryTechnicalFields ? (old.story_block_id || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id) : MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id),
      story_block_title_ru: shouldCarryTechnicalFields ? String(old.story_block_title_ru || "") : "",
      story_block_color: shouldCarryTechnicalFields ? String(old.story_block_color || "") : "",
      story_block_position_ru: canCarryStoryFields ? String(old.story_block_position_ru || "") : "",
      scene_role_in_block_ru: canCarryStoryFields ? String(old.scene_role_in_block_ru || "") : "",
      block_progress_ru: canCarryStoryFields ? String(old.block_progress_ru || "") : "",
      scene_global_context_ru: canCarryStoryFields ? String(old.scene_global_context_ru || "") : "",
      continuity_anchor_ru: canCarryStoryFields ? String(old.continuity_anchor_ru || "") : "",
      must_match_project_identity_ru: canCarryStoryFields ? String(old.must_match_project_identity_ru || "") : "",
      must_match_block_style_ru: canCarryStoryFields ? String(old.must_match_block_style_ru || "") : "",
      storyboard_frame_role_ru: canCarryStoryFields ? String(old.storyboard_frame_role_ru || "") : "",
      source_image_prompt_en: canCarryStoryFields ? String(old.source_image_prompt_en || "") : "",
      source_image_prompt_ru: canCarryStoryFields ? String(old.source_image_prompt_ru || "") : "",
      source_image_negative_prompt_en: canCarryStoryFields ? String(old.source_image_negative_prompt_en || "") : "",
      i2v_prompt_en: canCarryStoryFields ? String(old.i2v_prompt_en || "") : "",
      i2v_negative_prompt_en: canCarryStoryFields ? String(old.i2v_negative_prompt_en || "") : "",
      composition_ru: canCarryStoryFields ? String(old.composition_ru || "") : "",
      camera_angle_ru: canCarryStoryFields ? String(old.camera_angle_ru || "") : "",
      subject_lock_ru: canCarryStoryFields ? String(old.subject_lock_ru || "") : "",
      background_lock_ru: canCarryStoryFields ? String(old.background_lock_ru || "") : "",
      continuity_from_previous_scene_ru: canCarryStoryFields ? String(old.continuity_from_previous_scene_ru || "") : "",
      must_keep_same_ru: canCarryStoryFields ? String(old.must_keep_same_ru || "") : "",
      allowed_variation_ru: canCarryStoryFields ? String(old.allowed_variation_ru || "") : "",
      original_text: canCarryStoryFields ? String(old.original_text || "") : "",
      translated_text_ru: canCarryStoryFields ? String(old.translated_text_ru || "") : "",
      meaning_hint_ru: canCarryStoryFields ? String(old.meaning_hint_ru || "") : "",
      source_text_en: canCarryStoryFields ? String(old.source_text_en || "") : "",
      adapted_text_en: canCarryStoryFields ? String(old.adapted_text_en || "") : "",
      video_prompt: String(old.video_prompt || ""),
      negative_prompt: String(old.negative_prompt || ""),
      sound_prompt: String(old.sound_prompt || ""),
    });
  }

  return scenes;
}


export function validateSceneCoverage(scenes = [], audioDurationSec = 0) {
  const safeScenes = (Array.isArray(scenes) ? scenes : [])
    .map((scene, idx) => ({ ...scene, __idx: idx, start_sec: roundTimingSec(scene?.start_sec), end_sec: roundTimingSec(scene?.end_sec) }))
    .filter((scene) => Number.isFinite(scene.start_sec) && Number.isFinite(scene.end_sec))
    .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0));
  const duration = roundTimingSec(audioDurationSec);
  const tolerance = 0.01;
  const warnings = [];
  const errors = [];

  if (!safeScenes.length) {
    errors.push("Нет scenes для проверки покрытия аудио.");
    return { ok: false, warnings, errors };
  }

  if (Math.abs(Number(safeScenes[0].start_sec || 0)) > tolerance) {
    errors.push("Первая scene должна начинаться с 0.");
  }
  if (duration > 0 && Math.abs(Number(safeScenes[safeScenes.length - 1].end_sec || 0) - duration) > tolerance) {
    errors.push("Последняя scene должна заканчиваться на audio_duration_sec.");
  }

  safeScenes.forEach((scene, idx) => {
    if (!(Number(scene.end_sec) > Number(scene.start_sec))) {
      errors.push(`${scene.scene_id || `scene_${idx + 1}`}: end_sec должен быть больше start_sec.`);
    }
    if (idx === 0) return;
    const prev = safeScenes[idx - 1];
    const delta = roundTimingSec(Number(scene.start_sec || 0) - Number(prev.end_sec || 0));
    if (delta > tolerance) {
      errors.push(`Есть непокрытый участок аудио между scenes: ${prev.scene_id || idx} → ${scene.scene_id || idx + 1} (${delta.toFixed(3)} сек).`);
    } else if (delta < -tolerance) {
      errors.push(`Scenes перекрываются: ${prev.scene_id || idx} → ${scene.scene_id || idx + 1} (${Math.abs(delta).toFixed(3)} сек).`);
    }
  });

  const coveredSec = safeScenes.reduce((sum, scene) => sum + Math.max(0, Number(scene.end_sec || 0) - Number(scene.start_sec || 0)), 0);
  if (duration > 0 && Math.abs(roundTimingSec(coveredSec) - duration) > Math.max(tolerance, safeScenes.length * tolerance)) {
    warnings.push(`Сумма длительностей scenes (${roundTimingSec(coveredSec).toFixed(3)} сек) не равна полной длительности аудио (${duration.toFixed(3)} сек).`);
  }

  return { ok: errors.length === 0, warnings, errors };
}

function joinPhraseText(phrases = [], key = "text_en") {
  return phrases.map((phrase) => String(phrase?.[key] || "").trim()).filter(Boolean).join(" ");
}

export function buildGapAwareScenesFromAudioPhrases(audioPhrases = [], options = {}) {
  const phrases = normalizeManualTimingAudioPhrases(audioPhrases)
    .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0));
  if (!phrases.length) return [];

  const lastPhraseEnd = Math.max(...phrases.map((phrase) => Number(phrase.end_sec || 0)));
  const audioDurationSec = roundTimingSec(Math.max(Number(options.audioDurationSec || 0), lastPhraseEnd));
  const target = options.targetSceneDurationSec || {};
  const targetMin = Number(target.min || options.minSceneDurationSec || 4);
  const targetPreferred = Number(target.preferred || 6);
  const targetMax = Number(target.max || 9);
  const maxSceneDurationSec = Number(options.maxSceneDurationSec || 10);
  const projectKind = String(options.projectKind || MANUAL_TIMING_STORY_PROJECT_KIND);
  const route = normalizeManualTimingRoute(options.route || "i2v");

  const groups = [];
  let current = [];
  const flush = () => {
    if (current.length) groups.push(current);
    current = [];
  };

  phrases.forEach((phrase, idx) => {
    if (!current.length) {
      current.push(phrase);
      return;
    }

    const first = current[0];
    const prev = current[current.length - 1];
    const nextBoundaryProbe = idx < phrases.length - 1
      ? (Number(phrase.end_sec || 0) + Number(phrases[idx + 1].start_sec || phrase.end_sec || 0)) / 2
      : audioDurationSec;
    const currentBoundaryProbe = (Number(prev.end_sec || 0) + Number(phrase.start_sec || prev.end_sec || 0)) / 2;
    const durationIfAdded = Math.max(0, nextBoundaryProbe - Number(first.start_sec || 0));
    const durationWithout = Math.max(0, currentBoundaryProbe - Number(first.start_sec || 0));
    const gapFromPrev = Math.max(0, Number(phrase.start_sec || 0) - Number(prev.end_sec || 0));
    const shouldSplit = (
      durationWithout >= targetMin
      && (
        durationIfAdded > maxSceneDurationSec
        || durationIfAdded > targetMax
        || (durationWithout >= targetPreferred && gapFromPrev >= 0.35)
      )
    );

    if (shouldSplit) flush();
    current.push(phrase);
  });
  flush();

  const boundaries = [0];
  for (let i = 0; i < groups.length - 1; i += 1) {
    const prevLast = groups[i][groups[i].length - 1];
    const nextFirst = groups[i + 1][0];
    const boundary = roundTimingSec((Number(prevLast.end_sec || 0) + Number(nextFirst.start_sec || prevLast.end_sec || 0)) / 2);
    boundaries.push(Math.max(boundaries[boundaries.length - 1], Math.min(audioDurationSec, boundary)));
  }
  boundaries.push(audioDurationSec);

  return groups.map((group, idx) => {
    const start = roundTimingSec(boundaries[idx]);
    const end = roundTimingSec(boundaries[idx + 1]);
    const speechStart = roundTimingSec(group[0]?.start_sec || start);
    const speechEnd = roundTimingSec(group[group.length - 1]?.end_sec || speechStart);
    const sourcePhraseIds = group.map((phrase) => phrase.phrase_id);
    const storyBlockId = `block_draft_${String(Math.floor(idx / 4) + 1).padStart(2, "0")}`;
    const duration = roundTimingSec(end - start);
    return {
      scene_id: `seg_${String(idx + 1).padStart(2, "0")}`,
      index: idx + 1,
      start_sec: start,
      end_sec: end,
      duration_sec: duration,
      speech_start_sec: speechStart,
      speech_end_sec: speechEnd,
      pre_silence_sec: roundTimingSec(Math.max(0, speechStart - start)),
      post_silence_sec: roundTimingSec(Math.max(0, end - speechEnd)),
      section: idx === 0 ? "intro" : "verse",
      route,
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
      use_sound_suggestion: false,
      energy: "mid",
      quality: "asr_gap_aware_story_draft",
      boundary_reason: "asr_gap_aware_midpoint_pause",
      transition_out: "manual_cut",
      story_time: "",
      scene_type: projectKind === "story" ? "story_scene_from_asr" : "clip_scene_from_asr",
      drama_hint: "",
      short_note: "Gap-aware ASR story scene — паузы включены в монтажную длительность.",
      source_phrase_ids: sourcePhraseIds,
      original_text: joinPhraseText(group, "text_original") || joinPhraseText(group, "original_text") || joinPhraseText(group, "text_en"),
      translated_text_ru: joinPhraseText(group, "translation_ru") || joinPhraseText(group, "text_ru"),
      meaning_hint_ru: joinPhraseText(group, "meaning_ru"),
      story_block_id: storyBlockId,
      story_block_title_ru: "",
      story_block_position_ru: "",
      scene_role_in_block_ru: "",
      block_progress_ru: "",
      scene_global_context_ru: "",
      continuity_anchor_ru: "",
      must_match_project_identity_ru: "",
      must_match_block_style_ru: "",
      scene_goal_ru: "",
      photo_prompt_hint_ru: "",
      prompt_hint_ru: "",
      story_position_ru: "",
      user_note_ru: "Собрано из ASR audio_phrases: scene duration включает паузы; video generation должна использовать duration_sec.",
      source_text_en: joinPhraseText(group, "text_en"),
      adapted_text_en: "",
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: "",
    };
  });
}

export function buildDraftStoryBlocksFromGapAwareScenes(scenes = []) {
  const safeScenes = Array.isArray(scenes) ? scenes : [];
  const colorPalette = ["#2563EB", "#7C3AED", "#059669", "#D97706", "#DC2626", "#0891B2"];
  const blockIds = [...new Set(safeScenes.map((scene) => String(scene?.story_block_id || "").trim()).filter(Boolean))];
  return blockIds.map((blockId, idx) => {
    const blockScenes = safeScenes.filter((scene) => String(scene?.story_block_id || "") === blockId);
    const sceneIds = blockScenes.map((scene) => scene.scene_id);
    return {
      block_id: blockId,
      title_ru: `Черновой story block ${idx + 1}`,
      summary_ru: "Черновой блок из ASR. Название, summary и драматургию должен заполнить LLM-pass.",
      block_goal_ru: "",
      block_reveal_ru: "",
      block_emotion_ru: "",
      global_story_context_ru: "",
      block_place_in_global_arc_ru: "",
      inherits_style_lock_ru: "",
      inherits_world_lock_ru: "",
      inherits_atmosphere_ru: "",
      inherits_continuity_rules_ru: "",
      block_visual_bible_ru: "",
      block_style_lock_ru: "",
      block_location_lock_ru: "",
      block_time_of_day_ru: "",
      block_color_palette_ru: "",
      block_camera_language_ru: "",
      block_continuity_rules_ru: "",
      block_storyboard_summary_ru: "",
      block_reference_frame_prompt_en: "",
      color: colorPalette[idx % colorPalette.length],
      start_sec: roundTimingSec(blockScenes[0]?.start_sec || 0),
      end_sec: roundTimingSec(blockScenes[blockScenes.length - 1]?.end_sec || 0),
      scene_ids: sceneIds,
    };
  });
}

export function updateManualTimingSceneById(scenes = [], sceneId = "", patch = {}) {
  const safePatch = patch && typeof patch === "object" ? patch : {};
  return (Array.isArray(scenes) ? scenes : []).map((scene) => {
    if (String(scene?.scene_id || "") !== String(sceneId || "")) return scene;
    const next = { ...scene, ...safePatch };
    const sectionChanged = Object.prototype.hasOwnProperty.call(safePatch, "section");
    const routeChanged = Object.prototype.hasOwnProperty.call(safePatch, "route");
    const containsVocalChanged = Object.prototype.hasOwnProperty.call(safePatch, "contains_vocal");

    if (sectionChanged) {
      const defaults = getSectionDefaults(safePatch.section);
      next.section = defaults.section;
      if (!routeChanged) next.route = defaults.route;
      if (!containsVocalChanged) {
        next.contains_vocal = defaults.contains_vocal;
        next.contains_vocal_assumption = defaults.contains_vocal_assumption;
        next.contains_instrumental_assumption = defaults.contains_instrumental_assumption;
      }
    }

    if (containsVocalChanged) {
      next.contains_vocal = Boolean(safePatch.contains_vocal);
      next.contains_vocal_assumption = Boolean(safePatch.contains_vocal);
      next.contains_instrumental_assumption = !Boolean(safePatch.contains_vocal);
    }

    if (routeChanged) next.route = safePatch.route;
    next.section = normalizeManualTimingSection(next.section);
    next.route = normalizeManualTimingRoute(next.route);
    next.energy = normalizeManualTimingEnergy(next.energy);
    next.use_sound_suggestion = Boolean(next.use_sound_suggestion);
    return next;
  });
}



function toManualTimingBool(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "y", "on", "да"].includes(normalized)) return true;
    if (["false", "0", "no", "n", "off", "нет", ""].includes(normalized)) return false;
  }
  return Boolean(fallback);
}

function pickManualTimingText(scene = {}, keys = []) {
  for (const key of keys) {
    const value = scene?.[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value);
  }
  return "";
}

function normalizeManualTimingSceneForImport(scene = {}, idx = 0) {
  const rawSection = scene?.section || scene?.story_section || scene?.song_section || scene?.scene_section;
  const section = normalizeManualTimingSection(rawSection || (idx === 0 ? "intro" : "verse"));
  const defaults = getSectionDefaults(section);
  const route = normalizeManualTimingRoute(scene?.route || scene?.video_generation_route || scene?.renderMode || defaults.route);
  const containsVocal = toManualTimingBool(
    scene?.contains_vocal,
    toManualTimingBool(scene?.contains_vocal_assumption, defaults.contains_vocal)
  );
  const containsInstrumental = toManualTimingBool(
    scene?.contains_instrumental,
    toManualTimingBool(scene?.contains_instrumental_assumption, !containsVocal)
  );

  const start = roundTimingSec(scene?.start_sec ?? scene?.startSec ?? scene?.start ?? 0);
  const end = roundTimingSec(scene?.end_sec ?? scene?.endSec ?? scene?.end ?? start);

  return {
    scene_id: String(scene?.scene_id || scene?.sceneId || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene?.index || idx + 1),
    start_sec: start,
    end_sec: end,
    duration_sec: roundTimingSec(scene?.duration_sec ?? scene?.durationSec ?? (end - start)),
    source_kind: String(scene?.source_kind ?? scene?.sourceKind ?? "audio"),
    source_start_sec: scene?.source_start_sec ?? scene?.sourceStartSec ?? null,
    source_end_sec: scene?.source_end_sec ?? scene?.sourceEndSec ?? null,
    is_silence: Boolean(scene?.is_silence || scene?.isSilence || scene?.scene_type === "manual_silence"),
    composer_source_kind: String(scene?.composer_source_kind || scene?.composerSourceKind || ""),
    composer_source_audio_id: String(scene?.composer_source_audio_id || scene?.composerSourceAudioId || ""),
    composer_source_audio_name: String(scene?.composer_source_audio_name || scene?.composerSourceAudioName || ""),
    composer_source_start_sec: scene?.composer_source_start_sec ?? scene?.composerSourceStartSec ?? null,
    composer_source_end_sec: scene?.composer_source_end_sec ?? scene?.composerSourceEndSec ?? null,
    composer_block_id: String(scene?.composer_block_id || scene?.composerBlockId || ""),
    composer_block_type: String(scene?.composer_block_type || scene?.composerBlockType || ""),
    composer_block_label: String(scene?.composer_block_label || scene?.composerBlockLabel || ""),
    composer_role_label: String(scene?.composer_role_label || scene?.composerRoleLabel || ""),
    composer_saved_clip_id: String(scene?.composer_saved_clip_id || scene?.composerSavedClipId || ""),
    composer_saved_clip_label: String(scene?.composer_saved_clip_label || scene?.composerSavedClipLabel || ""),
    speech_start_sec: roundTimingSec(scene?.speech_start_sec ?? scene?.speechStartSec ?? scene?.speech_start ?? start),
    speech_end_sec: roundTimingSec(scene?.speech_end_sec ?? scene?.speechEndSec ?? scene?.speech_end ?? end),
    pre_silence_sec: roundTimingSec(scene?.pre_silence_sec ?? scene?.preSilenceSec ?? Math.max(0, (scene?.speech_start_sec ?? scene?.speechStartSec ?? start) - start)),
    post_silence_sec: roundTimingSec(scene?.post_silence_sec ?? scene?.postSilenceSec ?? Math.max(0, end - (scene?.speech_end_sec ?? scene?.speechEndSec ?? end))),
    section,
    route,
    contains_vocal: containsVocal,
    contains_vocal_assumption: toManualTimingBool(scene?.contains_vocal_assumption, containsVocal),
    contains_instrumental_assumption: toManualTimingBool(scene?.contains_instrumental_assumption, containsInstrumental),
    use_sound_suggestion: toManualTimingBool(scene?.use_sound_suggestion, false),
    energy: normalizeManualTimingEnergy(scene?.energy || "mid"),
    quality: String(scene?.quality || "manual_draft"),
    boundary_reason: String(scene?.boundary_reason || "json_import"),
    transition_out: String(scene?.transition_out || "manual_cut"),
    story_time: String(scene?.story_time || ""),
    scene_type: String(scene?.scene_type || ""),
    drama_hint: pickManualTimingText(scene, ["drama_hint", "dramaHint", "scene_drama_ru"]),
    short_note: pickManualTimingText(scene, ["short_note", "shortNote", "note", "summary_ru"]),
    scene_goal_ru: pickManualTimingText(scene, ["scene_goal_ru", "sceneGoalRu", "goal_ru", "goal"]),
    photo_prompt_hint_ru: pickManualTimingText(scene, ["photo_prompt_hint_ru", "photoPromptHintRu", "prompt_hint_ru", "visual_hint_ru"]),
    prompt_hint_ru: pickManualTimingText(scene, ["prompt_hint_ru", "photo_prompt_hint_ru", "promptHintRu", "visual_hint_ru"]),
    story_position_ru: pickManualTimingText(scene, ["story_position_ru", "story_time", "storyPositionRu"]),
    user_note_ru: pickManualTimingText(scene, ["user_note_ru", "user_notes_ru", "userNoteRu", "note_ru", "director_note_ru"]),
    source_phrase_ids: normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds),
    story_block_id: pickManualTimingText(scene, ["story_block_id", "storyBlockId", "block_id"]) || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id,
    story_block_title_ru: pickManualTimingText(scene, ["story_block_title_ru", "storyBlockTitleRu", "block_title_ru"]),
    story_block_color: pickManualTimingText(scene, ["story_block_color", "storyBlockColor", "block_color"]),
    story_block_position_ru: pickManualTimingText(scene, ["story_block_position_ru", "storyBlockPositionRu", "block_position_ru"]),
    scene_role_in_block_ru: pickManualTimingText(scene, ["scene_role_in_block_ru", "sceneRoleInBlockRu", "role_in_block_ru", "scene_block_role_ru"]),
    block_progress_ru: pickManualTimingText(scene, ["block_progress_ru", "blockProgressRu", "progress_in_block_ru", "story_block_progress_ru"]),
    scene_global_context_ru: pickManualTimingText(scene, ["scene_global_context_ru", "sceneGlobalContextRu"]),
    continuity_anchor_ru: pickManualTimingText(scene, ["continuity_anchor_ru", "continuityAnchorRu"]),
    must_match_project_identity_ru: pickManualTimingText(scene, ["must_match_project_identity_ru", "mustMatchProjectIdentityRu"]),
    must_match_block_style_ru: pickManualTimingText(scene, ["must_match_block_style_ru", "mustMatchBlockStyleRu"]),
    storyboard_frame_role_ru: pickManualTimingText(scene, ["storyboard_frame_role_ru", "storyboardFrameRoleRu"]),
    source_image_prompt_en: pickManualTimingText(scene, ["source_image_prompt_en", "sourceImagePromptEn"]),
    source_image_prompt_ru: pickManualTimingText(scene, ["source_image_prompt_ru", "sourceImagePromptRu"]),
    source_image_negative_prompt_en: pickManualTimingText(scene, ["source_image_negative_prompt_en", "sourceImageNegativePromptEn"]),
    i2v_prompt_en: pickManualTimingText(scene, ["i2v_prompt_en", "i2vPromptEn"]),
    i2v_negative_prompt_en: pickManualTimingText(scene, ["i2v_negative_prompt_en", "i2vNegativePromptEn"]),
    composition_ru: pickManualTimingText(scene, ["composition_ru", "compositionRu"]),
    camera_angle_ru: pickManualTimingText(scene, ["camera_angle_ru", "cameraAngleRu"]),
    subject_lock_ru: pickManualTimingText(scene, ["subject_lock_ru", "subjectLockRu"]),
    background_lock_ru: pickManualTimingText(scene, ["background_lock_ru", "backgroundLockRu"]),
    continuity_from_previous_scene_ru: pickManualTimingText(scene, ["continuity_from_previous_scene_ru", "continuityFromPreviousSceneRu"]),
    must_keep_same_ru: pickManualTimingText(scene, ["must_keep_same_ru", "mustKeepSameRu"]),
    allowed_variation_ru: pickManualTimingText(scene, ["allowed_variation_ru", "allowedVariationRu"]),
    original_text: pickManualTimingText(scene, ["original_text", "originalText"]),
    translated_text_ru: pickManualTimingText(scene, ["translated_text_ru", "translatedTextRu", "translation_ru"]),
    meaning_hint_ru: pickManualTimingText(scene, ["meaning_hint_ru", "meaningHintRu", "meaning_ru"]),
    source_text_en: pickManualTimingText(scene, ["source_text_en", "sourceTextEn", "source_text"]),
    adapted_text_en: pickManualTimingText(scene, ["adapted_text_en", "adaptedTextEn", "adapted_text"]),
    video_prompt: String(scene?.video_prompt || ""),
    negative_prompt: String(scene?.negative_prompt || ""),
    sound_prompt: String(scene?.sound_prompt || ""),
    song_block_id: pickManualTimingText(scene, ["song_block_id", "songBlockId"]),
    song_block_type: pickManualTimingText(scene, ["song_block_type", "songBlockType"]),
    song_block_title_ru: pickManualTimingText(scene, ["song_block_title_ru", "songBlockTitleRu"]),
    lyrics_text: pickManualTimingText(scene, ["lyrics_text", "lyricsText"]),
    visual_role_ru: pickManualTimingText(scene, ["visual_role_ru", "visualRoleRu"]),
    performance_role_ru: pickManualTimingText(scene, ["performance_role_ru", "performanceRoleRu"]),
    lip_sync_required: toManualTimingBool(scene?.lip_sync_required, false),
    vocal_owner_role: pickManualTimingText(scene, ["vocal_owner_role", "vocalOwnerRole"]),
    speaker_id: pickManualTimingText(scene, ["speaker_id", "speakerId"]),
    speaker_name: pickManualTimingText(scene, ["speaker_name", "speakerName"]),
    topic_block_id: pickManualTimingText(scene, ["topic_block_id", "topicBlockId"]),
    topic_block_title_ru: pickManualTimingText(scene, ["topic_block_title_ru", "topicBlockTitleRu"]),
    narrator_text_en: pickManualTimingText(scene, ["narrator_text_en", "narratorTextEn"]),
    narrator_text_ru: pickManualTimingText(scene, ["narrator_text_ru", "narratorTextRu"]),
    speaker_text_en: pickManualTimingText(scene, ["speaker_text_en", "speakerTextEn"]),
    speaker_text_ru: pickManualTimingText(scene, ["speaker_text_ru", "speakerTextRu"]),
    generated_speech_required: toManualTimingBool(scene?.generated_speech_required, false),
    voice_profile_id: pickManualTimingText(scene, ["voice_profile_id", "voiceProfileId"]),
    voice_profile: pickManualTimingText(scene, ["voice_profile", "voiceProfile"]),
    narrator_voice_profile_en: pickManualTimingText(scene, ["narrator_voice_profile_en", "narratorVoiceProfileEn"]),
    negative_voice_traits: pickManualTimingText(scene, ["negative_voice_traits", "negativeVoiceTraits"]),
    broll_hint_ru: pickManualTimingText(scene, ["broll_hint_ru", "brollHintRu"]),
  };
}

export function buildManualTimingAiSplitRequestJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);


  return {
    chatgpt_task: buildManualTimingChatGptTask(
      false,
      normalizeManualTimingAudioPhrases(safeProject.audio_phrases).some((phrase) => String(phrase.status || "") === "asr_raw" || String(phrase.source || "") === "asr")
    ),
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    manual_timing_workflow: normalizeManualTimingWorkflow(safeProject.manual_timing_workflow),
    mode: "manual_clip_board",
    project_mode: String(safeProject.project_mode || safeProject.projectMode || ""),
    project_kind: String(safeProject.project_kind || safeProject.projectKind || ""),
    format: String(safeProject.format || safeProject.aspect_ratio || "9:16"),
    aspect_ratio: String(safeProject.aspect_ratio || safeProject.format || "9:16"),
    format_locked: Boolean(safeProject.format_locked),
    split_type: "ai_story_blocks_split_request",
    semantic_cut_rules: MANUAL_TIMING_SEMANTIC_CUT_RULES,
    story_pass_mode: "semantic_story_cut",
    audio_duration_sec: Number(audio.duration_sec || 0),
    language_source: "en",
    language_helper: "ru",
    scene_duration_target_sec: {
      min: 4,
      max: 8,
      preferred: 6,
    },
    route_policy: {
      voiceover: "i2v",
      singer_lipsync: "ia2v",
      instrumental: "i2v",
    },
    global_hint: "Ты режешь не текст, ты режешь будущую съёмку. 1 сцена = 1 понятное фото + 1 i2v-клип. Блок = этап истории, не папка сцен.",
    story_request_ru: "",
    story_blocks: [],
    audio_phrases: normalizeManualTimingAudioPhrases(safeProject.audio_phrases),
    scenes: [],
  };
}

export const MANUAL_TIMING_SEMANTIC_CUT_RULES = {
  version: "semantic_story_cut_v1",
  main_rule: "one_scene_equals_one_visual_shootable_meaning",
  core_instruction_ru: "Ты режешь не текст. Ты режешь будущую съёмку.",
  scene_quality_question_ru: "Можно ли снять это одним понятным кадром?",
  scene_unit: "one photo + one i2v clip",
  allow_short_scenes: true,
  prefer_clear_shots_over_long_mixed_scenes: true,
  story_block_rule: "block_is_a_finished_dramatic_stage_not_fixed_scene_count",
  manual_user_split_priority: true,
  audio_phrases_are_timing_source: true,
  do_not_change_audio_phrases: true,
  video_fields_policy: "leave_video_prompt_negative_prompt_sound_prompt_empty_until_video_pass",
  do_not_merge: [
    "environment_wakes_up + animals_enter",
    "character_action + another_character_reaction",
    "wide_action + important_detail",
    "fear + protective_response",
    "attack + consequence",
    "philosophical_line + new_visual_event",
  ],
  split_details_as_scenes_when_meaningful: [
    "ears_turning",
    "eyes_waiting",
    "hooves_and_dust",
    "birds_flying",
    "hand_or_trunk_or_paw_action",
    "character_stops",
    "character_turns_back",
    "child_scared",
    "mother_protects_child",
    "trunk_raised",
    "predator_stops",
    "predator_retreats",
    "sky_color_change",
    "night_or_final_symbol",
  ],
  pause_rules: {
    short_pause_between_semantic_scenes: "split_by_midpoint",
    pause_before_important_action: "may_belong_to_next_scene",
    pause_after_strong_phrase: "may_remain_in_previous_scene",
    long_pause_with_visual_meaning: "may_become_atmospheric_broll_scene",
    silent_broll_scene_may_have_empty_source_phrase_ids: true,
    duration_sec_includes_assigned_silence: true,
  },
  required_scene_fields: [
    "translated_text_ru",
    "meaning_hint_ru",
    "scene_goal_ru",
    "scene_role_in_block_ru",
    "block_progress_ru",
    "photo_prompt_hint_ru",
    "prompt_hint_ru",
    "user_note_ru",
  ],
  quality_checks: [
    "each_scene_can_be_imagined_as_one_clear_photo",
    "each_scene_has_one_main_visual_action_or_state",
    "story_blocks_explain_dramatic_stages",
    "source_phrase_ids_match_scene_timing",
    "silent_broll_scenes_must_be_explicitly_marked",
    "manual_user_scene_boundaries_are_not_overwritten",
  ],
};

export const MANUAL_TIMING_STORY_PASS_TASK_RU = `SEMANTIC STORY CUT PASS / СМЫСЛОВАЯ НАРЕЗКА ИСТОРИИ ДЛЯ ФОТО-РАСКАДРОВКИ.

Это НЕ простой перевод и НЕ механический Story Pass.

Главная задача:
разбить историю так, чтобы по каждой сцене можно было сделать одну понятную фотографию и один i2v-клип.

ОСНОВНОЙ КАНОН:
1. Одна сцена = один законченный визуальный / съёмочный смысл.
2. Сцена должна отвечать:
   - что мы видим на фото?
   - что оживает в i2v?
   - какой конкретный шаг истории происходит сейчас?
3. Нельзя склеивать в одну сцену два разных визуальных события, даже если они находятся в одной фразе ASR.
4. Если внутри фразы есть:
   - общий план + деталь,
   - событие + реакция,
   - персонаж + реакция другого персонажа,
   - среда + появление животного/героя,
   - действие + последствие,
   это чаще всего нужно разделить на отдельные сцены.
5. Короткие сцены разрешены и желательны, если они объясняют историю.
6. Блок = законченный драматургический этап истории, а не просто 3–4 сцены подряд.
7. Смысловые блоки нужны для будущей фото-раскадровки, поэтому каждый блок должен иметь:
   - свою цель,
   - свой эмоциональный этап,
   - свой визуальный стиль внутри общей истории,
   - понятное место в общей драматургии.
8. Количество блоков не фиксировано. Делать столько блоков, сколько требует история.
9. Количество сцен не фиксировано. Лучше больше коротких понятных сцен, чем меньше длинных смешанных сцен.

КАК ДЕЛИТЬ СЦЕНЫ:
- Рассвет / появление света / пробуждение мира — отдельные сцены.
- Появление нового животного / героя — отдельная сцена.
- Деталь поведения животного — отдельная сцена, если она важна для смысла.
- Реакция на опасность — отдельная сцена.
- Подготовка к действию — отдельная сцена.
- Само действие — отдельная сцена.
- Последствие действия — отдельная сцена.
- Эмоциональная реакция — отдельная сцена.
- Философский вывод диктора — отдельная сцена или отдельный блок.

ПРИМЕРЫ ПРАВИЛЬНОГО ДЕЛЕНИЯ:
Нельзя делать одну сцену:
"саванна начинает просыпаться + стадо зебр пошло"

Нужно делить:
scene A — саванна начинает просыпаться
scene B — стадо зебр медленно идёт

Нельзя делать одну сцену:
"слонёнок испугался + старая слониха разворачивается назад"

Нужно делить:
scene A — слонёнок испугался
scene B — слониха разворачивается назад

Нельзя делать одну сцену:
"всё взрывается движением + копыта бьют пыль + птицы взлетают"

Нужно делить:
scene A — общий взрыв движения
scene B — копыта и пыль
scene C — птицы взлетают с деревьев

ДЕТАЛИ, КОТОРЫЕ ЧАСТО НУЖНО ВЫНОСИТЬ В ОТДЕЛЬНЫЕ СЦЕНЫ:
- уши животного настороженно поворачиваются;
- глаза / взгляд / ожидание;
- копыта и пыль;
- птицы взлетают;
- рука / хобот / лапа делает важное действие;
- герой останавливается;
- герой разворачивается;
- мать защищает детёныша;
- хищник замирает или отступает;
- небо / свет / ночь / финальный символ.

КАК ДЕЛАТЬ STORY BLOCKS:
Story block = законченный этап истории.

Блоки нельзя делать механически.
Блок должен отвечать:
- что изменилось в истории?
- какой этап драмы мы проходим?
- что зритель должен понять?
- какие сцены внутри блока работают вместе?

Пример структуры блоков:
block_01 — вступление / мир до события
block_02 — появление жизни / первых героев
block_03 — появление угрозы
block_04 — уязвимый герой / эмоциональная ставка
block_05 — напряжение перед действием
block_06 — кульминация / движение / хаос
block_07 — реакция / защита / перелом
block_08 — последствия / возвращение тишины
block_09 — смысл / философский вывод / цикл

Для другой истории названия блоков должны быть другими, по смыслу конкретного сюжета.

ПАУЗЫ И БЕЗРЕЧЕВОЕ ПРОСТРАНСТВО:
1. Паузы входят в duration_sec сцены.
2. Короткую паузу между двумя смысловыми сценами обычно делить по midpoint.
3. Пауза перед важным действием может относиться к следующей сцене.
4. Пауза после сильной фразы может оставаться в предыдущей сцене.
5. Длинная пауза может стать отдельной атмосферной b-roll сценой, если в ней есть визуальный смысл.
6. Нельзя резать так, чтобы конец одной сцены уже рассказывал начало следующего визуального события.

ЕСЛИ ЕСТЬ audio_phrases:
- Использовать audio_phrases как главный источник таймингов.
- Можно пересобирать scenes из audio_phrases по смыслу.
- Нельзя менять сами audio_phrases: phrase_id, start_sec, end_sec, исходный текст.
- source_phrase_ids каждой сцены должны соответствовать реальным фразам, которые входят в сцену.

ЕСЛИ ЕСТЬ РУЧНАЯ РАЗБИВКА ПОЛЬЗОВАТЕЛЯ:
- Считать ручные границы пользователя приоритетными.
- Не перетирать ручную разбивку механической логикой.
- Можно пересобрать story_blocks и смысловые поля поверх ручной разбивки.
- Если source_phrase_ids устарели после ручной правки, пересобрать их по фактическому таймингу сцены.

ЧТО ЗАПОЛНЯТЬ ДЛЯ КАЖДОЙ СЦЕНЫ:
translated_text_ru — нормальный русский смысл сцены, с исправлением ASR-ошибок.
meaning_hint_ru — что сцена значит в истории.
scene_goal_ru — зачем эта сцена нужна.
scene_role_in_block_ru — роль сцены внутри блока.
block_progress_ru — как сцена двигает блок вперёд.
photo_prompt_hint_ru — что нужно сгенерировать как стартовую фотографию.
prompt_hint_ru — как это должно ожить в i2v.
user_note_ru — важные исправления ASR и пояснения нарезки.

video_prompt, negative_prompt, sound_prompt оставить пустыми до отдельного video-pass.

КРИТЕРИЙ КАЧЕСТВА:
Если по сцене нельзя представить одну ясную фотографию — сцена нарезана неправильно.
Если сцена содержит два разных кадра — её нужно разделить.
Если блок не объясняет этап истории — блок сделан неправильно.

В самом конце JSON обязательно верни:
"manual_timing_pass_result": {
  "completed_pass_type": "semantic_story_cut",
  "unlock_next_stage": "story_bible",
  "activation_phrase": "SEMANTIC_STORY_CUT_DONE"
}

Без этого следующий этап не откроется.`;


export const MANUAL_TIMING_STORY_BIBLE_PASS_TASK_RU = `GLOBAL STORY BIBLE / ОБЩИЙ ПАСПОРТ ИСТОРИИ. Это не перевод и не переразбивка сцен. Нужно создать общий story bible для всей истории целиком. Не менять audio_phrases, scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids, story_block_id, story_blocks и scenes. Не менять количество сцен и блоков. Нужно заполнить только верхнеуровневые поля общего описания проекта: project_story_summary_ru, project_core_theme_ru, project_drama_arc_ru, project_visual_bible_ru, project_style_lock_ru, project_world_lock_ru, project_character_identity_lock_ru, project_location_lock_ru, project_time_progression_ru, project_atmosphere_lock_ru, project_camera_language_ru, project_color_progression_ru, project_continuity_rules_ru, project_must_keep_same_ru, project_allowed_variation_ru, project_reference_prompt_en. Это описание потом должно использоваться как глобальная подсказка для всех блоков и сцен, чтобы вся история держала единый стиль, атмосферу, мир и continuity от первого блока до последнего.

В самом конце JSON обязательно верни:
"manual_timing_pass_result": {
  "completed_pass_type": "story_bible",
  "unlock_next_stage": "block_storyboard",
  "activation_phrase": "STORY_BIBLE_DONE"
}

Без этого следующий этап не откроется.`;

export const MANUAL_TIMING_BLOCK_STORYBOARD_PASS_TASK_RU = `BLOCK STORYBOARD PASS / РАСКАДРОВКА БЛОКОВ.
Это не Story Pass, не перевод и не переразбивка. Не менять audio_phrases, scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids, story_block_id, story_blocks structure, scenes structure, количество сцен и блоков. video_prompt, negative_prompt, sound_prompt оставить пустыми.

Обязательно опираться на project_story_summary_ru, project_drama_arc_ru, project_visual_bible_ru, project_style_lock_ru, project_world_lock_ru, project_time_progression_ru, project_atmosphere_lock_ru, project_camera_language_ru и project_continuity_rules_ru.

Нужно создать визуальную раскадровку: общий visual bible для каждого блока и конкретные image/i2v prompts для каждой сцены. Все сцены должны выглядеть частью одной истории, одного мира, одного стиля и одной временной дуги.

В story_blocks заполни/сохрани: block_visual_bible_ru, block_style_lock_ru, block_location_lock_ru, block_time_of_day_ru, block_color_palette_ru, block_camera_language_ru, block_continuity_rules_ru, block_storyboard_summary_ru, block_reference_frame_prompt_en.

В scenes заполни/сохрани: storyboard_frame_role_ru, source_image_prompt_en, source_image_prompt_ru, source_image_negative_prompt_en, i2v_prompt_en, i2v_negative_prompt_en, composition_ru, camera_angle_ru, subject_lock_ru, background_lock_ru, continuity_from_previous_scene_ru, must_keep_same_ru, allowed_variation_ru.

В самом конце JSON обязательно верни:
"manual_timing_pass_result": {
  "completed_pass_type": "block_storyboard",
  "unlock_next_stage": "director_board",
  "activation_phrase": "BLOCK_STORYBOARD_DONE"
}

Без этого следующий этап не откроется.`;


function logManualTimingExportSourcePhraseIdsRepair(passType = "", sourcePhraseRepair = {}, sceneCount = 0, audioPhraseCount = 0) {
  if (!sourcePhraseRepair?.repaired) return;
  const repairedEmptyCount = Number(sourcePhraseRepair.repairedEmptyCount || 0);
  const replacedWrongCount = Number(sourcePhraseRepair.replacedWrongCount || 0);
  console.info("[MANUAL TIMING EXPORT_SOURCE_PHRASE_IDS_REPAIRED]", {
    passType,
    repairedSceneCount: repairedEmptyCount + replacedWrongCount,
    repairedEmptyCount,
    replacedWrongCount,
    sceneCount,
    audioPhraseCount,
  });
}

function repairManualTimingExportScenesSourcePhraseIds(passType = "", exportJson = {}) {
  const originalScenes = Array.isArray(exportJson?.scenes) ? exportJson.scenes : [];
  const audioPhrases = Array.isArray(exportJson?.audio_phrases) ? exportJson.audio_phrases : [];
  const sourcePhraseRepair = repairManualTimingSourcePhraseIdsFromTiming(originalScenes, audioPhrases);
  logManualTimingExportSourcePhraseIdsRepair(
    passType,
    { ...sourcePhraseRepair, originalScenes },
    originalScenes.length,
    audioPhrases.length
  );
  return Array.isArray(sourcePhraseRepair?.scenes) ? sourcePhraseRepair.scenes : originalScenes;
}

export function buildManualTimingStoryBiblePassJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const exportJson = buildManualTimingExportJson(safeProject);
  const exportScenes = repairManualTimingExportScenesSourcePhraseIds("story_bible", exportJson);
  const workflowMeta = buildManualTimingWorkflowForPass(safeProject, "story_bible");

  return {
    chatgpt_task: MANUAL_TIMING_STORY_BIBLE_PASS_TASK_RU,
    manual_timing_workflow: workflowMeta.workflow,
    manual_timing_pass: workflowMeta.pass,
    manual_timing_pass_result_required: {
      completed_pass_type: "story_bible",
      unlock_next_stage: "block_storyboard",
      activation_phrase: "STORY_BIBLE_DONE",
      rule_ru: "Верни этот блок только после заполнения общего паспорта истории. Без него блочная раскадровка не откроется.",
    },
    split_type: "manual_story_bible_pass",
    format: exportJson.format,
    aspect_ratio: exportJson.aspect_ratio,
    format_locked: exportJson.format_locked,
    audio_duration_sec: Number(audio.duration_sec || exportJson.audio_duration_sec || 0),
    ...pickManualTimingProjectStoryBibleFields(safeProject),
    audio_phrases: exportJson.audio_phrases,
    story_blocks: exportJson.story_blocks,
    scenes: exportScenes,
  };
}

export function buildManualTimingBlockStoryboardPassJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const exportJson = buildManualTimingExportJson(safeProject);
  const exportScenes = repairManualTimingExportScenesSourcePhraseIds("block_storyboard", exportJson);
  const workflowMeta = buildManualTimingWorkflowForPass(safeProject, "block_storyboard");
  return {
    chatgpt_task: MANUAL_TIMING_BLOCK_STORYBOARD_PASS_TASK_RU,
    manual_timing_workflow: workflowMeta.workflow,
    manual_timing_pass: workflowMeta.pass,
    manual_timing_pass_result_required: {
      completed_pass_type: "block_storyboard",
      unlock_next_stage: "director_board",
      activation_phrase: "BLOCK_STORYBOARD_DONE",
      rule_ru: "Верни этот блок только после заполнения блочной раскадровки. Без него режиссёрская доска не откроется.",
    },
    split_type: "manual_block_storyboard_pass",
    format: exportJson.format,
    aspect_ratio: exportJson.aspect_ratio,
    format_locked: exportJson.format_locked,
    audio_duration_sec: Number(audio.duration_sec || exportJson.audio_duration_sec || 0),
    ...pickManualTimingProjectStoryBibleFields(safeProject),
    audio_phrases: exportJson.audio_phrases,
    output_fields: {
      story_blocks: [
        "block_id",
        "title_ru",
        "summary_ru",
        "scene_ids",
        ...MANUAL_TIMING_BLOCK_STORYBOARD_BLOCK_FIELDS,
      ],
      scenes: [
        "scene_id",
        "story_block_id",
        "start_sec",
        "end_sec",
        "speech_start_sec",
        "speech_end_sec",
        "source_phrase_ids",
        ...MANUAL_TIMING_BLOCK_STORYBOARD_SCENE_FIELDS,
        "video_prompt",
        "negative_prompt",
        "sound_prompt",
      ],
    },
    story_blocks: exportJson.story_blocks.map((block) => ({
      ...block,
      global_story_context_ru: String(block?.global_story_context_ru || ""),
      block_place_in_global_arc_ru: String(block?.block_place_in_global_arc_ru || ""),
      inherits_style_lock_ru: String(block?.inherits_style_lock_ru || ""),
      inherits_world_lock_ru: String(block?.inherits_world_lock_ru || ""),
      inherits_atmosphere_ru: String(block?.inherits_atmosphere_ru || ""),
      inherits_continuity_rules_ru: String(block?.inherits_continuity_rules_ru || ""),
      block_visual_bible_ru: String(block?.block_visual_bible_ru || ""),
      block_style_lock_ru: String(block?.block_style_lock_ru || ""),
      block_location_lock_ru: String(block?.block_location_lock_ru || ""),
      block_time_of_day_ru: String(block?.block_time_of_day_ru || ""),
      block_color_palette_ru: String(block?.block_color_palette_ru || ""),
      block_camera_language_ru: String(block?.block_camera_language_ru || ""),
      block_continuity_rules_ru: String(block?.block_continuity_rules_ru || ""),
      block_storyboard_summary_ru: String(block?.block_storyboard_summary_ru || ""),
      block_reference_frame_prompt_en: String(block?.block_reference_frame_prompt_en || ""),
    })),
    scenes: exportScenes.map((scene) => ({
      ...scene,
      scene_global_context_ru: String(scene?.scene_global_context_ru || ""),
      continuity_anchor_ru: String(scene?.continuity_anchor_ru || ""),
      must_match_project_identity_ru: String(scene?.must_match_project_identity_ru || ""),
      must_match_block_style_ru: String(scene?.must_match_block_style_ru || ""),
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: "",
    })),
  };
}

export function buildManualTimingStoryPassJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const exportJson = buildManualTimingExportJson(safeProject);
  const exportScenes = repairManualTimingExportScenesSourcePhraseIds("semantic_story_cut", exportJson);
  const workflowMeta = buildManualTimingWorkflowForPass(safeProject, "semantic_story_cut");

  return {
    chatgpt_task: MANUAL_TIMING_STORY_PASS_TASK_RU,
    manual_timing_workflow: workflowMeta.workflow,
    manual_timing_pass: workflowMeta.pass,
    manual_timing_pass_result_required: {
      completed_pass_type: "semantic_story_cut",
      unlock_next_stage: "story_bible",
      activation_phrase: "SEMANTIC_STORY_CUT_DONE",
      rule_ru: "Верни этот блок только после полного заполнения смысловой нарезки. Без него следующий этап не откроется.",
    },
    semantic_cut_rules: MANUAL_TIMING_SEMANTIC_CUT_RULES,
    story_pass_mode: "semantic_story_cut",
    split_type: "semantic_story_cut_pass",
    format: exportJson.format,
    aspect_ratio: exportJson.aspect_ratio,
    format_locked: exportJson.format_locked,
    audio_duration_sec: Number(audio.duration_sec || exportJson.audio_duration_sec || 0),
    audio_phrases: exportJson.audio_phrases,
    scenes: exportScenes.map((scene) => ({
      ...scene,
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: "",
    })),
    story_blocks: exportJson.story_blocks,
  };
}

function canonicalManualTimingJson(value) {
  if (Array.isArray(value)) return value.map(canonicalManualTimingJson);
  if (value && typeof value === "object") {
    return Object.keys(value).sort().reduce((acc, key) => {
      acc[key] = canonicalManualTimingJson(value[key]);
      return acc;
    }, {});
  }
  return value;
}

function sameManualTimingJson(a, b) {
  return JSON.stringify(canonicalManualTimingJson(a)) === JSON.stringify(canonicalManualTimingJson(b));
}

function isManualTimingStoryPassPayload(raw = {}) {
  const passType = String(raw?.manual_timing_pass?.pass_type || raw?.manualTimingPass?.passType || "");
  const splitType = String(raw?.split_type || raw?.splitType || "");
  const task = typeof raw?.chatgpt_task === "string"
    ? raw.chatgpt_task
    : JSON.stringify(raw?.chatgpt_task || "");
  return passType === "semantic_story_cut"
    || splitType === "asr_gap_aware_story_pass"
    || splitType === "semantic_story_cut_pass"
    || String(raw?.story_pass_mode || raw?.storyPassMode || "") === "semantic_story_cut"
    || task.includes("SEMANTIC STORY CUT PASS")
    || task.includes("СМЫСЛОВАЯ НАРЕЗКА ИСТОРИИ")
    || task.includes("после ASR + gap-aware scene builder")
    || task.includes("РЕЖИССЁРСКАЯ СБОРКА ИСТОРИИ");
}

function isSemanticManualTimingStoryPassPayload(raw = {}) {
  if (String(raw?.backup_type || raw?.backupType || "") === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE) return false;
  const passType = String(raw?.manual_timing_pass?.pass_type || raw?.manualTimingPass?.passType || "");
  const splitType = String(raw?.split_type || raw?.splitType || "");
  const storyPassMode = String(raw?.story_pass_mode || raw?.storyPassMode || "");
  const task = typeof raw?.chatgpt_task === "string"
    ? raw.chatgpt_task
    : JSON.stringify(raw?.chatgpt_task || "");
  return passType === "semantic_story_cut"
    || splitType === "semantic_story_cut_pass"
    || storyPassMode === "semantic_story_cut"
    || task.includes("SEMANTIC STORY CUT PASS")
    || task.includes("СМЫСЛОВАЯ НАРЕЗКА ИСТОРИИ");
}

function isManualTimingSilentBrollScene(scene = {}, rawScene = {}) {
  const values = [
    scene.scene_type,
    rawScene.scene_type,
    scene.sceneType,
    rawScene.sceneType,
    scene.visual_role_ru,
    rawScene.visual_role_ru,
    scene.visualRoleRu,
    rawScene.visualRoleRu,
    scene.storyboard_frame_role_ru,
    rawScene.storyboard_frame_role_ru,
    scene.storyboardFrameRoleRu,
    rawScene.storyboardFrameRoleRu,
    scene.scene_goal_ru,
    rawScene.scene_goal_ru,
    scene.sceneGoalRu,
    rawScene.sceneGoalRu,
    scene.meaning_hint_ru,
    rawScene.meaning_hint_ru,
    scene.meaningHintRu,
    rawScene.meaningHintRu,
    scene.user_note_ru,
    rawScene.user_note_ru,
    scene.userNoteRu,
    rawScene.userNoteRu,
  ].map((value) => String(value || "").toLowerCase());

  return values.some((value) => (
    value.includes("b-roll")
    || value.includes("broll")
    || value.includes("silent")
    || value.includes("silence")
    || value.includes("без речи")
    || value.includes("пауза")
    || value.includes("атмосферная")
    || value.includes("атмосферный")
  ));
}

function isManualTimingStoryBiblePassPayload(raw = {}) {
  const passType = String(raw?.manual_timing_pass?.pass_type || raw?.manualTimingPass?.passType || "");
  const splitType = String(raw?.split_type || raw?.splitType || "");
  const task = typeof raw?.chatgpt_task === "string"
    ? raw.chatgpt_task
    : JSON.stringify(raw?.chatgpt_task || "");
  return passType === "story_bible"
    || splitType === "manual_story_bible_pass"
    || task.includes("GLOBAL STORY BIBLE")
    || task.includes("ОБЩИЙ ПАСПОРТ ИСТОРИИ");
}

function isManualTimingBlockStoryboardPassPayload(raw = {}) {
  const passType = String(raw?.manual_timing_pass?.pass_type || raw?.manualTimingPass?.passType || "");
  return passType === "block_storyboard" || String(raw?.split_type || raw?.splitType || "") === "manual_block_storyboard_pass";
}

export function validateManualTimingStoryBiblePassImport(raw = {}, baseProject = {}) {
  if (!isManualTimingStoryBiblePassPayload(raw)) return { ok: true, errors: [] };

  const basePayload = buildManualTimingStoryBiblePassJson(baseProject);
  const importedAudioPhrases = normalizeManualTimingAudioPhrases(raw?.audio_phrases || raw?.audioPhrases || []);
  const baseAudioPhrases = normalizeManualTimingAudioPhrases(basePayload.audio_phrases);
  const importedScenes = (Array.isArray(raw?.scenes) ? raw.scenes : []).map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const baseScenes = basePayload.scenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const importedStoryBlocks = normalizeManualTimingStoryBlocks(raw?.story_blocks || []);
  const baseStoryBlocks = normalizeManualTimingStoryBlocks(basePayload.story_blocks || []);
  const errors = [];

  if (!sameManualTimingJson(importedAudioPhrases, baseAudioPhrases)) {
    errors.push("audio_phrases изменились — Story Bible Pass не должен менять ASR-фразы.");
  }
  if (importedScenes.length !== baseScenes.length) {
    errors.push(`Количество scenes изменилось: было ${baseScenes.length}, стало ${importedScenes.length}.`);
  }
  if (importedStoryBlocks.length !== baseStoryBlocks.length) {
    errors.push(`Количество story_blocks изменилось: было ${baseStoryBlocks.length}, стало ${importedStoryBlocks.length}.`);
  }

  const importedSceneById = new Map(importedScenes.map((scene) => [String(scene.scene_id || ""), scene]));
  baseScenes.forEach((baseScene) => {
    const sceneId = String(baseScene.scene_id || "");
    const nextScene = importedSceneById.get(sceneId);
    if (!nextScene) {
      errors.push(`scene_id изменился или удалён: ${sceneId}.`);
      return;
    }
    ["start_sec", "end_sec", "speech_start_sec", "speech_end_sec"].forEach((key) => {
      if (roundTimingSec(nextScene[key]) !== roundTimingSec(baseScene[key])) errors.push(`${sceneId}: ${key} изменился (${baseScene[key]} → ${nextScene[key]}).`);
    });
    if (!sameManualTimingJson(normalizeManualTimingSourcePhraseIds(nextScene.source_phrase_ids), normalizeManualTimingSourcePhraseIds(baseScene.source_phrase_ids))) {
      errors.push(`${sceneId}: source_phrase_ids изменились.`);
    }
    if (String(nextScene.story_block_id || "") !== String(baseScene.story_block_id || "")) errors.push(`${sceneId}: story_block_id изменился.`);
  });

  const importedBlockById = new Map(importedStoryBlocks.map((block) => [String(block.block_id || ""), block]));
  baseStoryBlocks.forEach((baseBlock) => {
    const blockId = String(baseBlock.block_id || "");
    const nextBlock = importedBlockById.get(blockId);
    if (!nextBlock) {
      errors.push(`story_block_id изменился или удалён: ${blockId}.`);
      return;
    }
    ["start_sec", "end_sec"].forEach((key) => {
      if (roundTimingSec(nextBlock[key]) !== roundTimingSec(baseBlock[key])) errors.push(`${blockId}: ${key} изменился (${baseBlock[key]} → ${nextBlock[key]}).`);
    });
    if (!sameManualTimingJson(nextBlock.scene_ids || [], baseBlock.scene_ids || [])) errors.push(`${blockId}: scene_ids изменились.`);
  });

  const hasAnyBibleField = MANUAL_TIMING_PROJECT_STORY_BIBLE_FIELDS.some((key) => String(raw?.[key] || "").trim());
  if (!hasAnyBibleField) errors.push("Story Bible Pass не заполнил верхнеуровневые project_* поля.");

  return { ok: !errors.length, errors };
}

export function validateManualTimingBlockStoryboardPassImport(raw = {}, baseProject = {}) {
  if (!isManualTimingBlockStoryboardPassPayload(raw)) return { ok: true, errors: [] };

  const basePayload = buildManualTimingBlockStoryboardPassJson(baseProject);
  const importedAudioPhrases = normalizeManualTimingAudioPhrases(raw?.audio_phrases || raw?.audioPhrases || []);
  const baseAudioPhrases = normalizeManualTimingAudioPhrases(basePayload.audio_phrases || []);
  const rawScenes = Array.isArray(raw?.scenes) ? raw.scenes : [];
  const importedScenes = rawScenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const baseScenes = basePayload.scenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const importedStoryBlocks = normalizeManualTimingStoryBlocks(raw?.story_blocks || []);
  const baseStoryBlocks = normalizeManualTimingStoryBlocks(basePayload.story_blocks || []);
  const errors = [];

  if (!sameManualTimingJson(importedAudioPhrases, baseAudioPhrases)) {
    errors.push("audio_phrases изменились — Block Storyboard Pass не должен менять ASR-фразы.");
  }
  if (importedScenes.length !== baseScenes.length) {
    errors.push(`Количество scenes изменилось: было ${baseScenes.length}, стало ${importedScenes.length}.`);
  }
  if (importedStoryBlocks.length !== baseStoryBlocks.length) {
    errors.push(`Количество story_blocks изменилось: было ${baseStoryBlocks.length}, стало ${importedStoryBlocks.length}.`);
  }

  const importedSceneById = new Map(importedScenes.map((scene) => [String(scene.scene_id || ""), scene]));
  baseScenes.forEach((baseScene) => {
    const sceneId = String(baseScene.scene_id || "");
    const nextScene = importedSceneById.get(sceneId);
    if (!nextScene) {
      errors.push(`scene_id изменился или удалён: ${sceneId}.`);
      return;
    }
    ["start_sec", "end_sec", "speech_start_sec", "speech_end_sec"].forEach((key) => {
      if (roundTimingSec(nextScene[key]) !== roundTimingSec(baseScene[key])) {
        errors.push(`${sceneId}: ${key} изменился (${baseScene[key]} → ${nextScene[key]}).`);
      }
    });
    if (!sameManualTimingJson(normalizeManualTimingSourcePhraseIds(nextScene.source_phrase_ids), normalizeManualTimingSourcePhraseIds(baseScene.source_phrase_ids))) {
      errors.push(`${sceneId}: source_phrase_ids изменились.`);
    }
    if (String(nextScene.story_block_id || "") !== String(baseScene.story_block_id || "")) {
      errors.push(`${sceneId}: story_block_id изменился.`);
    }
    ["video_prompt", "negative_prompt", "sound_prompt"].forEach((key) => {
      if (String(nextScene[key] || "").trim()) errors.push(`${sceneId}: Block Storyboard Pass не должен заполнять ${key}.`);
    });
  });

  const importedBlockById = new Map(importedStoryBlocks.map((block) => [String(block.block_id || ""), block]));
  baseStoryBlocks.forEach((baseBlock) => {
    const blockId = String(baseBlock.block_id || "");
    const nextBlock = importedBlockById.get(blockId);
    if (!nextBlock) {
      errors.push(`story_block_id изменился или удалён: ${blockId}.`);
      return;
    }
    ["start_sec", "end_sec"].forEach((key) => {
      if (roundTimingSec(nextBlock[key]) !== roundTimingSec(baseBlock[key])) errors.push(`${blockId}: ${key} изменился (${baseBlock[key]} → ${nextBlock[key]}).`);
    });
    if (!sameManualTimingJson(nextBlock.scene_ids || [], baseBlock.scene_ids || [])) errors.push(`${blockId}: scene_ids изменились.`);
  });

  const hasAnyStoryboardField = importedScenes.some((scene) => (
    ["source_image_prompt_en", "source_image_prompt_ru", "i2v_prompt_en"].some((key) => String(scene?.[key] || "").trim())
  )) || importedStoryBlocks.some((block) => String(block?.block_visual_bible_ru || "").trim());
  if (!hasAnyStoryboardField) {
    errors.push("Block Storyboard Pass должен заполнить хотя бы одно storyboard поле: source_image_prompt_en/source_image_prompt_ru/i2v_prompt_en/block_visual_bible_ru.");
  }

  return { ok: !errors.length, errors };
}

export function validateManualTimingStoryPassImport(raw = {}, baseProject = {}) {
  if (!isManualTimingStoryPassPayload(raw)) return { ok: true, errors: [] };

  const basePayload = buildManualTimingStoryPassJson(baseProject);
  const importedAudioPhrases = normalizeManualTimingAudioPhrases(raw?.audio_phrases || raw?.audioPhrases || []);
  const baseAudioPhrases = normalizeManualTimingAudioPhrases(basePayload.audio_phrases);
  const rawScenes = Array.isArray(raw?.scenes) ? raw.scenes : [];
  const importedScenes = rawScenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const baseScenes = basePayload.scenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const rawStoryBlocks = Array.isArray(raw?.story_blocks) ? raw.story_blocks : [];
  const storyBlocks = normalizeManualTimingStoryBlocks(rawStoryBlocks);
  const errors = [];

  if (!sameManualTimingJson(importedAudioPhrases, baseAudioPhrases)) {
    errors.push("audio_phrases изменились — Story Pass не должен менять ASR-фразы.");
  }

  if (isSemanticManualTimingStoryPassPayload(raw)) {
    if (!importedScenes.length) errors.push("scenes не заполнены.");

    const audioDurationSec = roundTimingSec(raw?.audio_duration_sec ?? raw?.audioDurationSec ?? basePayload.audio_duration_sec ?? baseProject?.audio?.duration_sec ?? 0);
    const phraseIds = new Set(baseAudioPhrases.map((phrase) => String(phrase.phrase_id || "")).filter(Boolean));
    const sceneIds = new Set();
    const duplicateSceneIds = new Set();

    rawScenes.forEach((rawScene, idx) => {
      const sceneId = String(rawScene?.scene_id || rawScene?.sceneId || "").trim();
      if (!sceneId) {
        errors.push(`scene_${idx + 1}: scene_id не заполнен.`);
        return;
      }
      if (sceneIds.has(sceneId)) duplicateSceneIds.add(sceneId);
      sceneIds.add(sceneId);
    });
    duplicateSceneIds.forEach((sceneId) => errors.push(`${sceneId}: scene_id дублируется.`));

    importedScenes.forEach((scene, idx) => {
      const rawScene = rawScenes[idx] || {};
      const sceneId = String(scene.scene_id || rawScene?.scene_id || rawScene?.sceneId || `scene_${idx + 1}`);
      const rawStart = Number(rawScene?.start_sec ?? rawScene?.startSec);
      const rawEnd = Number(rawScene?.end_sec ?? rawScene?.endSec);
      const start = roundTimingSec(rawStart);
      const end = roundTimingSec(rawEnd);
      const sourcePhraseIds = normalizeManualTimingSourcePhraseIds(scene.source_phrase_ids || rawScene?.sourcePhraseIds);

      if (!Number.isFinite(rawStart)) errors.push(`${sceneId}: start_sec должен быть числом.`);
      if (!Number.isFinite(rawEnd)) errors.push(`${sceneId}: end_sec должен быть числом.`);
      if (Number.isFinite(rawStart) && Number.isFinite(rawEnd) && !(start < end)) errors.push(`${sceneId}: start_sec должен быть меньше end_sec.`);
      if (audioDurationSec > 0 && Number.isFinite(rawStart) && Number.isFinite(rawEnd) && (start < -0.01 || end - audioDurationSec > 0.01)) {
        errors.push(`${sceneId}: тайминг scene вне audio_duration_sec (${audioDurationSec.toFixed(3)} сек).`);
      }
      const isSilentBrollScene = isManualTimingSilentBrollScene(scene, rawScene);

      if (!sourcePhraseIds.length && !isSilentBrollScene) {
        errors.push(`${sceneId}: source_phrase_ids не заполнены.`);
      }
      if (!sourcePhraseIds.length && isSilentBrollScene) {
        const sceneType = String(rawScene?.scene_type || scene?.scene_type || "").trim();
        const goal = String(rawScene?.scene_goal_ru || scene?.scene_goal_ru || "").trim();
        const meaning = String(rawScene?.meaning_hint_ru || scene?.meaning_hint_ru || "").trim();

        if (!sceneType && !goal && !meaning) {
          errors.push(`${sceneId}: silent/b-roll scene без source_phrase_ids должна иметь scene_type, scene_goal_ru или meaning_hint_ru.`);
        }
      }
      sourcePhraseIds.forEach((phraseId) => {
        if (!phraseIds.has(phraseId)) errors.push(`${sceneId}: source_phrase_ids содержит неизвестный phrase_id ${phraseId}.`);
      });
      [
        "translated_text_ru",
        "meaning_hint_ru",
        "scene_goal_ru",
        "photo_prompt_hint_ru",
        "prompt_hint_ru",
        "scene_role_in_block_ru",
        "block_progress_ru",
        "user_note_ru",
        "story_block_id",
        "story_block_title_ru",
        "story_block_position_ru",
      ].forEach((key) => {
        if (!String(scene[key] || "").trim()) errors.push(`${sceneId}: не заполнено поле ${key}.`);
      });
      ["video_prompt", "negative_prompt", "sound_prompt"].forEach((key) => {
        if (String(scene[key] || "").trim()) {
          errors.push(`${sceneId}: Semantic Story Cut не должен заполнять ${key}.`);
        }
      });
    });

    for (let idx = 1; idx < importedScenes.length; idx += 1) {
      const prev = importedScenes[idx - 1];
      const scene = importedScenes[idx];
      const prevId = String(prev?.scene_id || `scene_${idx}`);
      const sceneId = String(scene?.scene_id || `scene_${idx + 1}`);
      const prevEnd = roundTimingSec(prev?.end_sec);
      const start = roundTimingSec(scene?.start_sec);
      if (start < prevEnd - 0.01) {
        errors.push(`${sceneId}: scenes должны быть отсортированы по start_sec и не перекрываться с ${prevId}.`);
      }
    }

    const unknownBlockId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "");
    const storyBlockIds = new Set(rawStoryBlocks.map((block) => String(block?.block_id || block?.blockId || block?.id || "").trim()).filter(Boolean));
    const realStoryBlocks = storyBlocks.filter((block) => String(block.block_id || "") !== unknownBlockId);
    const finalBlockId = String(rawStoryBlocks[rawStoryBlocks.length - 1]?.block_id || rawStoryBlocks[rawStoryBlocks.length - 1]?.blockId || rawStoryBlocks[rawStoryBlocks.length - 1]?.id || "");
    if (finalBlockId === unknownBlockId) errors.push("Финальный story_block не должен быть block_unknown.");

    if (!realStoryBlocks.length) {
      errors.push("story_blocks не заполнены.");
    }
    importedScenes.forEach((scene, idx) => {
      const sceneId = String(scene.scene_id || `scene_${idx + 1}`);
      const blockId = String(scene.story_block_id || "").trim();
      if (blockId && !storyBlockIds.has(blockId)) errors.push(`${sceneId}: story_block_id ${blockId} отсутствует в story_blocks.`);
    });
    realStoryBlocks.forEach((block) => {
      const blockId = String(block.block_id || "block_without_id");
      ["title_ru", "summary_ru", "block_goal_ru", "block_reveal_ru", "block_emotion_ru"].forEach((key) => {
        if (!String(block[key] || "").trim()) errors.push(`${blockId}: не заполнено ${key}.`);
      });
      if (!Array.isArray(block.scene_ids) || !block.scene_ids.length) {
        errors.push(`${blockId}: не заполнены scene_ids.`);
      } else {
        block.scene_ids.forEach((sceneId) => {
          const safeSceneId = String(sceneId || "").trim();
          if (!safeSceneId || !sceneIds.has(safeSceneId)) errors.push(`${blockId}: scene_ids содержит неизвестный scene_id ${safeSceneId || "<empty>"}.`);
        });
      }
    });
    storyBlocks
      .filter((block) => String(block.block_id || "") === unknownBlockId && storyBlockIds.has(unknownBlockId))
      .forEach((block) => {
        const blockId = String(block.block_id || "block_without_id");
        (Array.isArray(block.scene_ids) ? block.scene_ids : []).forEach((sceneId) => {
          const safeSceneId = String(sceneId || "").trim();
          if (!safeSceneId || !sceneIds.has(safeSceneId)) errors.push(`${blockId}: scene_ids содержит неизвестный scene_id ${safeSceneId || "<empty>"}.`);
        });
      });

    return { ok: !errors.length, errors };
  }

  if (importedScenes.length !== baseScenes.length) {
    errors.push(`Количество scenes изменилось: было ${baseScenes.length}, стало ${importedScenes.length}.`);
  }

  const byId = new Map(importedScenes.map((scene) => [String(scene.scene_id || ""), scene]));
  baseScenes.forEach((baseScene) => {
    const sceneId = String(baseScene.scene_id || "");
    const nextScene = byId.get(sceneId);
    if (!nextScene) {
      errors.push(`scene_id изменился или удалён: ${sceneId}.`);
      return;
    }
    ["start_sec", "end_sec", "speech_start_sec", "speech_end_sec"].forEach((key) => {
      if (roundTimingSec(nextScene[key]) !== roundTimingSec(baseScene[key])) {
        errors.push(`${sceneId}: ${key} изменился (${baseScene[key]} → ${nextScene[key]}).`);
      }
    });
    if (!sameManualTimingJson(normalizeManualTimingSourcePhraseIds(nextScene.source_phrase_ids), normalizeManualTimingSourcePhraseIds(baseScene.source_phrase_ids))) {
      errors.push(`${sceneId}: source_phrase_ids изменились.`);
    }
    [
      "translated_text_ru",
      "meaning_hint_ru",
      "scene_goal_ru",
      "photo_prompt_hint_ru",
      "prompt_hint_ru",
      "scene_role_in_block_ru",
      "block_progress_ru",
    ].forEach((key) => {
      if (!String(nextScene[key] || "").trim()) errors.push(`${sceneId}: не заполнено поле ${key}.`);
    });
    ["video_prompt", "negative_prompt", "sound_prompt"].forEach((key) => {
      if (String(nextScene[key] || "").trim()) {
        errors.push(`${sceneId}: Story Pass не должен заполнять ${key}.`);
      }
    });
  });

  const unknownBlockId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "");
  const realStoryBlocks = storyBlocks.filter((block) => String(block.block_id || "") !== unknownBlockId);

  if (!realStoryBlocks.length) {
    errors.push("story_blocks не заполнены.");
  }
  storyBlocks.forEach((block) => {
    const blockId = String(block.block_id || "block_without_id");

    if (blockId === unknownBlockId) {
      return;
    }

    ["title_ru", "summary_ru", "block_goal_ru", "block_reveal_ru", "block_emotion_ru"].forEach((key) => {
      if (!String(block[key] || "").trim()) errors.push(`${blockId}: не заполнено ${key}.`);
    });
    if (!Array.isArray(block.scene_ids) || !block.scene_ids.length) errors.push(`${blockId}: не заполнены scene_ids.`);
  });

  return { ok: !errors.length, errors };
}


export const MANUAL_TIMING_CLIP_PASS_TASK_RU = "Это JSON для Music Clip Pass. Не меняй audio_phrases, scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids. Нужно определить структуру песни: intro, verse, pre_chorus, chorus, drop, bridge, instrumental, outro. Заполни song_blocks и смысловые поля сцен. Назначь route: ia2v только для реального lip-sync/вокальной фразы, i2v для сюжетных/визуальных сцен, i2v_sound для атмосферных сцен со звуком. video_prompt, negative_prompt, sound_prompt оставить пустыми.";

export const MANUAL_TIMING_PODCAST_PASS_TASK_RU = "Это JSON для Podcast / Dialogue Pass. Не меняй audio_phrases, scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids. Нужно определить speakers, topic_blocks, scene_type и поля сцен. Если сцена должна произносить текст через generated voice, заполни narrator_text_en/ru или speaker_text_en/ru из текста сцены. route может быть i2v, i2v_sound или i2v_text. video_prompt, negative_prompt, sound_prompt оставить пустыми.";

function buildManualTimingLockedPassScenes(project = {}) {
  return buildManualTimingExportJson(project).scenes.map((scene) => ({
    ...scene,
    video_prompt: "",
    negative_prompt: "",
    sound_prompt: "",
  }));
}

export function buildManualTimingClipPassJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const exportJson = buildManualTimingExportJson(safeProject);

  return {
    chatgpt_task: MANUAL_TIMING_CLIP_PASS_TASK_RU,
    split_type: "asr_gap_aware_clip_pass",
    project_mode: MANUAL_TIMING_MUSIC_CLIP_MODE,
    project_kind: MANUAL_TIMING_MUSIC_CLIP_PROJECT_KIND,
    format: exportJson.format,
    aspect_ratio: exportJson.aspect_ratio,
    format_locked: exportJson.format_locked,
    audio_duration_sec: Number(audio.duration_sec || exportJson.audio_duration_sec || 0),
    audio_phrases: exportJson.audio_phrases,
    output_fields: {
      song_blocks: ["block_id", "block_type", "title_ru", "summary_ru", "energy_ru", "emotional_function_ru", "start_sec", "end_sec", "scene_ids"],
      scenes: ["song_block_id", "song_block_type", "song_block_title_ru", "lyrics_text", "translated_text_ru", "meaning_hint_ru", "scene_goal_ru", "visual_role_ru", "performance_role_ru", "photo_prompt_hint_ru", "prompt_hint_ru", "route", "lip_sync_required", "vocal_owner_role", "video_prompt", "negative_prompt", "sound_prompt"],
    },
    song_blocks: Array.isArray(safeProject.song_blocks) ? safeProject.song_blocks : [],
    scenes: buildManualTimingLockedPassScenes(safeProject),
  };
}

export function buildManualTimingPodcastPassJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const exportJson = buildManualTimingExportJson(safeProject);

  return {
    chatgpt_task: MANUAL_TIMING_PODCAST_PASS_TASK_RU,
    split_type: "asr_gap_aware_podcast_pass",
    project_mode: MANUAL_TIMING_PODCAST_DIALOGUE_MODE,
    project_kind: MANUAL_TIMING_PODCAST_DIALOGUE_PROJECT_KIND,
    format: exportJson.format,
    aspect_ratio: exportJson.aspect_ratio,
    format_locked: exportJson.format_locked,
    audio_duration_sec: Number(audio.duration_sec || exportJson.audio_duration_sec || 0),
    audio_mode: String(safeProject.audio_mode || "master_with_generated_speech"),
    audio_phrases: exportJson.audio_phrases,
    speakers: Array.isArray(safeProject.speakers) ? safeProject.speakers : [],
    topic_blocks: Array.isArray(safeProject.topic_blocks) ? safeProject.topic_blocks : [],
    output_fields: {
      speakers: ["speaker_id", "label_ru", "role_ru", "voice_profile_ru", "voice_profile_en"],
      topic_blocks: ["block_id", "title_ru", "summary_ru", "topic_goal_ru", "topic_reveal_ru", "emotion_ru", "start_sec", "end_sec", "scene_ids"],
      scenes: ["speaker_id", "speaker_name", "topic_block_id", "topic_block_title_ru", "scene_type", "route", "original_text", "translated_text_ru", "meaning_hint_ru", "narrator_text_en", "narrator_text_ru", "speaker_text_en", "speaker_text_ru", "generated_speech_required", "voice_profile_id", "scene_goal_ru", "broll_hint_ru", "photo_prompt_hint_ru", "prompt_hint_ru", "video_prompt", "negative_prompt", "sound_prompt"],
    },
    scenes: buildManualTimingLockedPassScenes(safeProject),
  };
}

function isManualTimingClipPassPayload(raw = {}) {
  const splitType = String(raw?.split_type || raw?.splitType || "");
  return splitType === "asr_gap_aware_clip_pass";
}

function isManualTimingPodcastPassPayload(raw = {}) {
  const splitType = String(raw?.split_type || raw?.splitType || "");
  return splitType === "asr_gap_aware_podcast_pass";
}

function validateManualTimingLockedPassImport(raw = {}, basePayload = {}, passName = "Pass") {
  const importedAudioPhrases = normalizeManualTimingAudioPhrases(raw?.audio_phrases || raw?.audioPhrases || []);
  const baseAudioPhrases = normalizeManualTimingAudioPhrases(basePayload.audio_phrases);
  const rawScenes = Array.isArray(raw?.scenes) ? raw.scenes : [];
  const importedScenes = rawScenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const baseScenes = basePayload.scenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const errors = [];

  if (!sameManualTimingJson(importedAudioPhrases, baseAudioPhrases)) {
    errors.push("audio_phrases изменились — pass не должен менять ASR-фразы.");
  }
  if (importedScenes.length !== baseScenes.length) {
    errors.push(`Количество scenes изменилось: было ${baseScenes.length}, стало ${importedScenes.length}.`);
  }

  const byId = new Map(importedScenes.map((scene) => [String(scene.scene_id || ""), scene]));
  baseScenes.forEach((baseScene) => {
    const sceneId = String(baseScene.scene_id || "");
    const nextScene = byId.get(sceneId);
    if (!nextScene) {
      errors.push(`${sceneId}: scene_id изменился или удалён.`);
      return;
    }
    ["start_sec", "end_sec", "speech_start_sec", "speech_end_sec"].forEach((key) => {
      if (roundTimingSec(nextScene[key]) !== roundTimingSec(baseScene[key])) {
        errors.push(`${sceneId}: ${key} изменился (${baseScene[key]} → ${nextScene[key]}).`);
      }
    });
    if (!sameManualTimingJson(normalizeManualTimingSourcePhraseIds(nextScene.source_phrase_ids), normalizeManualTimingSourcePhraseIds(baseScene.source_phrase_ids))) {
      errors.push(`${sceneId}: source_phrase_ids изменились.`);
    }
    ["video_prompt", "negative_prompt", "sound_prompt"].forEach((key) => {
      if (String(nextScene[key] || "").trim()) errors.push(`${sceneId}: ${passName} не должен заполнять ${key}.`);
    });
  });

  return { importedScenes, errors };
}

export function validateManualTimingClipPassImport(raw = {}, baseProject = {}) {
  if (!isManualTimingClipPassPayload(raw)) return { ok: true, errors: [] };

  const basePayload = buildManualTimingClipPassJson(baseProject);
  const { importedScenes, errors } = validateManualTimingLockedPassImport(raw, basePayload, "Clip Pass");
  const songBlocks = Array.isArray(raw?.song_blocks) ? raw.song_blocks : [];
  if (!songBlocks.length) errors.push("song_blocks должны быть заполнены.");
  songBlocks.forEach((block, idx) => {
    const blockId = String(block?.block_id || `song_block_${idx + 1}`);
    ["block_type", "title_ru", "summary_ru", "energy_ru", "emotional_function_ru"].forEach((key) => {
      if (!String(block?.[key] || "").trim()) errors.push(`${blockId}: не заполнено ${key}.`);
    });
    if (!Array.isArray(block?.scene_ids) || !block.scene_ids.length) errors.push(`${blockId}: не заполнены scene_ids.`);
  });
  const rawScenes = Array.isArray(raw?.scenes) ? raw.scenes : [];
  importedScenes.forEach((scene, idx) => {
    const sceneId = String(scene.scene_id || "scene");
    if (!String(rawScenes[idx]?.route || rawScenes[idx]?.video_generation_route || "").trim()) errors.push(`${sceneId}: не заполнено поле route.`);
    ["song_block_id", "scene_goal_ru", "photo_prompt_hint_ru", "prompt_hint_ru"].forEach((key) => {
      if (!String(scene[key] || "").trim()) errors.push(`${sceneId}: не заполнено поле ${key}.`);
    });
  });
  return { ok: !errors.length, errors };
}

export function validateManualTimingPodcastPassImport(raw = {}, baseProject = {}) {
  if (!isManualTimingPodcastPassPayload(raw)) return { ok: true, errors: [] };

  const basePayload = buildManualTimingPodcastPassJson(baseProject);
  const { importedScenes, errors } = validateManualTimingLockedPassImport(raw, basePayload, "Podcast Pass");
  const speakers = Array.isArray(raw?.speakers) ? raw.speakers : [];
  const topicBlocks = Array.isArray(raw?.topic_blocks) ? raw.topic_blocks : [];
  if (!speakers.length) errors.push("speakers должны быть заполнены.");
  if (!topicBlocks.length) errors.push("topic_blocks должны быть заполнены.");
  topicBlocks.forEach((block, idx) => {
    const blockId = String(block?.block_id || `topic_block_${idx + 1}`);
    ["title_ru", "summary_ru", "topic_goal_ru", "topic_reveal_ru", "emotion_ru"].forEach((key) => {
      if (!String(block?.[key] || "").trim()) errors.push(`${blockId}: не заполнено ${key}.`);
    });
    if (!Array.isArray(block?.scene_ids) || !block.scene_ids.length) errors.push(`${blockId}: не заполнены scene_ids.`);
  });
  importedScenes.forEach((scene) => {
    const sceneId = String(scene.scene_id || "scene");
    ["topic_block_id", "scene_type", "scene_goal_ru", "photo_prompt_hint_ru", "prompt_hint_ru"].forEach((key) => {
      if (!String(scene[key] || "").trim()) errors.push(`${sceneId}: не заполнено поле ${key}.`);
    });
    if (String(scene.route || "") === "i2v_text") {
      const speechText = String(scene.narrator_text_en || scene.narrator_text_ru || scene.speaker_text_en || scene.speaker_text_ru || "").trim();
      if (!speechText) errors.push(`${sceneId}: route i2v_text требует narrator_text_en/ru или speaker_text_en/ru.`);
    }
  });
  return { ok: !errors.length, errors };
}

export function buildManualTimingSampleJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const audioPhrases = normalizeManualTimingAudioPhrases(safeProject.audio_phrases);
  const existingScenes = Array.isArray(safeProject.scenes) ? safeProject.scenes : [];
  const scenes = existingScenes.length ? buildManualTimingExportJson(safeProject).scenes : [
    {
      scene_id: "seg_01",
      index: 1,
      start_sec: 0,
      end_sec: 5,
      duration_sec: 5,
      section: "intro",
      route: "i2v",
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
      use_sound_suggestion: true,
      energy: "soft",
      quality: "manual_draft",
      boundary_reason: "json_import",
      transition_out: "manual_cut",
      story_time: "настоящее / прошлое / флешбэк",
      scene_type: "intro / performance / flashback / cutaway",
      drama_hint: "Коротко: что происходит драматургически.",
      short_note: "Короткая подпись для карточки сцены.",
      scene_goal_ru: "Зачем нужна сцена в сюжете.",
      photo_prompt_hint_ru: "Что учесть при создании фото.",
      prompt_hint_ru: "Что учесть в видео-промте.",
      story_position_ru: "Позиция в истории.",
      user_note_ru: "Твоя заметка: звук, фраза, визуал, что не забыть.",
      source_phrase_ids: [],
      story_block_id: "block_01",
      story_block_title_ru: "Смысловой блок 1",
      story_block_color: "#F59E0B",
      story_block_position_ru: "сцена 1 из 1 в блоке",
      scene_role_in_block_ru: "Какую функцию выполняет сцена внутри смыслового блока.",
      block_progress_ru: "Как эта сцена продвигает раскрытие блока шаг за шагом.",
      scene_global_context_ru: "Как сцена связана с общей историей и глобальным story bible.",
      continuity_anchor_ru: "Что обязательно должно совпасть с предыдущими/следующими сценами.",
      must_match_project_identity_ru: "Какие элементы должны совпасть с project world/style identity.",
      must_match_block_style_ru: "Какие элементы должны совпасть со стилем текущего блока.",
      original_text: "Original English phrase for this audio segment.",
      translated_text_ru: "Русский перевод фразы.",
      meaning_hint_ru: "Смысл сцены для режиссёра.",
      source_text_en: "",
      adapted_text_en: "",
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: ""
    }
  ];

  let storyBlocks;

  if (existingScenes.length) {
    storyBlocks = normalizeManualTimingStoryBlocks(safeProject.story_blocks);
  } else {
    const normalizedStoryBlocks = normalizeManualTimingStoryBlocks(safeProject.story_blocks);
    const hasCustomStoryBlocks = normalizedStoryBlocks.some((block) => String(block.block_id || "") !== MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id);
    storyBlocks = hasCustomStoryBlocks ? normalizedStoryBlocks : [
      {
        block_id: "block_01",
        title_ru: "Смысловой блок 1",
        summary_ru: "Кратко опиши общий смысл первого блока без привязки к конкретному сюжету.",
        block_goal_ru: "Опиши драматургическую задачу блока по фактическому аудио.",
        block_reveal_ru: "Опиши, какое новое понимание должен получить зритель к концу блока.",
        block_emotion_ru: "эмоциональная дуга блока по фактическому аудио",
        global_story_context_ru: "Как этот блок наследует общий паспорт истории.",
        block_place_in_global_arc_ru: "Место блока в общей драматургической дуге.",
        inherits_style_lock_ru: "Какие style lock правила проекта обязательны для блока.",
        inherits_world_lock_ru: "Какие world lock правила проекта обязательны для блока.",
        inherits_atmosphere_ru: "Как блок наследует общую атмосферу проекта.",
        inherits_continuity_rules_ru: "Какие continuity rules проекта обязательны для блока.",
        color: "#F59E0B",
        scene_ids: ["seg_01"],
        start_sec: 0,
        end_sec: 5,
      },
      { ...MANUAL_TIMING_UNKNOWN_STORY_BLOCK },
    ];
  }

  return {
    chatgpt_task: buildManualTimingChatGptTask(
      audioPhrases.some((phrase) => (
        String(phrase.status || "") === "needs_transcription"
        || String(phrase.assignment_status || "") === "unassigned"
      )),
      audioPhrases.some((phrase) => String(phrase.status || "") === "asr_raw" || String(phrase.source || "") === "asr")
    ),
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    manual_timing_workflow: normalizeManualTimingWorkflow(safeProject.manual_timing_workflow),
    mode: "manual_clip_board",
    project_mode: String(safeProject.project_mode || safeProject.projectMode || ""),
    project_kind: String(safeProject.project_kind || safeProject.projectKind || ""),
    format: String(safeProject.format || safeProject.aspect_ratio || "9:16"),
    aspect_ratio: String(safeProject.aspect_ratio || safeProject.format || "9:16"),
    format_locked: Boolean(safeProject.format_locked),
    split_type: existingScenes.length ? "manual_timing_export_for_chatgpt" : "manual_timing_template_for_chatgpt",
    audio_duration_sec: Number(audio.duration_sec || 0),
    global_hint: "Заполни/поправь story_blocks и scenes: перевод/смысл, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru, scene_role_in_block_ru и block_progress_ru. Prompts оставь пустыми. Если есть audio_phrases из ASR, audio_phrases являются источником истины по речи: не меняй их start_sec/end_sec; scenes должны быть gap-aware, покрывать всю audio_duration_sec без дыр/overlap, а requestedDurationSec для будущей video generation должен соответствовать scene.duration_sec, а не speech duration. Если есть audio_phrases со status=needs_transcription или assignment_status=unassigned, не удаляй их: распознай/переведи и реши, куда назначить phrase_id; не меняй тайминги без явного указания пользователя.",
    story_blocks: storyBlocks,
    audio_phrases: audioPhrases,
    scenes,
  };
}


function manualTimingTextFilled(value) {
  return String(value || "").trim().length > 0;
}

function getManualTimingSceneSourcePhraseIds(scene = {}) {
  return normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds);
}

function manualTimingRangesOverlap(aStart = 0, aEnd = 0, bStart = 0, bEnd = 0) {
  const start = Math.max(Number(aStart || 0), Number(bStart || 0));
  const end = Math.min(Number(aEnd || 0), Number(bEnd || 0));
  return end - start > 0.001;
}

function areManualTimingSourcePhraseIdsEqual(left = [], right = []) {
  if (left.length !== right.length) return false;
  return left.every((id, index) => id === right[index]);
}

function getExpectedManualTimingSourcePhraseIds(scene = {}, normalizedPhrases = []) {
  const startSec = Number(scene?.start_sec ?? scene?.startSec ?? 0);
  const endSec = Number(scene?.end_sec ?? scene?.endSec ?? 0);
  if (!(endSec > startSec)) return [];

  return normalizedPhrases
    .filter((phrase) => manualTimingRangesOverlap(startSec, endSec, phrase.start_sec, phrase.end_sec))
    .map((phrase) => String(phrase.phrase_id || "").trim())
    .filter(Boolean);
}

export function repairManualTimingSourcePhraseIdsFromTiming(scenes = [], audioPhrases = []) {
  const normalizedScenes = Array.isArray(scenes) ? scenes : [];
  const normalizedPhrases = normalizeManualTimingAudioPhrases(audioPhrases);
  const emptyResult = {
    scenes: normalizedScenes,
    repaired: false,
    repairedEmptyCount: 0,
    replacedWrongCount: 0,
  };
  if (!normalizedScenes.length || !normalizedPhrases.length) return emptyResult;

  let repairedEmptyCount = 0;
  let replacedWrongCount = 0;
  const repairedScenes = normalizedScenes.map((scene) => {
    const currentIds = getManualTimingSceneSourcePhraseIds(scene);
    const expectedSourcePhraseIds = getExpectedManualTimingSourcePhraseIds(scene, normalizedPhrases);

    if (!currentIds.length && !expectedSourcePhraseIds.length) {
      return scene;
    }

    if (!currentIds.length) {
      repairedEmptyCount += 1;
      return { ...scene, source_phrase_ids: expectedSourcePhraseIds };
    }

    if (!areManualTimingSourcePhraseIdsEqual(currentIds, expectedSourcePhraseIds)) {
      replacedWrongCount += 1;
      return { ...scene, source_phrase_ids: expectedSourcePhraseIds };
    }

    return scene;
  });

  const repairedCount = repairedEmptyCount + replacedWrongCount;
  if (repairedCount > 0) {
    console.info("[MANUAL TIMING SOURCE_PHRASE_IDS_REPAIRED]", {
      repairedEmptyCount,
      replacedWrongCount,
      sceneCount: normalizedScenes.length,
      audioPhraseCount: normalizedPhrases.length,
    });
  }

  return {
    scenes: repairedScenes,
    repaired: repairedCount > 0,
    repairedEmptyCount,
    replacedWrongCount,
  };
}

export function inferManualTimingCompletedStages(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const scenes = Array.isArray(safeProject.scenes) ? safeProject.scenes : [];
  const storyBlocks = normalizeManualTimingStoryBlocks(safeProject.story_blocks);
  const completedStages = [];
  const knownBlockId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "block_unknown");
  const hasRealBlocks = storyBlocks.some((block) => {
    const blockId = String(block?.block_id || block?.id || "").trim();
    return blockId && blockId !== knownBlockId;
  });
  const semanticFields = [
    "translated_text_ru",
    "meaning_hint_ru",
    "scene_goal_ru",
    "photo_prompt_hint_ru",
    "prompt_hint_ru",
    "scene_role_in_block_ru",
  ];
  const scenesWithMeaning = scenes.filter((scene) => semanticFields.some((key) => manualTimingTextFilled(scene?.[key])));
  const scenesWithSource = scenes.filter((scene) => getManualTimingSceneSourcePhraseIds(scene).length || manualTimingTextFilled(scene?.source_phrase_id));
  const majoritySceneCount = Math.max(1, Math.ceil(scenes.length * 0.7));
  if (scenes.length > 0 && hasRealBlocks && scenesWithMeaning.length >= majoritySceneCount && scenesWithSource.length >= majoritySceneCount) {
    completedStages.push("semantic_story_cut");
  }

  const storyBibleRequired = ["project_story_summary_ru", "project_visual_bible_ru", "project_continuity_rules_ru"];
  const storyBibleOptional = ["project_style_lock_ru", "project_world_lock_ru"];
  if (
    storyBibleRequired.every((key) => manualTimingTextFilled(safeProject?.[key]))
    && storyBibleOptional.some((key) => manualTimingTextFilled(safeProject?.[key]))
  ) {
    completedStages.push("story_bible");
  }

  const storyboardFields = [
    "storyboard_frame_role_ru",
    "i2v_prompt_en",
    "composition_ru",
    "camera_angle_ru",
    "subject_lock_ru",
    "background_lock_ru",
  ];
  const scenesWithStoryboard = scenes.filter((scene) => (
    manualTimingTextFilled(scene?.storyboard_frame_role_ru)
    && (manualTimingTextFilled(scene?.source_image_prompt_en) || manualTimingTextFilled(scene?.source_image_prompt_ru))
    && manualTimingTextFilled(scene?.i2v_prompt_en)
    && storyboardFields.some((key) => manualTimingTextFilled(scene?.[key]))
  ));
  const blocksWithStoryboard = storyBlocks.filter((block) => manualTimingTextFilled(block?.block_visual_bible_ru) || manualTimingTextFilled(block?.block_storyboard_summary_ru));
  if (scenes.length > 0 && scenesWithStoryboard.length >= majoritySceneCount && blocksWithStoryboard.length > 0) {
    completedStages.push("block_storyboard");
  }

  if (completedStages.length) {
    console.info("[MANUAL TIMING WORKFLOW_INFERRED]", {
      completedStages,
      sceneCount: scenes.length,
      storyBlockCount: storyBlocks.length,
    });
  }
  return completedStages;
}

function normalizeManualTimingWorkflowWithInferredFallback(safeRaw = {}, safeBase = {}, projectForInference = {}) {
  const isCurrentTimingBackupImport = String(safeRaw?.backup_type || safeRaw?.backupType || "") === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE;
  const rawWorkflow = safeRaw?.manual_timing_workflow || safeRaw?.manualTimingWorkflow || (isCurrentTimingBackupImport ? undefined : safeBase?.manual_timing_workflow);
  const hasCompletedStages = Array.isArray(rawWorkflow?.completed_stages) && rawWorkflow.completed_stages.length > 0;
  const inferredCompletedStages = hasCompletedStages ? [] : inferManualTimingCompletedStages(projectForInference);
  return normalizeManualTimingWorkflow(rawWorkflow, inferredCompletedStages);
}

export function normalizeManualTimingProjectFromJson(raw = {}, baseProject = {}) {
  const safeRaw = unwrapManualProjectBackupJson(raw && typeof raw === "object" ? raw : {});
  const safeBase = baseProject && typeof baseProject === "object" ? baseProject : {};
  const modeAndKind = pickManualTimingModeAndKind(safeRaw, safeBase);
  const baseAudio = normalizeManualTimingAudio(safeRaw.audio || safeRaw.audio_metadata || safeBase.audio);
  const rawDuration = Number(safeRaw.audio_duration_sec ?? safeRaw.audioDurationSec ?? safeRaw.duration_sec ?? safeRaw.durationSec ?? safeRaw.audio?.duration_sec ?? safeRaw.audio_metadata?.duration_sec ?? 0);
  const durationSec = roundTimingSec(baseAudio.duration_sec || rawDuration || 0);
  const isCurrentTimingBackupImport = String(safeRaw?.backup_type || safeRaw?.backupType || "") === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE;
  const isSemanticStoryCutImport = !isCurrentTimingBackupImport && isSemanticManualTimingStoryPassPayload(safeRaw);
  const rawStoryBlocksForImport = Array.isArray(safeRaw.story_blocks) ? safeRaw.story_blocks : (isSemanticStoryCutImport ? [] : safeBase.story_blocks);
  const rawHasUnknownStoryBlock = Array.isArray(rawStoryBlocksForImport) && rawStoryBlocksForImport.some((block) => String(block?.block_id || block?.blockId || block?.id || "") === MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id);
  const normalizedStoryBlocks = normalizeManualTimingStoryBlocks(rawStoryBlocksForImport);
  const storyBlocks = isSemanticStoryCutImport && !rawHasUnknownStoryBlock
    ? normalizedStoryBlocks.filter((block) => String(block.block_id || "") !== MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id)
    : normalizedStoryBlocks;
  const audioPhrases = normalizeManualTimingAudioPhrases(safeRaw.audio_phrases || safeRaw.audioPhrases || safeBase.audio_phrases);
  const vocalAsrGaps = Array.isArray(safeRaw.vocal_asr_gaps)
    ? safeRaw.vocal_asr_gaps
    : (Array.isArray(safeRaw?.asr_phrase_map?.gaps)
      ? safeRaw.asr_phrase_map.gaps
      : (Array.isArray(safeBase.vocal_asr_gaps) ? safeBase.vocal_asr_gaps : []));
  const rawScenes = Array.isArray(safeRaw.scenes) ? safeRaw.scenes : [];
  const importedScenes = rawScenes
    .map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx))
    .filter((scene) => Number(scene.end_sec) > Number(scene.start_sec));

  const markerValues = [];
  importedScenes.forEach((scene) => {
    markerValues.push(scene.start_sec);
    markerValues.push(scene.end_sec);
  });
  if (durationSec > 0) {
    markerValues.push(0);
    markerValues.push(durationSec);
  }

  if (Array.isArray(safeRaw.markers)) markerValues.push(...safeRaw.markers);
  const markers = normalizeManualTimingMarkers(markerValues, durationSec || importedScenes[importedScenes.length - 1]?.end_sec || 0);
  const finalDuration = durationSec || markers[markers.length - 1] || 0;
  const markerScenes = (isSemanticStoryCutImport || isCurrentTimingBackupImport)
    ? importedScenes
    : (markers.length >= 2
      ? buildManualTimingScenesFromMarkers(markers, importedScenes, { durationSec: finalDuration, preserveSceneIds: true })
      : importedScenes);
  const sourcePhraseRepair = repairManualTimingSourcePhraseIdsFromTiming(markerScenes, audioPhrases);
  const scenes = hydrateManualTimingScenesWithStoryBlocks(sourcePhraseRepair.scenes, storyBlocks);
  const inferenceProject = { ...safeBase, ...safeRaw, scenes, story_blocks: storyBlocks, audio_phrases: audioPhrases };

  return {
    ...getDefaultManualTimingNodeData(),
    ...safeBase,
    project_mode: modeAndKind.project_mode,
    project_kind: modeAndKind.project_kind,
    format: String(safeRaw.format || safeRaw.aspect_ratio || safeBase.format || safeBase.aspect_ratio || "9:16"),
    aspect_ratio: String(safeRaw.aspect_ratio || safeRaw.format || safeBase.aspect_ratio || safeBase.format || "9:16"),
    format_locked: Boolean(safeRaw.format_locked ?? safeBase.format_locked),
    ...pickManualTimingProjectStoryBibleFields({ ...safeBase, ...safeRaw }),
    audio: {
      ...baseAudio,
      duration_sec: finalDuration || baseAudio.duration_sec || 0,
      duration_ms: Math.round((finalDuration || baseAudio.duration_sec || 0) * 1000),
    },
    timing_status: String(safeRaw.timing_status || safeBase.timing_status || "draft"),
    manual_scene_edits: Boolean(safeRaw.manual_scene_edits ?? safeRaw.manualSceneEdits ?? safeBase.manual_scene_edits ?? safeBase.manualSceneEdits),
    manualSceneEdits: Boolean(safeRaw.manualSceneEdits ?? safeRaw.manual_scene_edits ?? safeBase.manualSceneEdits ?? safeBase.manual_scene_edits),
    lastManualEditReason: String(safeRaw.lastManualEditReason || safeRaw.last_manual_edit_reason || safeBase.lastManualEditReason || safeBase.last_manual_edit_reason || ""),
    markers,
    story_blocks: storyBlocks,
    song_blocks: Array.isArray(safeRaw.song_blocks) ? safeRaw.song_blocks : (Array.isArray(safeBase.song_blocks) ? safeBase.song_blocks : []),
    speakers: Array.isArray(safeRaw.speakers) ? safeRaw.speakers : (Array.isArray(safeBase.speakers) ? safeBase.speakers : []),
    topic_blocks: Array.isArray(safeRaw.topic_blocks) ? safeRaw.topic_blocks : (Array.isArray(safeBase.topic_blocks) ? safeBase.topic_blocks : []),
    audio_mode: String(safeRaw.audio_mode || safeBase.audio_mode || ""),
    vocal_asr_source: safeRaw.vocal_asr_source || safeRaw.vocalAsrSource || safeBase.vocal_asr_source || safeBase.vocalAsrSource || null,
    vocal_asr_split_preset: String(
      safeRaw.vocal_asr_split_preset
      || safeRaw.vocalAsrSplitPreset
      || safeBase.vocal_asr_split_preset
      || safeBase.vocalAsrSplitPreset
      || "song_lines"
    ),
    vocal_asr_language: String(
      safeRaw.vocal_asr_language
      || safeRaw.vocalAsrLanguage
      || safeBase.vocal_asr_language
      || safeBase.vocalAsrLanguage
      || "auto"
    ),
    vocal_asr_gaps: vocalAsrGaps,
    audio_phrases: audioPhrases,
    scenes,
    manual_timing_workflow: normalizeManualTimingWorkflowWithInferredFallback(safeRaw, safeBase, inferenceProject),
    selectedSceneId: String(safeRaw.selectedSceneId || scenes[0]?.scene_id || ""),
    updatedAt: safeRaw.updatedAt || Date.now(),
  };
}


export function getManualTimingSceneDurationWarning(scene = {}) {
  const start = Number(scene?.start_sec || 0);
  const end = Number(scene?.end_sec || 0);
  const durationSec = Number(scene?.duration_sec || (end - start));
  if (!Number.isFinite(durationSec) || durationSec <= 0) return null;

  const route = String(scene?.route || "").trim().toLowerCase();

  if (durationSec < 3.0) {
    return {
      type: "too_short",
      severity: "warning",
      label: "короткая",
      text: "Сцена короткая: LTX может не успеть раскрыть движение. Использовать можно для быстрых монтажных ударов.",
    };
  }
  if (durationSec < 3.5) {
    return {
      type: "short_but_ok",
      severity: "soft",
      label: "коротковата",
      text: "Сцена коротковата, но допустима. Лучше использовать простое действие.",
    };
  }
  if (route === "ia2v" && durationSec > 6.5 && durationSec < 8.0) {
    return {
      type: "ia2v_long",
      severity: "soft",
      label: "ia2v длинновата",
      text: "Lip-sync сцена длинновата. Можно оставить, если фраза цельная, но лучше 3.5–6.5 сек.",
    };
  }
  if (route === "i2v" && durationSec > 7.5 && durationSec < 8.0) {
    return {
      type: "i2v_long",
      severity: "soft",
      label: "i2v длинновата",
      text: "i2v сцена длинновата. Можно оставить для атмосферы, но движение должно быть простым.",
    };
  }
  if (durationSec >= 8.0 && durationSec < 10.0) {
    return {
      type: "long",
      severity: "warning",
      label: "длинная",
      text: "Сцена длинная: лучше держать простое движение или разделить.",
    };
  }
  if (durationSec >= 10.0) {
    return {
      type: "very_long",
      severity: "danger",
      label: "очень длинная",
      text: "Очень длинная сцена: лучше разделить на несколько сцен.",
    };
  }

  return null;
}

export function buildManualTimingWarnings(project = {}) {
  const audio = normalizeManualTimingAudio(project.audio);
  const scenes = Array.isArray(project.scenes) ? project.scenes : [];
  const audioPhrases = normalizeManualTimingAudioPhrases(project.audio_phrases);
  const warnings = [];
  const duration = Number(audio.duration_sec || 0);
  const durationWarningBuckets = new Map();

  if (!scenes.length) warnings.push("Нет сегментов разметки.");
  if (scenes.length) {
    const coverage = validateSceneCoverage(scenes, duration);
    warnings.push(...coverage.errors, ...coverage.warnings);
  }

  scenes.forEach((scene, idx) => {
    const start = Number(scene.start_sec || 0);
    const end = Number(scene.end_sec || 0);
    const dur = Number(scene.duration_sec || (end - start));
    if (idx > 0) {
      const prevEnd = Number(scenes[idx - 1]?.end_sec || 0);
      if (Math.abs(start - prevEnd) > 0.01) warnings.push(`${scene.scene_id}: есть разрыв или наложение с предыдущей сценой.`);
    }
    if (dur < 1.0) warnings.push(`${scene.scene_id}: длительность меньше 1 сек.`);
    if (dur > 9.0) warnings.push(`${scene.scene_id}: длительность больше 9 сек — проверь, не склеены ли разные фразы.`);
    if (scene.route === "ia2v" && !scene.contains_vocal) warnings.push(`${scene.scene_id}: ia2v стоит на участке без вокала.`);
    if (["intro", "instrumental"].includes(String(scene.section || "")) && scene.route === "ia2v") warnings.push(`${scene.scene_id}: секция “${sectionLabelRu(scene.section)}”, но выбран route=ia2v.`);

    const durationWarning = getManualTimingSceneDurationWarning(scene);
    if (durationWarning) {
      const bucketKey = durationWarning.type;
      const bucket = durationWarningBuckets.get(bucketKey) || { label: durationWarning.label, items: [] };
      bucket.items.push(`${scene.scene_id} (${roundTimingSec(dur).toFixed(3)} сек)`);
      durationWarningBuckets.set(bucketKey, bucket);
    }
  });

  const assignedPhraseIds = new Set();
  scenes.forEach((scene) => {
    normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds).forEach((phraseId) => assignedPhraseIds.add(phraseId));
  });

  audioPhrases.forEach((phrase) => {
    if (String(phrase.source || "") === "asr" && !assignedPhraseIds.has(String(phrase.phrase_id || ""))) {
      warnings.push(`ASR-фраза не назначена ни одной scene через source_phrase_ids: ${phrase.phrase_id}`);
    }

    if (String(phrase.status || "") === "needs_transcription") {
      warnings.push(`Есть пропущенная фраза без расшифровки: ${phrase.phrase_id} (${phrase.start_sec.toFixed(2)}–${phrase.end_sec.toFixed(2)})`);
    }

    if (String(phrase.assignment_status || "") === "unassigned") {
      warnings.push(`Пропущенная фраза ещё не назначена сцене: ${phrase.phrase_id}`);
    }
  });

  const durationWarningOrder = [
    ["too_short", "Короткие сцены"],
    ["short_but_ok", "Коротковатые сцены"],
    ["ia2v_long", "Lip-sync сцены длинноваты"],
    ["i2v_long", "i2v сцены длинноваты"],
    ["long", "Длинные сцены"],
    ["very_long", "Очень длинные сцены"],
  ];
  durationWarningOrder.forEach(([type, title]) => {
    const bucket = durationWarningBuckets.get(type);
    if (!bucket?.items?.length) return;
    warnings.push(`${title}: ${bucket.items.join(", ")}`);
  });

  const isStoryVoiceover = String(project?.project_mode || project?.projectMode || "") === MANUAL_TIMING_STORY_VOICEOVER_MODE
    || String(project?.project_kind || project?.projectKind || "") === MANUAL_TIMING_STORY_PROJECT_KIND;
  if (isStoryVoiceover) {
    if (!audioPhrases.length) warnings.push("Нет audio_phrases — сначала создайте Audio Phrase Map.");
    const hasStoryScenes = scenes.some((scene) => normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds).length);
    const hasImportedStoryPass = scenes.some((scene) => String(scene?.translated_text_ru || scene?.meaning_hint_ru || scene?.scene_goal_ru || scene?.prompt_hint_ru || "").trim());
    if (hasStoryScenes && !hasImportedStoryPass) warnings.push("Semantic Story Cut ещё не заполнен — скопируйте JSON для Semantic Story Cut и импортируйте результат.");
    if (hasStoryScenes) {
      scenes.forEach((scene) => {
        [
          "translated_text_ru",
          "meaning_hint_ru",
          "scene_goal_ru",
          "photo_prompt_hint_ru",
          "prompt_hint_ru",
          "scene_role_in_block_ru",
          "block_progress_ru",
        ].forEach((key) => {
          if (!String(scene?.[key] || "").trim()) warnings.push(`${scene.scene_id || "scene"}: пустое смысловое поле ${key}.`);
        });
      });
    }
  }

  return [...new Set(warnings)];
}

export function buildManualTimingExportJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const audio_phrases = normalizeManualTimingAudioPhrases(safeProject.audio_phrases);
  const normalizedStoryBlocks = normalizeManualTimingStoryBlocks(safeProject.story_blocks);
  const hydratedScenes = hydrateManualTimingScenesWithStoryBlocks(Array.isArray(safeProject.scenes) ? safeProject.scenes : [], normalizedStoryBlocks);
  const scenes = hydratedScenes.map((scene, idx) => ({
    scene_id: String(scene?.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene?.index || idx + 1),
    start_sec: roundTimingSec(scene?.start_sec),
    end_sec: roundTimingSec(scene?.end_sec),
    duration_sec: roundTimingSec(scene?.duration_sec || (Number(scene?.end_sec || 0) - Number(scene?.start_sec || 0))),
    source_kind: String(scene?.source_kind ?? scene?.sourceKind ?? (scene?.scene_type === "manual_silence" ? "silence" : "audio")),
    source_start_sec: scene?.source_start_sec ?? scene?.sourceStartSec ?? null,
    source_end_sec: scene?.source_end_sec ?? scene?.sourceEndSec ?? null,
    is_silence: Boolean(scene?.is_silence || scene?.isSilence || scene?.scene_type === "manual_silence"),
    composer_source_kind: String(scene?.composer_source_kind || scene?.composerSourceKind || ""),
    composer_source_audio_id: String(scene?.composer_source_audio_id || scene?.composerSourceAudioId || ""),
    composer_source_audio_name: String(scene?.composer_source_audio_name || scene?.composerSourceAudioName || ""),
    composer_source_start_sec: scene?.composer_source_start_sec ?? scene?.composerSourceStartSec ?? null,
    composer_source_end_sec: scene?.composer_source_end_sec ?? scene?.composerSourceEndSec ?? null,
    composer_block_id: String(scene?.composer_block_id || scene?.composerBlockId || ""),
    composer_block_type: String(scene?.composer_block_type || scene?.composerBlockType || ""),
    composer_block_label: String(scene?.composer_block_label || scene?.composerBlockLabel || ""),
    composer_role_label: String(scene?.composer_role_label || scene?.composerRoleLabel || ""),
    composer_saved_clip_id: String(scene?.composer_saved_clip_id || scene?.composerSavedClipId || ""),
    composer_saved_clip_label: String(scene?.composer_saved_clip_label || scene?.composerSavedClipLabel || ""),
    speech_start_sec: roundTimingSec(scene?.speech_start_sec ?? scene?.speechStartSec ?? scene?.start_sec),
    speech_end_sec: roundTimingSec(scene?.speech_end_sec ?? scene?.speechEndSec ?? scene?.end_sec),
    pre_silence_sec: roundTimingSec(scene?.pre_silence_sec ?? scene?.preSilenceSec ?? Math.max(0, Number(scene?.speech_start_sec ?? scene?.start_sec ?? 0) - Number(scene?.start_sec ?? 0))),
    post_silence_sec: roundTimingSec(scene?.post_silence_sec ?? scene?.postSilenceSec ?? Math.max(0, Number(scene?.end_sec ?? 0) - Number(scene?.speech_end_sec ?? scene?.end_sec ?? 0))),
    section: normalizeManualTimingSection(scene?.section),
    route: normalizeManualTimingRoute(scene?.route),
    contains_vocal: Boolean(scene?.contains_vocal),
    contains_vocal_assumption: Boolean(scene?.contains_vocal_assumption ?? scene?.contains_vocal),
    contains_instrumental_assumption: Boolean(scene?.contains_instrumental_assumption ?? !scene?.contains_vocal),
    use_sound_suggestion: Boolean(scene?.use_sound_suggestion),
    energy: normalizeManualTimingEnergy(scene?.energy),
    quality: String(scene?.quality || (safeProject.timing_status === "confirmed" ? "manual_confirmed" : "manual_draft")),
    format: String(scene?.format || scene?.aspect_ratio || safeProject.format || safeProject.aspect_ratio || "9:16"),
    aspect_ratio: String(scene?.aspect_ratio || scene?.format || safeProject.aspect_ratio || safeProject.format || "9:16"),
    boundary_reason: String(scene?.boundary_reason || "manual_marker"),
    transition_out: String(scene?.transition_out || "manual_cut"),
    story_time: String(scene?.story_time || ""),
    scene_type: String(scene?.scene_type || ""),
    drama_hint: String(scene?.drama_hint || ""),
    short_note: String(scene?.short_note || ""),
    scene_goal_ru: String(scene?.scene_goal_ru || ""),
    photo_prompt_hint_ru: String(scene?.photo_prompt_hint_ru || ""),
    prompt_hint_ru: String(scene?.prompt_hint_ru || scene?.photo_prompt_hint_ru || ""),
    story_position_ru: String(scene?.story_position_ru || scene?.story_time || ""),
    user_note_ru: String(scene?.user_note_ru || ""),
    source_phrase_ids: normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds),
    story_block_id: String(scene?.story_block_id || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id),
    story_block_title_ru: String(scene?.story_block_title_ru || ""),
    story_block_color: normalizeStoryBlockColor(scene?.story_block_color || ""),
    story_block_position_ru: String(scene?.story_block_position_ru || ""),
    scene_role_in_block_ru: String(scene?.scene_role_in_block_ru || ""),
    block_progress_ru: String(scene?.block_progress_ru || ""),
    scene_global_context_ru: String(scene?.scene_global_context_ru || ""),
    continuity_anchor_ru: String(scene?.continuity_anchor_ru || ""),
    must_match_project_identity_ru: String(scene?.must_match_project_identity_ru || ""),
    must_match_block_style_ru: String(scene?.must_match_block_style_ru || ""),
    storyboard_frame_role_ru: String(scene?.storyboard_frame_role_ru || ""),
    source_image_prompt_en: String(scene?.source_image_prompt_en || ""),
    source_image_prompt_ru: String(scene?.source_image_prompt_ru || ""),
    source_image_negative_prompt_en: String(scene?.source_image_negative_prompt_en || ""),
    i2v_prompt_en: String(scene?.i2v_prompt_en || ""),
    i2v_negative_prompt_en: String(scene?.i2v_negative_prompt_en || ""),
    composition_ru: String(scene?.composition_ru || ""),
    camera_angle_ru: String(scene?.camera_angle_ru || ""),
    subject_lock_ru: String(scene?.subject_lock_ru || ""),
    background_lock_ru: String(scene?.background_lock_ru || ""),
    continuity_from_previous_scene_ru: String(scene?.continuity_from_previous_scene_ru || ""),
    must_keep_same_ru: String(scene?.must_keep_same_ru || ""),
    allowed_variation_ru: String(scene?.allowed_variation_ru || ""),
    original_text: String(scene?.original_text || ""),
    translated_text_ru: String(scene?.translated_text_ru || ""),
    meaning_hint_ru: String(scene?.meaning_hint_ru || ""),
    source_text_en: String(scene?.source_text_en || ""),
    adapted_text_en: String(scene?.adapted_text_en || ""),
    video_prompt: String(scene?.video_prompt || ""),
    negative_prompt: String(scene?.negative_prompt || ""),
    sound_prompt: String(scene?.sound_prompt || ""),
    song_block_id: String(scene?.song_block_id || ""),
    song_block_type: String(scene?.song_block_type || ""),
    song_block_title_ru: String(scene?.song_block_title_ru || ""),
    lyrics_text: String(scene?.lyrics_text || ""),
    visual_role_ru: String(scene?.visual_role_ru || ""),
    performance_role_ru: String(scene?.performance_role_ru || ""),
    lip_sync_required: Boolean(scene?.lip_sync_required),
    vocal_owner_role: String(scene?.vocal_owner_role || ""),
    speaker_id: String(scene?.speaker_id || ""),
    speaker_name: String(scene?.speaker_name || ""),
    topic_block_id: String(scene?.topic_block_id || ""),
    topic_block_title_ru: String(scene?.topic_block_title_ru || ""),
    narrator_text_en: String(scene?.narrator_text_en || ""),
    narrator_text_ru: String(scene?.narrator_text_ru || ""),
    speaker_text_en: String(scene?.speaker_text_en || ""),
    speaker_text_ru: String(scene?.speaker_text_ru || ""),
    generated_speech_required: Boolean(scene?.generated_speech_required),
    voice_profile_id: String(scene?.voice_profile_id || ""),
    voice_profile: String(scene?.voice_profile || ""),
    narrator_voice_profile_en: String(scene?.narrator_voice_profile_en || ""),
    negative_voice_traits: String(scene?.negative_voice_traits || ""),
    broll_hint_ru: String(scene?.broll_hint_ru || ""),
  }));

  const story_blocks = syncManualTimingStoryBlocksWithScenes(normalizedStoryBlocks, scenes).map(({ scene_count, ...block }) => block);
  const hasUnresolvedAudioPhrases = audio_phrases.some((phrase) => (
    String(phrase.status || "") === "needs_transcription"
    || String(phrase.assignment_status || "") === "unassigned"
  ));
  const maxSceneEndSec = scenes.reduce((max, scene) => Math.max(max, Number(scene?.end_sec || 0)), 0);
  const exportAudioDurationSec = Math.max(Number(audio.duration_sec || 0), roundTimingSec(maxSceneEndSec));
  const podcastEditManifest = safeProject.podcast_edit_manifest || safeProject.composer_edit_manifest || null;

  return {
    chatgpt_task: buildManualTimingChatGptTask(
      hasUnresolvedAudioPhrases,
      audio_phrases.some((phrase) => String(phrase.status || "") === "asr_raw" || String(phrase.source || "") === "asr")
    ),
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    manual_timing_workflow: normalizeManualTimingWorkflow(safeProject.manual_timing_workflow),
    mode: "manual_clip_board",
    project_mode: String(safeProject.project_mode || safeProject.projectMode || ""),
    project_kind: String(safeProject.project_kind || safeProject.projectKind || ""),
    format: String(safeProject.format || safeProject.aspect_ratio || "9:16"),
    aspect_ratio: String(safeProject.aspect_ratio || safeProject.format || "9:16"),
    format_locked: Boolean(safeProject.format_locked),
    ...pickManualTimingProjectStoryBibleFields(safeProject),
    split_type: safeProject.timing_status === "confirmed" ? "manual_timing_confirmed" : "manual_timing_draft",
    audio_duration_sec: exportAudioDurationSec,
    source_audio_duration_sec: Number(audio.duration_sec || 0),
    timeline_duration_sec: exportAudioDurationSec,
    podcast_edit_manifest: podcastEditManifest,
    composer_edit_manifest: podcastEditManifest,
    global_hint: safeProject.timing_status === "confirmed" ? "Manual timing confirmed by user" : "Manual timing draft",
    story_blocks,
    song_blocks: Array.isArray(safeProject.song_blocks) ? safeProject.song_blocks : [],
    speakers: Array.isArray(safeProject.speakers) ? safeProject.speakers : [],
    topic_blocks: Array.isArray(safeProject.topic_blocks) ? safeProject.topic_blocks : [],
    audio_mode: String(safeProject.audio_mode || ""),
    vocal_asr_source: safeProject.vocal_asr_source || safeProject.vocalAsrSource || null,
    vocal_asr_split_preset: String(safeProject.vocal_asr_split_preset || safeProject.vocalAsrSplitPreset || "song_lines"),
    vocal_asr_language: String(safeProject.vocal_asr_language || safeProject.vocalAsrLanguage || "auto"),
    audio_phrases,
    vocal_asr_gaps: Array.isArray(safeProject?.vocal_asr_gaps)
      ? safeProject.vocal_asr_gaps
      : (Array.isArray(safeProject?.asr_phrase_map?.gaps) ? safeProject.asr_phrase_map.gaps : []),
    scenes,
  };
}
