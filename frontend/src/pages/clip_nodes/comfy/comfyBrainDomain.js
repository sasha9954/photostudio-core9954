const RENDER_PROFILE_OPTIONS = ["comfy image", "comfy text"];
export const AUDIO_STORY_MODE_OPTIONS = ["lyrics_music", "music_only", "music_plus_text"];

const REFERENCE_HANDLE_TO_ROLE = {
  ref_character_1: "character_1",
  ref_character_2: "character_2",
  ref_character_3: "character_3",
  ref_animal: "animal",
  ref_group: "group",
  ref_location: "location",
  ref_style: "style",
  ref_props: "props",
};

const ROLE_PRIORITY = ["character_1", "character_2", "character_3", "animal", "group", "location", "props", "style"];
const VISUAL_ANCHOR_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"];

const STORY_OVERRIDE_MARKERS = ["не по песне", "другой сюжет", "separate story", "different story", "not literal lyrics"];
const STORY_ENHANCEMENT_MARKERS = ["усили", "усилить", "enhance", "intensify", "emphasize", "make more cinematic"];

export const PROMPT_SYNC_STATUS = {
  synced: "synced",
  needsSync: "needs_sync",
  syncing: "syncing",
  syncError: "sync_error",
};

function computePromptSync({ ru = "", en = "" } = {}) {
  const ruText = String(ru || "").trim();
  const enText = String(en || "").trim();
  if (ruText && enText) return PROMPT_SYNC_STATUS.synced;
  return PROMPT_SYNC_STATUS.needsSync;
}

export function normalizeComfyScenePrompts(scene = {}) {
  const imagePromptRu = String(scene?.imagePromptRu || scene?.imagePrompt || "").trim();
  const imagePromptEn = String(scene?.imagePromptEn || scene?.imagePrompt || "").trim();
  const videoPromptRu = String(scene?.videoPromptRu || scene?.videoPrompt || "").trim();
  const videoPromptEn = String(scene?.videoPromptEn || scene?.videoPrompt || "").trim();
  const imagePromptSyncStatus = [PROMPT_SYNC_STATUS.synced, PROMPT_SYNC_STATUS.needsSync, PROMPT_SYNC_STATUS.syncing, PROMPT_SYNC_STATUS.syncError].includes(scene?.imagePromptSyncStatus)
    ? scene.imagePromptSyncStatus
    : computePromptSync({ ru: imagePromptRu, en: imagePromptEn });
  const videoPromptSyncStatus = [PROMPT_SYNC_STATUS.synced, PROMPT_SYNC_STATUS.needsSync, PROMPT_SYNC_STATUS.syncing, PROMPT_SYNC_STATUS.syncError].includes(scene?.videoPromptSyncStatus)
    ? scene.videoPromptSyncStatus
    : computePromptSync({ ru: videoPromptRu, en: videoPromptEn });
  const refsUsed = Array.isArray(scene?.refsUsed)
    ? scene.refsUsed.map((role) => String(role || "").trim()).filter(Boolean)
    : [];
  const primaryRole = String(scene?.primaryRole || "").trim();
  const fallbackHero = primaryRole || refsUsed[0] || "";
  const heroEntityId = String(scene?.heroEntityId || fallbackHero).trim();
  const mustAppear = Array.isArray(scene?.mustAppear)
    ? scene.mustAppear.map((role) => String(role || "").trim()).filter(Boolean)
    : (heroEntityId ? [heroEntityId] : refsUsed);
  return {
    ...scene,
    imagePromptRu,
    imagePromptEn,
    videoPromptRu,
    videoPromptEn,
    imagePrompt: imagePromptEn,
    videoPrompt: videoPromptEn,
    refsUsed,
    primaryRole,
    heroEntityId,
    mustAppear,
    videoPanelOpen: Boolean(scene?.videoPanelOpen || String(scene?.videoUrl || "").trim()),
    imagePromptSyncStatus,
    videoPromptSyncStatus,
  };
}


export function normalizeRenderProfile(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return RENDER_PROFILE_OPTIONS.includes(normalized) ? normalized : "comfy image";
}

export function normalizeAudioStoryMode(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return AUDIO_STORY_MODE_OPTIONS.includes(normalized) ? normalized : "lyrics_music";
}

export function normalizeRoleList(input = []) {
  const unique = new Set();
  (Array.isArray(input) ? input : []).forEach((raw) => {
    const role = REFERENCE_HANDLE_TO_ROLE[raw] || String(raw || "").trim().toLowerCase();
    if (ROLE_PRIORITY.includes(role)) unique.add(role);
  });
  return ROLE_PRIORITY.filter((role) => unique.has(role));
}

export function deriveSceneRoles({ refsByRole = {} } = {}) {
  const cast = normalizeRoleList(Object.keys(refsByRole).filter((role) => Array.isArray(refsByRole[role]) && refsByRole[role].length > 0));
  const castSubjects = cast.filter((role) => ["character_1", "character_2", "character_3", "animal", "group"].includes(role));
  const primarySubject = castSubjects[0] || cast[0] || "character_1";
  const secondarySubjects = castSubjects.filter((role) => role !== primarySubject);
  return { primarySubject, secondarySubjects, cast };
}

export function canGenerateComfyImage(plannerInput = {}) {
  const refsByRole = plannerInput?.refsByRole || {};
  return VISUAL_ANCHOR_ROLES.some((role) => Array.isArray(refsByRole[role]) && refsByRole[role].length > 0);
}

export function inferPropAnchorLabel(refsByRole = {}) {
  const firstProp = (Array.isArray(refsByRole.props) ? refsByRole.props : [])[0];
  return String(firstProp?.name || "").trim() || "hero prop";
}

function normalizeStoryHeuristicText(value = "") {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function hasStoryMarker(input = "", markers = []) {
  if (!input) return false;
  return markers.some((marker) => input.includes(marker));
}

export function detectStoryControlMode({ meaningfulText = "", meaningfulAudio = "", refsByRole = {} } = {}) {
  const text = normalizeStoryHeuristicText(meaningfulText);
  const hasText = !!text;
  const hasAudio = !!String(meaningfulAudio || "").trim();
  const hasRefs = Object.values(refsByRole || {}).some((items) => Array.isArray(items) && items.length > 0);
  if (!hasText && !hasAudio && !hasRefs) return "insufficient_input";
  if (!hasText && !hasAudio && hasRefs) return "refs_mode_generated";
  if (hasText && !hasAudio) return "text_override";
  if (!hasText && hasAudio) return "audio_primary";
  if (hasStoryMarker(text, STORY_OVERRIDE_MARKERS)) return "text_override";
  if (hasStoryMarker(text, STORY_ENHANCEMENT_MARKERS)) return "audio_enhanced_by_text";
  return "hybrid_balanced";
}

export function deriveStoryNarrativeRoles(storyControlMode = "insufficient_input") {
  if (storyControlMode === "text_override") return { textNarrativeRole: "story_mission_primary", audioNarrativeRole: "rhythm_emotion_support" };
  if (storyControlMode === "audio_primary") return { textNarrativeRole: "optional_intent_hint", audioNarrativeRole: "timeline_and_emotional_backbone" };
  if (storyControlMode === "audio_enhanced_by_text") return { textNarrativeRole: "dramatic_boost", audioNarrativeRole: "timeline_backbone" };
  if (storyControlMode === "refs_mode_generated") return { textNarrativeRole: "none", audioNarrativeRole: "none" };
  return { textNarrativeRole: "shared_story_driver", audioNarrativeRole: "shared_timeline_driver" };
}

export function buildStoryMissionSummary({ meaningfulText = "", storyControlMode = "insufficient_input", mode = "clip" } = {}) {
  const text = String(meaningfulText || "").trim();
  if (text) return text.slice(0, 220);
  if (storyControlMode === "audio_primary") return `Build ${mode} scenes from audio rhythm and emotional contour.`;
  if (storyControlMode === "refs_mode_generated") return `Build ${mode} scenes from references and mode semantics.`;
  return `Build ${mode} scenes with coherent narrative progression.`;
}

export function getModeSemantics(mode = "clip") {
  const key = String(mode || "clip").toLowerCase();
  if (key === "kino") return { modeIntent: "cinematic causality", modePromptBias: "dramatic logic", modeSceneStrategy: "narrative chain", modeContinuityBias: "strong continuity", planningMindset: "director" };
  if (key === "reklama") return { modeIntent: "commercial persuasion", modePromptBias: "product focus", modeSceneStrategy: "hook-value-payoff", modeContinuityBias: "brand consistency", planningMindset: "creative strategist" };
  if (key === "scenario") return { modeIntent: "structured storyboard", modePromptBias: "clarity", modeSceneStrategy: "beat-by-beat", modeContinuityBias: "script continuity", planningMindset: "screenwriter" };
  return { modeIntent: "music-driven montage", modePromptBias: "rhythm and visual energy", modeSceneStrategy: "beats and transitions", modeContinuityBias: "motif continuity", planningMindset: "music video director" };
}

export function getStyleSemantics(stylePreset = "realism") {
  const key = String(stylePreset || "realism").toLowerCase();
  const map = {
    realism: "natural light and believable texture",
    film: "cinematic grading and dramatic light",
    neon: "high contrast neon accents",
    glossy: "clean premium commercial polish",
    soft: "gentle light and airy mood",
  };
  return { styleSummary: map[key] || map.realism, styleContinuity: `Keep ${key} style continuity across all scenes.` };
}

export function buildComfyGlobalContinuity({ plannerInput = {}, refsByRole = {}, sceneRoleModel = {} } = {}) {
  const mode = plannerInput.mode || "clip";
  const style = plannerInput.stylePreset || "realism";
  const cast = (sceneRoleModel.cast || []).join(", ") || "character_1";
  const world = ["location", "props", "style"].filter((role) => (refsByRole[role] || []).length > 0).join(", ") || "implicit world";
  return `Mode ${mode}. Style ${style}. Keep cast (${cast}) and world anchors (${world}) consistent scene-to-scene.`;
}

export function buildComfyScenesFromPlanner({ plannerInput = {}, plannerMeta = {} } = {}) {
  const count = Number(plannerMeta?.summary?.sceneCount || 6);
  const defaultDuration = 3;
  return Array.from({ length: Math.max(1, Math.min(12, count)) }).map((_, idx) => {
    const imagePromptRu = `Кадр ${idx + 1} в стиле ${plannerInput.stylePreset || "realism"}. ${plannerInput.storyMissionSummary || ""}`.trim();
    const imagePromptEn = `Create frame ${idx + 1} in ${plannerInput.stylePreset || "realism"} style. ${plannerInput.storyMissionSummary || ""}`.trim();
    const videoPromptRu = `Анимируй кадр ${idx + 1} с согласованным движением камеры и ритмом.`;
    const videoPromptEn = `Animate frame ${idx + 1} with coherent transition and rhythm cues.`;
    return {
      sceneId: `comfy-scene-${idx + 1}`,
      title: `Scene ${idx + 1}`,
      startSec: idx * defaultDuration,
      endSec: (idx + 1) * defaultDuration,
      durationSec: defaultDuration,
      sceneNarrativeStep: `step_${idx + 1}`,
      sceneGoal: plannerInput.storyMissionSummary || "advance story",
      storyMission: plannerInput.storyMissionSummary || "",
      sceneOutputRule: "scene image first",
      primaryRole: plannerMeta?.sceneRoleModel?.primarySubject || "character_1",
      secondaryRoles: plannerMeta?.sceneRoleModel?.secondarySubjects || [],
      continuity: plannerMeta?.globalContinuity || "",
      imagePrompt: imagePromptEn,
      videoPrompt: videoPromptEn,
      imagePromptRu,
      imagePromptEn,
      videoPromptRu,
      videoPromptEn,
      imagePromptSyncStatus: PROMPT_SYNC_STATUS.synced,
      videoPromptSyncStatus: PROMPT_SYNC_STATUS.synced,
      refsUsed: [],
      refDirectives: {},
      heroEntityId: plannerMeta?.sceneRoleModel?.primarySubject || "character_1",
      supportEntityIds: plannerMeta?.sceneRoleModel?.secondarySubjects || [],
      mustAppear: [plannerMeta?.sceneRoleModel?.primarySubject || "character_1"],
      mustNotAppear: [],
      environmentLock: true,
      styleLock: true,
      identityLock: true,
      roleSelectionReason: "mock_default_role_selection",
      imageUrl: "",
      videoUrl: "",
      videoPanelOpen: false,
      plannerMeta,
    };
  });
}

export function buildMockComfyScenes(meta = {}) {
  const plannerInput = meta?.plannerInput || {};
  const plannerMeta = {
    mode: String(plannerInput?.mode || meta?.mode || "clip"),
    output: normalizeRenderProfile(plannerInput?.output || meta?.output || "comfy image"),
    stylePreset: String(plannerInput?.stylePreset || meta?.stylePreset || "realism"),
    narrativeSource: String(plannerInput?.narrativeSource || meta?.narrativeSource || "none"),
    timelineSource: String(plannerInput?.timelineSource || meta?.timelineSource || "logic"),
    warnings: Array.isArray(meta?.warnings) ? meta.warnings : [],
    summary: meta?.summary || {},
    sceneRoleModel: meta?.sceneRoleModel || deriveSceneRoles({ refsByRole: plannerInput?.refsByRole || {} }),
    referenceSummary: meta?.referenceSummary || {},
    storyControlMode: String(plannerInput?.storyControlMode || meta?.storyControlMode || "insufficient_input"),
    storyMissionSummary: String(plannerInput?.storyMissionSummary || meta?.storyMissionSummary || ""),
    textNarrativeRole: String(plannerInput?.textNarrativeRole || meta?.textNarrativeRole || ""),
    audioNarrativeRole: String(plannerInput?.audioNarrativeRole || meta?.audioNarrativeRole || ""),
    audioStoryMode: normalizeAudioStoryMode(plannerInput?.audioStoryMode || meta?.audioStoryMode || "lyrics_music"),
  };
  plannerMeta.globalContinuity = buildComfyGlobalContinuity({ plannerInput, refsByRole: plannerInput?.refsByRole || {}, sceneRoleModel: plannerMeta.sceneRoleModel });
  return buildComfyScenesFromPlanner({ plannerInput, plannerMeta });
}

export function deriveComfyBrainState({ nodeId = "", nodeData = {}, nodesNow = [], edgesNow = [], normalizeRefDataFn } = {}) {
  const incoming = (edgesNow || []).filter((e) => e.target === nodeId);
  const pickConnectedNode = (handleId) => {
    const edge = [...incoming].reverse().find((e) => String(e.targetHandle || "") === handleId);
    return edge ? (nodesNow.find((x) => x.id === edge.source) || null) : null;
  };

  const comfyRefConfigByHandle = {
    ref_character_1: { nodeType: "refNode", kind: "ref_character" },
    ref_location: { nodeType: "refNode", kind: "ref_location" },
    ref_style: { nodeType: "refNode", kind: "ref_style" },
    ref_props: { nodeType: "refNode", kind: "ref_items" },
    ref_character_2: { nodeType: "refCharacter2" },
    ref_character_3: { nodeType: "refCharacter3" },
    ref_animal: { nodeType: "refAnimal" },
    ref_group: { nodeType: "refGroup" },
  };

  const extractRefsFromSourceNode = (sourceNode, cfg = {}) => {
    if (!sourceNode || sourceNode?.type !== cfg.nodeType) return [];
    if (cfg.kind && sourceNode?.data?.kind !== cfg.kind) return [];
    if (cfg.nodeType === "refNode" && typeof normalizeRefDataFn === "function") {
      return normalizeRefDataFn(sourceNode?.data || {}, cfg.kind || "").refs;
    }
    const refs = Array.isArray(sourceNode?.data?.refs) ? sourceNode.data.refs : [];
    return refs.map((item) => ({ url: String(item?.url || "").trim(), name: String(item?.name || "").trim() })).filter((item) => !!item.url);
  };

  const refsByRole = {
    character_1: extractRefsFromSourceNode(pickConnectedNode("ref_character_1"), comfyRefConfigByHandle.ref_character_1),
    character_2: extractRefsFromSourceNode(pickConnectedNode("ref_character_2"), comfyRefConfigByHandle.ref_character_2),
    character_3: extractRefsFromSourceNode(pickConnectedNode("ref_character_3"), comfyRefConfigByHandle.ref_character_3),
    animal: extractRefsFromSourceNode(pickConnectedNode("ref_animal"), comfyRefConfigByHandle.ref_animal),
    group: extractRefsFromSourceNode(pickConnectedNode("ref_group"), comfyRefConfigByHandle.ref_group),
    location: extractRefsFromSourceNode(pickConnectedNode("ref_location"), comfyRefConfigByHandle.ref_location),
    style: extractRefsFromSourceNode(pickConnectedNode("ref_style"), comfyRefConfigByHandle.ref_style),
    props: extractRefsFromSourceNode(pickConnectedNode("ref_props"), comfyRefConfigByHandle.ref_props),
  };

  const audioNode = pickConnectedNode("audio");
  const textNode = pickConnectedNode("text");
  const modeValue = String(nodeData?.mode || "clip").toLowerCase();
  const outputValue = normalizeRenderProfile(nodeData?.output || "comfy image");
  const audioStoryMode = normalizeAudioStoryMode(nodeData?.audioStoryMode || "lyrics_music");
  const stylePreset = String(nodeData?.styleKey || "realism").toLowerCase();
  const freezeStyle = !!nodeData?.freezeStyle;
  const meaningfulAudio = audioNode?.type === "audioNode" ? String(audioNode?.data?.audioUrl || "").trim() : "";
  const audioDurationRaw = Number(audioNode?.data?.audioDurationSec || 0);
  const meaningfulAudioDurationSec = Number.isFinite(audioDurationRaw) && audioDurationRaw > 0 ? audioDurationRaw : null;
  const meaningfulText = textNode?.type === "textNode" ? String(textNode?.data?.textValue || "").trim() : "";
  const storyControlMode = detectStoryControlMode({ meaningfulText, meaningfulAudio, refsByRole });
  const narrativeRoles = deriveStoryNarrativeRoles(storyControlMode);
  const narrativeSource = meaningfulText && meaningfulAudio ? "text+audio" : meaningfulText ? "text" : meaningfulAudio ? "audio" : "refs";
  const timelineSource = meaningfulAudio ? "audio rhythm" : "logic";
  const modeSemantics = getModeSemantics(modeValue);
  const styleSemantics = getStyleSemantics(stylePreset);
  const storyMissionSummary = buildStoryMissionSummary({ meaningfulText, storyControlMode, mode: modeValue });

  return {
    modeValue,
    outputValue,
    audioStoryMode,
    stylePreset,
    freezeStyle,
    meaningfulText,
    meaningfulAudio,
    meaningfulAudioDurationSec,
    refsByRole,
    storyControlMode,
    narrativeRoles,
    narrativeSource,
    timelineSource,
    modeSemantics,
    styleSemantics,
    storyMissionSummary,
  };
}

export function extractComfyDebugFields({ plannerInput = {}, plannerMeta = {} } = {}) {
  return {
    mode: plannerInput.mode,
    output: plannerInput.output,
    stylePreset: plannerInput.stylePreset,
    storyControlMode: plannerInput.storyControlMode,
    narrativeSource: plannerInput.narrativeSource,
    timelineSource: plannerInput.timelineSource,
    audioStoryMode: plannerInput.audioStoryMode,
    warnings: plannerMeta.warnings || [],
    globalContinuity: plannerMeta.globalContinuity || "",
    primaryRole: plannerMeta?.sceneRoleModel?.primarySubject || "character_1",
    secondaryRoles: plannerMeta?.sceneRoleModel?.secondarySubjects || [],
    pipelineFlow: "brain → per-scene prompts/rules → scene image → scene video",
  };
}
