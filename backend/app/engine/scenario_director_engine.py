import ast
import base64
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import requests

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.core.config import settings
from app.core.static_paths import ASSETS_DIR, BACKEND_DIR
from app.engine.audio_analyzer import (
    analyze_audio,
    analyze_audio_semantics,
    analyze_audio_semantics_fallback,
    derive_audio_semantic_profile,
)
from app.engine.gemini_rest import post_generate_content
from app.engine.prompt_layers import build_ltx_video_canon_block

ALLOWED_SOURCE_MODES = {"audio", "video_file", "video_link"}
ALLOWED_LTX_MODES = {"i2v", "i2v_as", "f_l", "f_l_as", "continuation", "lip_sync", "lip_sync_music"}
ALLOWED_NARRATION_MODES = {"full", "duck", "pause"}
ALLOWED_EXPLICIT_ROLE_TYPES = {"hero", "support", "antagonist", "auto"}
GEMINI_SCENE_ROUTE_ENUM = ("i2v", "lip_sync_music", "first_last")
GEMINI_ROUTE_TO_WORKFLOW_KEY = {
    "i2v": "i2v",
    "lip_sync_music": "lip_sync",
    "first_last": "f_l",
}
LIP_SYNC_PERFORMANCE_FRAMINGS = {"tight_medium", "medium", "three_quarter", "close_emotional"}
NON_LIP_ACTION_FRAMINGS = {"wide_action", "full_body_action", "medium"}
LIP_SYNC_SPIN_RISK_MARKERS = (
    "spin",
    "spinning",
    "twirl",
    "twirling",
    "swirl",
    "swirling dress",
    "flowing dress",
    "dramatic sweep",
    "dress sweep",
    "full-body silhouette",
    "overhead dance spectacle",
    "whip-turn",
    "whip turn",
    "full-body spin",
    "rotation-first choreography",
    "risky rotation",
)
LIP_SYNC_PERFORMANCE_MARKERS = (
    "sing",
    "singer",
    "lyric",
    "mouth",
    "articulation",
    "eye contact",
    "to camera",
    "performance",
)
FINAL_LINE_MARKERS = (
    "final line",
    "final lines",
    "last line",
    "last lines",
    "final phrase",
    "last phrase",
    "closing line",
    "ending lyric",
    "final lyric",
    "final vocal",
    "last vocal",
)
DIRECT_PERFORMANCE_MARKERS = (
    "direct to camera",
    "to camera",
    "looks into camera",
    "eye contact",
    "performance",
    "performs",
    "performing",
    "sing",
    "sings",
    "singing",
    "vocal delivery",
    "lyric articulation",
)
NON_LIP_PORTRAIT_MARKERS = (
    "portrait",
    "face close",
    "face-only",
    "upper torso",
    "close-up",
    "close up",
    "headshot",
)
NON_LIP_ACTION_MARKERS = (
    "walk",
    "step",
    "move",
    "zone",
    "space",
    "turn",
    "pivot",
    "gesture",
    "track",
    "atmosphere",
    "crowd",
    "environment",
)
NON_LIP_RISKY_ROTATION_MARKERS = (
    "spin-first",
    "spin first",
    "full-body spin",
    "full body spin",
    "aggressive twirl",
    "twirl-first",
    "twirl first",
    "fast whip-turn",
    "fast whip turn",
    "rotation-first choreography",
    "rotation first choreography",
    "dramatic dress-sweep",
    "dramatic dress sweep",
)
DEFAULT_TEXT_MODEL = (getattr(settings, "GEMINI_TEXT_MODEL", None) or "gemini-3.1-pro-preview").strip() or "gemini-3.1-pro-preview"
FALLBACK_TEXT_MODEL = (getattr(settings, "GEMINI_TEXT_MODEL_FALLBACK", None) or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC = (1.5, 4.0, 8.0)
LEGACY_START_FRAME_ALIASES = {
    "previous_last_frame": "previous_frame",
    "prev_last": "previous_frame",
    "last_frame": "previous_frame",
}
JSON_ONLY_RETRY_SUFFIX = (
    "\n\nRETRY OVERRIDE: Output ONLY one JSON object. No markdown. No commentary. No comments. "
    "No alternative versions. Keep the same backend contract and return flat scene fields. "
    "HARD CONTRACT: narration_mode must be present in every scene, must be a string, must never be null, and allowed values are full, duck, pause. "
    "If unsure use full."
)
AUDIO_FIRST_JSON_RETRY_SUFFIX = (
    "\n\nRETRY OVERRIDE (AUDIO-FIRST): Return ONLY one JSON object. No markdown. No commentary. No comments. "
    "Keep exact required top-level contract keys: transcript (array), audioStructure (object), semanticTimeline (array), scenes (array). "
    "Additional keys are allowed only if all required keys remain present and correctly typed."
)
MASTER_JSON_RETRY_SUFFIX = (
    "\n\nRETRY OVERRIDE: Return ONLY JSON. No markdown. No comments. "
    "MASTER MODE ONLY. DO NOT generate scenes. Keep fields short."
)
SCENES_JSON_RETRY_SUFFIX = (
    "\n\nRETRY OVERRIDE: Return ONLY JSON. No markdown. No comments. "
    "SCENES MODE ONLY. Keep short fields only."
)

logger = logging.getLogger(__name__)

CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY = {
    "i2v": "image-video.json",
    "f_l": "imag-imag-video-bz.json",
    "lip_sync_music": "image-lipsink-video-music.json",
    "i2v_sound": "image-video-golos-zvuk.json",
    "f_l_sound": "imag-imag-video-zvuk.json",
}
CLIP_CANONICAL_WORKFLOW_KEY_BY_FILE = {
    value.lower(): key for key, value in CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY.items()
}
CLIP_LEGACY_WORKFLOW_ALIASES = {
    "image-video": "i2v",
    "imag-imag-video-bz": "f_l",
    "image-lipsink-video-music": "lip_sync_music",
    "image-video-golos-zvuk": "i2v_sound",
    "imag-imag-video-zvuk": "f_l_sound",
    "lip_sync": "lip_sync_music",
    "i2v_as": "i2v",
    "f_l_as": "f_l",
}


def _normalize_workflow_filename(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw if raw.endswith(".json") else f"{raw}.json"


def _resolve_workflow_key_and_file(value: Any, *, fallback_key: str = "i2v") -> tuple[str, str]:
    raw_value = str(value or "").strip()
    normalized_raw = raw_value.lower()
    aliased_key = CLIP_LEGACY_WORKFLOW_ALIASES.get(normalized_raw, normalized_raw)
    workflow_key = aliased_key if aliased_key in CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY else fallback_key
    workflow_file = _normalize_workflow_filename(raw_value)
    if workflow_file:
        workflow_key = CLIP_CANONICAL_WORKFLOW_KEY_BY_FILE.get(workflow_file.lower(), workflow_key)
    if not workflow_file:
        workflow_file = CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY.get(workflow_key, CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY["i2v"])
    return workflow_key, workflow_file


def _normalize_scene_canon_by_route(
    *,
    route: str,
    description: str,
    performance_framing: str,
    content_tags: list[str] | None = None,
) -> tuple[str, str]:
    normalized_description = str(description or "").strip()
    framing = str(performance_framing or "").strip().lower()
    tags_text = " ".join([str(tag).strip().lower() for tag in (content_tags or []) if str(tag).strip()])
    descriptor_text = f"{normalized_description.lower()} {tags_text}".strip()
    is_empty_description = not normalized_description
    has_risky_rotation_bias = any(marker in descriptor_text for marker in (*LIP_SYNC_SPIN_RISK_MARKERS, *NON_LIP_RISKY_ROTATION_MARKERS))
    if route == "lip_sync_music":
        spin_risk_count = sum(1 for marker in LIP_SYNC_SPIN_RISK_MARKERS if marker in descriptor_text)
        performance_signal_count = sum(1 for marker in LIP_SYNC_PERFORMANCE_MARKERS if marker in descriptor_text)
        if spin_risk_count >= 2 or (spin_risk_count >= 1 and performance_signal_count == 0):
            normalized_description = (
                "Singer performs emotionally to camera with clear lyric articulation; face, mouth, neck, shoulders, and upper torso "
                "stay readable, phrase-driven expressive hands support meaning, and camera motion stays controlled with gentle push/pull or side arc."
            )
        elif is_empty_description:
            normalized_description = (
                "Singer-performance-first moment: emotion is delivered through singing, with readable face/mouth/neck/shoulders/upper torso, "
                "phrase-driven hand acting, subtle upper-body pulse, and beat-driven emotional intensity escalation."
            )
        elif performance_signal_count == 0:
            normalized_description = (
                f"{normalized_description}. Singer remains camera-readable with emotional lyric delivery, clear mouth articulation, and phrase-driven hand/upper-body performance."
            ).strip(". ")
        if normalized_description and "beat-driven emotional intensity" not in normalized_description.lower():
            normalized_description = (
                f"{normalized_description}. Beat progression drives emotional intensity and performance energy."
            ).strip(". ")
        if normalized_description and "face/mouth readability is mandatory" not in normalized_description.lower():
            normalized_description = (
                f"{normalized_description}. Performer-first lip-sync canon: face/mouth readability is mandatory, eye line stays toward camera or near-camera, "
                "camera must stay slow/controlled/readable (gentle push-in, slight lateral drift, slow eye-level arc), and forbid overhead orbit/top-down rotation/camera roll/fast retreat."
            ).strip(". ")
        if framing not in LIP_SYNC_PERFORMANCE_FRAMINGS:
            framing = "tight_medium"
    elif route in {"i2v", "first_last"}:
        portrait_signal = any(marker in descriptor_text for marker in NON_LIP_PORTRAIT_MARKERS)
        action_signal = any(marker in descriptor_text for marker in NON_LIP_ACTION_MARKERS)
        if has_risky_rotation_bias:
            sanitized_scene_meaning = _strip_risky_rotation_markers(normalized_description)
            normalized_description = (
                f"{sanitized_scene_meaning}. Scene drives forward through space with safe step/pivot/gesture progression, changing body angles, "
                "and camera-led reveal/tracking/parallax; beat shapes mood and intensity without rotation-first choreography."
            ).strip(". ")
        elif portrait_signal and not action_signal:
            normalized_description = (
                "Action-driven beat in a readable venue zone: performer moves through space with safe walking/pivot/gesture progression, "
                "camera builds dynamics with tracking/angle changes, and atmosphere evolves with light and environment."
            )
        elif is_empty_description:
            normalized_description = (
                "Beat-led non-lip scene with movement through space, safe step/pivot/gesture progression, evolving body angles, and camera-led "
                "reveal/tracking/parallax; beat shapes mood, intensity, and energy progression."
            )
        elif normalized_description and "beat shapes mood" not in normalized_description.lower():
            normalized_description = (
                f"{normalized_description}. Beat shapes mood, intensity, and energy progression."
            ).strip(". ")
        if normalized_description and "model-safe choreography" not in normalized_description.lower():
            normalized_description = (
                f"{normalized_description}. Motion safety canon: smooth dynamic nightclub realism with controlled musical movement, "
                "phrase-shaped readable accents, moderate amplitude, controlled rotation only, and no jerky dance/flailing limbs/abrupt spins/hair-whip/torso-snapping/crowd turbulence."
            ).strip(". ")
        if framing not in NON_LIP_ACTION_FRAMINGS:
            establishing_signal = any(token in descriptor_text for token in ("establish", "venue reveal", "wide reveal", "crowd scale", "panorama"))
            framing = "wide_action" if establishing_signal else "medium"
    return normalized_description.strip(), framing


def _strip_risky_rotation_markers(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = raw
    risky_phrases = sorted(
        set((*LIP_SYNC_SPIN_RISK_MARKERS, *NON_LIP_RISKY_ROTATION_MARKERS)),
        key=len,
        reverse=True,
    )
    for phrase in risky_phrases:
        token = str(phrase or "").strip()
        if not token:
            continue
        cleaned = re.sub(rf"\b{re.escape(token)}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,;:.])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:.]){2,}", r"\1", cleaned)
    cleaned = cleaned.strip(" ,;:.")
    return cleaned


def _rewrite_risky_rotation_markers(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    rewritten = raw
    phrase_rewrites: list[tuple[str, str]] = [
        (r"\bbegins to\s+(?:spin|twirl|swirl)\b", "begins a controlled turn-like movement"),
        (r"\b(?:spin-first|spin first|twirl-first|twirl first|rotation-first choreography|rotation first choreography)\b", "controlled rotational suggestion through body angle, step, and fabric movement"),
        (r"\b(?:fast whip-turn|fast whip turn|whip-turn|whip turn)\b", "safe pivot with controlled camera progression"),
        (r"\b(?:full-body spin|full body spin|aggressive twirl|risky rotation)\b", "dress-led motion beat with safe pivot and flowing fabric response"),
        (r"\b(?:dramatic dress-sweep|dramatic dress sweep|dress sweep|dramatic sweep)\b", "flowing fabric response with controlled body angle change"),
        (r"\boverhead dance spectacle\b", "camera reads motion through spatial progression, not sharp rotation"),
    ]
    for pattern, replacement in phrase_rewrites:
        rewritten = re.sub(pattern, replacement, rewritten, flags=re.IGNORECASE)

    word_rewrites: list[tuple[str, str]] = [
        (r"\bspinning\b", "in controlled turn-like motion"),
        (r"\btwirling\b", "in controlled turn-like motion"),
        (r"\bswirling\b", "with flowing fabric response"),
        (r"\bspin\b", "controlled turn-like movement"),
        (r"\btwirl\b", "controlled turn-like movement"),
        (r"\bswirl\b", "controlled flowing movement"),
    ]
    for pattern, replacement in word_rewrites:
        rewritten = re.sub(pattern, replacement, rewritten, flags=re.IGNORECASE)

    rewritten = re.sub(r"\s{2,}", " ", rewritten)
    rewritten = re.sub(r"\s+([,;:.])", r"\1", rewritten)
    rewritten = re.sub(r"([,;:.]){2,}", r"\1", rewritten)
    return rewritten.strip(" ,;:.")


def _normalize_image_prompt_by_route(*, route: str, image_prompt: str, fallback_text: str = "") -> str:
    route_key = str(route or "").strip().lower()
    base = _rewrite_risky_rotation_markers(image_prompt)
    if not base:
        base = _rewrite_risky_rotation_markers(fallback_text)
    if route_key == "lip_sync_music":
        if not base:
            return (
                "Singer-performance-first frame: emotional lyric delivery with readable face/mouth/neck/shoulders/upper torso, "
                "phrase-driven expressive hands, controlled upper-body pulse, subtle forward intention/weight shift, and beat-driven emotional intensity."
            )
        return (
            f"{base}. Singer-performance-first readability: emotional lyric delivery through face/mouth/neck/shoulders/upper torso, "
            "phrase-driven hand acting, controlled upper-body pulse, and beat-driven emotional intensity."
        ).strip()
    if route_key in {"i2v", "first_last"}:
        if not base:
            return (
                "Action-space progression with safe step/pivot/gesture, zone progression, evolving body angles, and camera-led dynamism; "
                "beat shapes mood and intensity."
            )
        return (
            f"{base}. Movement stays spatial and safe with step/pivot/gesture progression, evolving body angles, camera-led dynamism, "
            "and beat-shaped mood/intensity."
        ).strip()
    return base or str(image_prompt or "").strip() or str(fallback_text or "").strip()

SCENARIO_CANONICAL_ROLES = (
    "character_1",
    "character_2",
    "character_3",
    "animal",
    "animal_1",
    "group",
    "group_faces",
    "location",
    "style",
    "props",
)
SCENARIO_CAST_ROLES = {"character_1", "character_2", "character_3", "animal", "animal_1", "group", "group_faces"}
SCENARIO_ROLE_ALIASES = {
    "character1": "character_1",
    "character2": "character_2",
    "character3": "character_3",
    "animal1": "animal_1",
    "group_face": "group_faces",
}
DUO_SCENE_HINTS = {"duo", "reunion", "shared-presence", "shared_presence", "two-shot", "two_shot", "joint", "together", "both"}
DUO_SCENE_EXTRA_HINTS = {
    "two shot",
    "shared",
    "meet",
    "meets",
    "meeting",
    "hug",
    "hugs",
    "walk together",
    "walking together",
}
GROUP_NARRATIVE_REQUIRED_HINTS = {
    "protest",
    "riot",
    "mob",
    "audience",
    "crowd chant",
    "mass panic",
    "chorus",
    "митинг",
    "бунт",
    "толпа",
    "массов",
    "хор",
}
WORLD_ANCHOR_ROLES = {"location", "style", "props", "animal", "animal_1", "group", "group_faces"}
SCENARIO_WORLD_ROLES = {"location", "style", "props"}
SCENARIO_CONTENT_TYPE_REGISTRY: dict[str, dict[str, Any]] = {
    "story": {
        "label": "История",
        "description": "Базовый сюжетный режим",
        "is_enabled": True,
        "mode_family": "narrative",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "balanced_story",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "story_arc",
        "pacing_profile": "balanced",
    },
    "music_video": {
        "label": "Клип",
        "description": "Музыкальный режим с опорой на master audio",
        "is_enabled": True,
        "mode_family": "performance",
        "uses_global_music_prompt": False,
        "supports_lip_sync": True,
        "supports_audio_slices": True,
        "default_ltx_strategy": "i2v",
        "prefers_close_face_for_lipsync": True,
        "clipWorkflowDefault": "i2v",
        "clipWorkflowLipSync": "lip_sync_music",
        "clipWorkflowSound": "i2v_sound",
        "clipWorkflowFirstLast": "f_l",
        "clipWorkflowFirstLastSound": "f_l_sound",
        "narrative_priority": "beat_sync",
        "pacing_profile": "rhythmic",
    },
    "ad": {
        "label": "Реклама",
        "description": "Коммерческий режим",
        "is_enabled": True,
        "mode_family": "commercial",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "brand_focus",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "offer_clarity",
        "pacing_profile": "concise",
    },
    "cartoon": {
        "label": "Мультфильм",
        "description": "Стилизация и выразительная подача",
        "is_enabled": False,
        "mode_family": "stylized",
        "uses_global_music_prompt": True,
        "supports_lip_sync": True,
        "supports_audio_slices": False,
        "default_ltx_strategy": "stylized_motion",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "expressive_action",
        "pacing_profile": "playful",
    },
    "teaser": {
        "label": "Тизер",
        "description": "Короткий интригующий формат",
        "is_enabled": False,
        "mode_family": "promo",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "hook_first",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "intrigue",
        "pacing_profile": "punchy",
    },
    "series": {
        "label": "Сериал",
        "description": "Эпизодический режим",
        "is_enabled": False,
        "mode_family": "episodic",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "episodic_continuity",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "episode_progression",
        "pacing_profile": "steady",
    },
    "film": {
        "label": "Фильм",
        "description": "Полнометражная кинематографическая подача",
        "is_enabled": False,
        "mode_family": "cinematic",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "cinematic_long_arc",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "feature_arc",
        "pacing_profile": "cinematic",
    },
    "comics": {
        "label": "Комикс",
        "description": "Панельный/графический стиль",
        "is_enabled": False,
        "mode_family": "stylized",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "panel_like",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "panel_story",
        "pacing_profile": "framed",
    },
    "documentary": {
        "label": "Документалка",
        "description": "Фактическое повествование",
        "is_enabled": False,
        "mode_family": "factual",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": True,
        "default_ltx_strategy": "observational",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "fact_priority",
        "pacing_profile": "measured",
    },
    "trailer": {
        "label": "Трейлер",
        "description": "Пиковый промо-монтаж",
        "is_enabled": False,
        "mode_family": "promo",
        "uses_global_music_prompt": False,
        "supports_lip_sync": False,
        "supports_audio_slices": True,
        "default_ltx_strategy": "impact_cut",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "peaks_only",
        "pacing_profile": "impact",
    },
}

WEAK_SCENE_PATTERNS = (
    "character walks",
    "walks with determination",
    "camera follows",
    "tense cinematic moment",
    "mysterious atmosphere",
    "cinematic scene",
    "dramatic shot",
    "the subject moves",
    "the character looks around",
    "moody lighting",
    "suspenseful moment",
)
GENERIC_SCENE_GOALS = {
    "",
    "scene",
    "moment",
    "transition",
    "build mood",
    "set tone",
    "cinematic moment",
    "dramatic moment",
}
ABSTRACT_AUDIO_ONLY_WORDS = {"mood", "tension", "emotion", "feeling", "vibe", "atmosphere", "energy", "tone"}
CONCRETE_AUDIO_HINT_WORDS = {
    "door",
    "tunnel",
    "bunker",
    "missile",
    "desert",
    "facility",
    "map",
    "rock",
    "entrance",
    "corridor",
    "weapon",
    "radar",
    "silo",
    "base",
    "launch",
    "operation",
    "checkpoint",
    "control",
    "shaft",
    "vault",
}
WORLD_AUDIO_KEYWORDS = {
    "iran",
    "bunker",
    "tunnel",
    "missile",
    "military",
    "desert",
    "facility",
    "silo",
    "underground",
    "base",
    "corridor",
    "blast",
    "door",
    "war",
    "operation",
    "launch",
}
WORLD_MISMATCH_LOCATION_KEYWORDS = {
    "cafe",
    "restaurant",
    "beach",
    "party",
    "nightclub",
    "club",
    "hotel",
    "apartment",
    "penthouse",
    "rooftop",
    "ballroom",
    "wedding",
    "classroom",
    "office",
    "mall",
    "amusement",
    "park",
}
HIGH_SEVERITY_RISKS = {"world_mismatch", "invalid_phrase_boundary", "invalid_pause_boundary", "invalid_energy_boundary"}
LOW_SEVERITY_RISKS = {"weak_audio_anchor", "low_scene_confidence", "abstract_audio_usage", "missing_audio_anchor_evidence"}
SCENE_PURPOSES = (
    "hook",
    "entry",
    "destabilization",
    "reveal",
    "escalation",
    "confrontation",
    "transition",
    "peak image",
    "emotional climax",
    "final image / ending hold",
)
TIMELINE_START_TOLERANCE_SEC = 0.35
TIMELINE_END_TOLERANCE_SEC = 0.75
TIMELINE_INTERNAL_GAP_WARN_SEC = 1.25
TIMELINE_TAIL_WARN_SEC = 1.0
TIMELINE_COVERAGE_RATIO_WARN = 0.95
CLIP_LEADING_INTRO_GAP_ABSORB_MAX_SEC = 0.8
MAX_INLINE_AUDIO_BYTES = 15 * 1024 * 1024
PRESENTATION_MALE_HINTS = (
    "male vocal",
    "male voice",
    "male singer",
    "masculine vocal",
    "masculine voice",
    "man singing",
    "his voice",
    "he sings",
    "he is singing",
    "мужской вокал",
    "мужской голос",
    "поет мужчина",
    "поёт мужчина",
)
PRESENTATION_FEMALE_HINTS = (
    "female vocal",
    "female voice",
    "female singer",
    "feminine vocal",
    "feminine voice",
    "woman singing",
    "her voice",
    "she sings",
    "she is singing",
    "женский вокал",
    "женский голос",
    "поет женщина",
    "поёт женщина",
)
PRESENTATION_MIXED_HINTS = (
    "male and female",
    "female and male",
    "mixed vocal",
    "mixed voices",
    "duet vocal",
    "duet",
    "group vocal",
    "мужской и женский",
    "смешанный вокал",
    "дуэт",
)
NON_PERFORMER_ROLE_TYPE_HINTS = {"animal", "animal_1", "props", "location", "style", "group", "group_faces", "object", "background"}
MUSIC_VIDEO_SCENE_PURPOSES = {"hook", "build", "performance", "transition", "payoff", "ending_hold"}
MUSIC_VIDEO_PERFORMANCE_FRAMINGS = {"face_close", "close_performance", "medium_performance", "duet_frame"}
VOCAL_PRIORITY_KEYS = (
    "audioSemantics",
    "audio_semantics",
    "audioUnderstanding",
    "audio_understanding",
    "structuredPlannerDiagnostics",
    "structured_planner_diagnostics",
    "sceneAudioEvidence",
    "scene_audio_evidence",
    "plannerDebug",
    "planner_debug",
    "directorSummary",
    "director_summary",
    "storySummary",
    "story_summary",
    "transcriptHints",
    "transcript_hints",
    "lyrics",
    "lyricText",
    "lyric_text",
    "localPhrase",
    "local_phrase",
)
VOCAL_MALE_HINTS = (
    "male vocal",
    "male voice",
    "man singing",
    "man vocals",
    "masculine vocal",
    "masculine voice",
    "baritone",
    "tenor",
    "deep male vocal",
    "male singer",
)
VOCAL_FEMALE_HINTS = (
    "female vocal",
    "female voice",
    "woman singing",
    "woman vocals",
    "feminine vocal",
    "feminine voice",
    "alto female",
    "soprano",
    "female singer",
)
VOCAL_MIXED_HINTS = (
    "male and female vocals",
    "duet vocal",
    "mixed vocals",
    "two voices",
    "alternating male female",
)
PERFORMER_FEMALE_HINTS = ("girl", "woman", "female", "lady", "девушка", "женщина", "девочка")
PERFORMER_MALE_HINTS = ("man", "male", "boy", "guy", "мужчина", "парень", "мальчик")


class ScenarioDirectorError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _is_direct_gemini_storyboard_mode(payload: dict[str, Any] | None = None) -> bool:
    payload_map = payload if isinstance(payload, dict) else {}
    payload_flag = payload_map.get("direct_gemini_storyboard_mode")
    if payload_flag is None:
        payload_flag = payload_map.get("directGeminiStoryboardMode")
    if isinstance(payload_flag, str):
        payload_flag = payload_flag.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(payload_flag, bool):
        return payload_flag
    env_raw = str(os.getenv("DIRECT_GEMINI_STORYBOARD_MODE", "")).strip().lower()
    return env_raw in {"1", "true", "yes", "on"}


def _parse_gemini_scene_route_strict(route_value: Any, *, scene_index: int, parse_stage: str) -> str:
    incoming_route = str(route_value or "").strip()
    route = incoming_route.lower()
    route_valid = route in GEMINI_SCENE_ROUTE_ENUM
    logger.info(
        "[SCENARIO_DIRECTOR ROUTE CONTRACT] route_contract_mode=%s allowed_routes=%s incoming_route=%s route_valid=%s mapped_workflow_key=%s route_source=%s backend_route_override_applied=%s parse_stage=%s scene_index=%s",
        "strict_enum",
        list(GEMINI_SCENE_ROUTE_ENUM),
        incoming_route,
        route_valid,
        GEMINI_ROUTE_TO_WORKFLOW_KEY.get(route),
        "gemini_schema_enum",
        False,
        parse_stage,
        scene_index,
    )
    if not route_valid:
        raise ScenarioDirectorError(
            "invalid_route",
            "Gemini returned an invalid scene route. Route must be one of: i2v, lip_sync_music, first_last.",
            status_code=502,
            details={
                "routeContractMode": "strict_enum",
                "allowedRoutes": list(GEMINI_SCENE_ROUTE_ENUM),
                "incomingRoute": incoming_route,
                "routeValid": False,
                "routeSource": "gemini_schema_enum",
                "backendRouteOverrideApplied": False,
                "parseStage": parse_stage,
                "sceneIndex": scene_index,
            },
        )
    return route


class ScenarioDirectorScene(BaseModel):
    scene_id: str
    time_start: float = 0.0
    time_end: float = 0.0
    duration: float = 0.0
    actors: list[str] = Field(default_factory=list)
    location: str = ""
    props: list[str] = Field(default_factory=list)
    emotion: str = ""
    scene_goal: str = ""
    frame_description: str = ""
    action_in_frame: str = ""
    camera: str = ""
    image_prompt: str = ""
    video_prompt: str = ""
    video_negative_prompt: str = ""
    start_frame_prompt: str = ""
    end_frame_prompt: str = ""
    ltx_mode: str = "i2v"
    ltx_reason: str = ""
    start_frame_source: str = "new"
    needs_two_frames: bool = False
    continuation_from_previous: bool = False
    narration_mode: str = "full"
    local_phrase: str | None = None
    scene_phrase_count: int = 0
    sfx: str = ""
    music_mix_hint: str = "off"
    render_mode: str = "image_video"
    resolved_workflow_key: str = "i2v"
    resolved_workflow_file: str = ""
    transition_type: str = "cut"
    shot_type: str = "medium"
    requested_duration_sec: float = 0.0
    scene_purpose: str = ""
    viewer_hook: str = ""
    performance_framing: str = ""
    clip_arc_stage: str = ""
    story_function: str = ""
    display_index: int = 0
    absorbed_story_functions: list[str] = Field(default_factory=list)
    beat_function: str = ""
    progression_reason: str = ""
    transition_family: str = ""
    start_visual_state: str = ""
    end_visual_state: str = ""
    delta_axes: list[str] = Field(default_factory=list)
    visual_intensity_level: str = ""
    crowd_relation_state: str = ""
    performance_phase: str = ""
    audio_emotion_direction: str = ""
    lip_sync: bool = False
    lip_sync_text: str = ""
    send_audio_to_generator: bool = False
    audio_slice_kind: str = ""
    music_vocal_lipsync_allowed: bool = False
    performer_presentation: str = "unknown"
    vocal_presentation: str = "unknown"
    lip_sync_voice_compatibility: str = "unknown"
    lip_sync_voice_compatibility_reason: str = ""
    audio_slice_start_sec: float = 0.0
    audio_slice_end_sec: float = 0.0
    audio_slice_expected_duration_sec: float = 0.0
    clip_decision_reason: str = ""
    role_influence_applied: bool = False
    role_influence_reason: str = ""
    scene_role_dynamics: str = ""
    multi_character_identity_lock: bool = False
    distinct_character_separation: bool = False
    duet_lock_enabled: bool = False
    duet_composition_mode: str = ""
    secondary_role_visibility_requirement: str = ""
    character2_drift_guard: str = ""
    duet_identity_contract: str = ""
    appearance_drift_risk: str = ""
    director_genre_intent: str = ""
    director_genre_reason: str = ""
    director_tone_bias: str = ""
    workflow_decision_reason: str = ""
    lip_sync_decision_reason: str = ""
    audio_slice_decision_reason: str = ""
    video_ready: bool = True
    video_block_reason_code: str = ""
    video_block_reason_message: str = ""
    video_downgrade_reason_code: str = ""
    video_downgrade_reason_message: str = ""
    video_generation_route: str = "i2v"
    planned_video_generation_route: str = ""
    identity_lock_applied: bool = False
    identity_lock_notes: str = ""
    identity_lock_fields_used: list[str] = Field(default_factory=list)
    hero_appearance_contract: dict[str, str] = Field(default_factory=dict)
    previous_stable_image_anchor_applied: bool = False
    previous_stable_image_anchor_available: bool = False
    previous_stable_image_anchor_url_resolved: str = ""
    previous_stable_image_anchor_used: bool = False
    previous_stable_image_anchor_reason: str = ""
    what_from_audio_this_scene_uses: str = ""
    director_note_layer: str = ""
    boundary_reason: str = "fallback"
    audio_anchor_evidence: str = ""
    confidence: float = 0.5
    first_last_candidate: bool = False
    first_last_candidate_score: float = 0.0
    first_last_candidate_reasons: list[str] = Field(default_factory=list)
    first_last_reject_reasons: list[str] = Field(default_factory=list)
    strong_visual_delta: bool = False
    phrase_loop_similarity_with_prev: float = 0.0
    phrase_loop_action: str = "keep"
    route_before_rebalance: str = ""
    route_after_rebalance: str = ""
    phrase_boundary_trim_applied: bool = False
    phrase_boundary_trim_reason: str = ""
    original_scene_end: float = 0.0
    trimmed_scene_end: float = 0.0
    lip_sync_route_state_consistent: bool = False
    audio_slice_bounds_filled_from_scene: bool = False

    @field_validator("scene_id", mode="before")
    @classmethod
    def _validate_scene_id(cls, value: Any) -> str:
        clean = str(value or "").strip()
        return clean or "S1"

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorScene":
        self.time_start = _safe_float(self.time_start, 0.0)
        self.time_end = _safe_float(self.time_end, self.time_start)
        self.duration = _safe_float(self.duration, max(0.0, self.time_end - self.time_start))
        if self.duration <= 0 and self.time_end > self.time_start:
            self.duration = round(self.time_end - self.time_start, 3)
        if self.time_end <= self.time_start and self.duration > 0:
            self.time_end = round(self.time_start + self.duration, 3)
        self.actors = [str(item).strip() for item in (self.actors or []) if str(item).strip()]
        self.props = [str(item).strip() for item in (self.props or []) if str(item).strip()]
        self.location = str(self.location or "").strip()
        self.emotion = str(self.emotion or "").strip()
        self.scene_goal = str(self.scene_goal or "").strip()
        self.frame_description = str(self.frame_description or "").strip()
        self.action_in_frame = str(self.action_in_frame or "").strip()
        self.camera = str(self.camera or "").strip()
        self.image_prompt = str(self.image_prompt or "").strip()
        self.video_prompt = str(self.video_prompt or "").strip()
        self.video_negative_prompt = str(self.video_negative_prompt or "").strip()
        self.start_frame_prompt = str(self.start_frame_prompt or "").strip()
        self.end_frame_prompt = str(self.end_frame_prompt or "").strip()
        self.narration_mode = str(self.narration_mode or "full").strip() or "full"
        self.start_frame_source = _normalize_start_frame_source(self.start_frame_source, continuation=self.continuation_from_previous)
        self.needs_two_frames = _coerce_bool(self.needs_two_frames, False)
        self.continuation_from_previous = _coerce_bool(self.continuation_from_previous, False)
        self.ltx_mode = _normalize_ltx_mode(
            self.ltx_mode,
            continuation=self.continuation_from_previous,
            needs_two_frames=self.needs_two_frames,
            narration_mode=self.narration_mode,
        )
        self.local_phrase = str(self.local_phrase).strip() if self.local_phrase is not None and str(self.local_phrase).strip() else None
        self.sfx = _stringify_sfx(self.sfx)
        self.music_mix_hint = str(self.music_mix_hint or "off").strip() or "off"
        self.render_mode = str(self.render_mode or "image_video").strip() or "image_video"
        self.resolved_workflow_key, self.resolved_workflow_file = _resolve_workflow_key_and_file(
            self.resolved_workflow_key or self.resolved_workflow_file,
            fallback_key="lip_sync_music" if self.lip_sync else ("f_l" if self.needs_two_frames else "i2v"),
        )
        self.transition_type = str(self.transition_type or "cut").strip() or "cut"
        self.shot_type = str(self.shot_type or "").strip()
        self.requested_duration_sec = _safe_float(
            self.requested_duration_sec,
            max(0.0, _safe_float(self.duration, max(0.0, self.time_end - self.time_start))),
        )
        self.scene_purpose = str(self.scene_purpose or "").strip()
        self.viewer_hook = str(self.viewer_hook or "").strip()
        self.performance_framing = str(self.performance_framing or "").strip()
        self.clip_arc_stage = str(self.clip_arc_stage or "").strip()
        self.story_function = str(self.story_function or "").strip()
        self.display_index = max(0, int(_safe_float(self.display_index, 0)))
        self.absorbed_story_functions = [str(item).strip() for item in (self.absorbed_story_functions or []) if str(item).strip()]
        self.beat_function = str(self.beat_function or "").strip()
        self.progression_reason = str(self.progression_reason or "").strip()
        self.transition_family = str(self.transition_family or "").strip()
        self.start_visual_state = str(self.start_visual_state or "").strip()
        self.end_visual_state = str(self.end_visual_state or "").strip()
        self.delta_axes = [str(item).strip() for item in (self.delta_axes or []) if str(item).strip()]
        self.visual_intensity_level = str(self.visual_intensity_level or "").strip()
        self.crowd_relation_state = str(self.crowd_relation_state or "").strip()
        self.performance_phase = str(self.performance_phase or "").strip()
        self.audio_emotion_direction = str(self.audio_emotion_direction or "").strip().lower()
        self.lip_sync = _coerce_bool(self.lip_sync, self.ltx_mode in {"lip_sync", "lip_sync_music"})
        self.lip_sync_text = str(self.lip_sync_text or "").strip()
        self.send_audio_to_generator = _coerce_bool(self.send_audio_to_generator, False)
        self.audio_slice_kind = str(self.audio_slice_kind or "").strip().lower()
        self.music_vocal_lipsync_allowed = _coerce_bool(self.music_vocal_lipsync_allowed, False)
        self.performer_presentation = str(self.performer_presentation or "unknown").strip().lower() or "unknown"
        self.vocal_presentation = str(self.vocal_presentation or "unknown").strip().lower() or "unknown"
        self.lip_sync_voice_compatibility = str(self.lip_sync_voice_compatibility or "unknown").strip().lower() or "unknown"
        self.lip_sync_voice_compatibility_reason = str(self.lip_sync_voice_compatibility_reason or "").strip()
        self.audio_slice_start_sec = _safe_float(self.audio_slice_start_sec, self.time_start)
        self.audio_slice_end_sec = _safe_float(self.audio_slice_end_sec, self.time_end)
        self.audio_slice_expected_duration_sec = _safe_float(
            self.audio_slice_expected_duration_sec,
            max(0.0, self.audio_slice_end_sec - self.audio_slice_start_sec),
        )
        self.clip_decision_reason = str(self.clip_decision_reason or "").strip()
        self.role_influence_applied = _coerce_bool(self.role_influence_applied, False)
        self.role_influence_reason = str(self.role_influence_reason or "").strip()
        self.scene_role_dynamics = str(self.scene_role_dynamics or "").strip()
        self.multi_character_identity_lock = _coerce_bool(self.multi_character_identity_lock, False)
        self.distinct_character_separation = _coerce_bool(self.distinct_character_separation, False)
        self.duet_lock_enabled = _coerce_bool(self.duet_lock_enabled, False)
        self.duet_composition_mode = str(self.duet_composition_mode or "").strip()
        self.secondary_role_visibility_requirement = str(self.secondary_role_visibility_requirement or "").strip()
        self.character2_drift_guard = str(self.character2_drift_guard or "").strip()
        self.duet_identity_contract = str(self.duet_identity_contract or "").strip()
        self.appearance_drift_risk = str(self.appearance_drift_risk or "").strip()
        self.director_genre_intent = str(self.director_genre_intent or "").strip()
        self.director_genre_reason = str(self.director_genre_reason or "").strip()
        self.director_tone_bias = str(self.director_tone_bias or "").strip()
        self.workflow_decision_reason = str(self.workflow_decision_reason or "").strip()
        self.lip_sync_decision_reason = str(self.lip_sync_decision_reason or "").strip()
        self.audio_slice_decision_reason = str(self.audio_slice_decision_reason or "").strip()
        self.video_ready = _coerce_bool(self.video_ready, True)
        self.video_block_reason_code = str(self.video_block_reason_code or "").strip()
        self.video_block_reason_message = str(self.video_block_reason_message or "").strip()
        self.video_downgrade_reason_code = str(self.video_downgrade_reason_code or "").strip()
        self.video_downgrade_reason_message = str(self.video_downgrade_reason_message or "").strip()
        route = str(self.video_generation_route or "").strip().lower()
        self.video_generation_route = route if route in {"i2v", "f_l", "lip_sync_music", "blocked", "downgraded_to_i2v"} else "i2v"
        self.planned_video_generation_route = str(self.planned_video_generation_route or "").strip().lower()
        self.identity_lock_applied = _coerce_bool(self.identity_lock_applied, False)
        self.identity_lock_notes = str(self.identity_lock_notes or "").strip()
        self.identity_lock_fields_used = [str(item).strip() for item in (self.identity_lock_fields_used or []) if str(item).strip()]
        self.hero_appearance_contract = {
            str(key).strip(): str(value).strip()
            for key, value in (self.hero_appearance_contract or {}).items()
            if str(key).strip() and str(value).strip()
        }
        self.previous_stable_image_anchor_applied = _coerce_bool(self.previous_stable_image_anchor_applied, False)
        self.previous_stable_image_anchor_available = _coerce_bool(self.previous_stable_image_anchor_available, False)
        self.previous_stable_image_anchor_url_resolved = str(self.previous_stable_image_anchor_url_resolved or "").strip()
        self.previous_stable_image_anchor_used = _coerce_bool(self.previous_stable_image_anchor_used, False)
        self.previous_stable_image_anchor_reason = str(self.previous_stable_image_anchor_reason or "").strip()
        self.what_from_audio_this_scene_uses = str(self.what_from_audio_this_scene_uses or "").strip()
        self.director_note_layer = str(self.director_note_layer or "").strip()
        boundary_reason = str(self.boundary_reason or "fallback").strip().lower() or "fallback"
        self.boundary_reason = boundary_reason if boundary_reason in {"phrase", "pause", "semantic", "energy", "fallback"} else "fallback"
        self.audio_anchor_evidence = str(self.audio_anchor_evidence or "").strip()
        self.confidence = _safe_float(self.confidence, 0.5)
        if self.confidence < 0:
            self.confidence = 0.0
        elif self.confidence > 1:
            self.confidence = 1.0
        self.first_last_candidate = _coerce_bool(self.first_last_candidate, False)
        self.first_last_candidate_score = _safe_float(self.first_last_candidate_score, 0.0)
        self.first_last_candidate_reasons = [str(item).strip() for item in (self.first_last_candidate_reasons or []) if str(item).strip()]
        self.first_last_reject_reasons = [str(item).strip() for item in (self.first_last_reject_reasons or []) if str(item).strip()]
        self.strong_visual_delta = _coerce_bool(self.strong_visual_delta, False)
        similarity = _safe_float(self.phrase_loop_similarity_with_prev, 0.0)
        self.phrase_loop_similarity_with_prev = 0.0 if similarity < 0.0 else (1.0 if similarity > 1.0 else similarity)
        phrase_loop_action = str(self.phrase_loop_action or "keep").strip().lower() or "keep"
        self.phrase_loop_action = phrase_loop_action if phrase_loop_action in {"keep", "merge", "reframe", "reject_duplicate"} else "keep"
        self.route_before_rebalance = str(self.route_before_rebalance or "").strip().lower()
        self.route_after_rebalance = str(self.route_after_rebalance or "").strip().lower()
        self.phrase_boundary_trim_applied = _coerce_bool(self.phrase_boundary_trim_applied, False)
        self.phrase_boundary_trim_reason = str(self.phrase_boundary_trim_reason or "").strip()
        self.original_scene_end = _safe_float(self.original_scene_end, self.time_end)
        self.trimmed_scene_end = _safe_float(self.trimmed_scene_end, self.time_end)
        self.lip_sync_route_state_consistent = _coerce_bool(self.lip_sync_route_state_consistent, False)
        self.audio_slice_bounds_filled_from_scene = _coerce_bool(self.audio_slice_bounds_filled_from_scene, False)
        self.ltx_reason = _normalize_ltx_reason(
            str(self.ltx_reason or "").strip(),
            self.ltx_mode,
            narration_mode=self.narration_mode,
        )
        return self


class ScenarioDirectorAudioUnderstanding(BaseModel):
    main_topic: str = ""
    world_context: str = ""
    implied_events: list[str] = Field(default_factory=list)
    emotional_tone_from_audio: str = ""
    confidence_audio_understood: float = 0.0
    what_from_audio_defines_world: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorAudioUnderstanding":
        self.main_topic = str(self.main_topic or "").strip()
        self.world_context = str(self.world_context or "").strip()
        self.implied_events = [str(item).strip() for item in (self.implied_events or []) if str(item).strip()]
        self.emotional_tone_from_audio = str(self.emotional_tone_from_audio or "").strip()
        self.confidence_audio_understood = _safe_float(self.confidence_audio_understood, 0.0)
        if self.confidence_audio_understood < 0:
            self.confidence_audio_understood = 0.0
        elif self.confidence_audio_understood > 1:
            self.confidence_audio_understood = 1.0
        self.what_from_audio_defines_world = str(self.what_from_audio_defines_world or "").strip()
        return self


class ScenarioDirectorConflictAnalysis(BaseModel):
    audio_vs_director_note_conflict: bool = False
    conflict_description: str = ""
    resolution_strategy: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorConflictAnalysis":
        self.audio_vs_director_note_conflict = _coerce_bool(self.audio_vs_director_note_conflict, False)
        self.conflict_description = str(self.conflict_description or "").strip()
        self.resolution_strategy = str(self.resolution_strategy or "").strip()
        return self


class ScenarioDirectorNarrativeStrategy(BaseModel):
    story_core_source: str = "mixed"
    story_frame_source: str = ""
    rhythm_source: str = ""
    story_frame_source_reason: str = ""
    rhythm_source_reason: str = ""
    did_audio_remain_primary: bool = False
    did_director_note_override_audio: bool = False
    why: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorNarrativeStrategy":
        story_core_source = str(self.story_core_source or "mixed").strip().lower() or "mixed"
        self.story_core_source = story_core_source if story_core_source in {"audio", "source_of_truth", "director_note", "mixed", "fallback"} else "mixed"
        story_frame_source = str(self.story_frame_source or "").strip().lower()
        self.story_frame_source = story_frame_source if story_frame_source in {"source_of_truth", "director_note"} else ""
        rhythm_source = str(self.rhythm_source or "").strip().lower()
        self.rhythm_source = rhythm_source if rhythm_source in {"audio"} else ""
        self.story_frame_source_reason = str(self.story_frame_source_reason or "").strip()
        self.rhythm_source_reason = str(self.rhythm_source_reason or "").strip()
        self.did_audio_remain_primary = _coerce_bool(self.did_audio_remain_primary, False)
        self.did_director_note_override_audio = _coerce_bool(self.did_director_note_override_audio, False)
        self.why = str(self.why or "").strip()
        return self


class ScenarioDirectorStoryMeta(BaseModel):
    title: str = ""
    summary: str = ""
    how_director_note_was_integrated: str = ""
    how_romance_exists_inside_audio_world: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorStoryMeta":
        self.title = str(self.title or "").strip()
        self.summary = str(self.summary or "").strip()
        self.how_director_note_was_integrated = str(self.how_director_note_was_integrated or "").strip()
        self.how_romance_exists_inside_audio_world = str(self.how_romance_exists_inside_audio_world or "").strip()
        return self


class ScenarioDirectorDiagnostics(BaseModel):
    used_audio_as_content_source: bool = False
    used_audio_only_as_mood: bool = False
    did_fallback_from_audio_content_truth: bool = False
    biggest_risk: str = ""
    what_may_be_wrong: str = ""
    planner_mode: str = "text_fallback"
    how_director_note_was_integrated: str = ""
    no_text_fallback_mode: str = "off"
    authorial_interpretation_level: str = "balanced"
    audio_literalness_level: str = "balanced"
    no_text_clip_policy: str = "off"
    no_text_clip_policy_applied: bool = False
    phrase_loop_prevented: bool = False
    phrase_loop_detected: bool = False
    phrase_loop_prevention_action: str = ""
    phrase_loop_prevention_reason: str = ""
    scene_merge_or_reuse_reason: str = ""
    clip_formula_target: dict[str, int] = Field(default_factory=dict)
    clip_formula_actual: dict[str, int] = Field(default_factory=dict)
    clip_formula_rebalance_applied: bool = False
    clip_formula_rebalance_detected_need: bool = False
    duration_span_debug: float = 0.0
    rebalance_reason: str = ""
    rebalance_actions: list[str] = Field(default_factory=list)
    clip_formula_rebalance_notes: list[str] = Field(default_factory=list)
    phrase_loop_prevented_reason: str = ""
    strong_first_last_candidate_count: int = 0
    first_last_shortage_reason: str = ""
    chorus_detected: bool = False
    active_connected_character_roles: list[str] = Field(default_factory=list)
    single_character_mode_enforced: bool = False
    removed_inactive_roles: list[str] = Field(default_factory=list)
    direct_gemini_storyboard_mode: bool = False
    intro_logic_applied: bool = False
    final_scene_split_applied: bool = False
    final_scene_split_reason: str = ""
    final_scene_split_source_scene_id: str = ""
    final_scene_split_created_ids: list[str] = Field(default_factory=list)
    final_scene_split_strategy: str = ""
    sentenceBoundaryCandidates: list[str] = Field(default_factory=list)
    clauseBoundaryCandidates: list[str] = Field(default_factory=list)
    finalSceneOversizeDetected: bool = False
    finalSceneSplitConsidered: bool = False
    segmentationRepairSource: str = ""
    oversizedScenesDetected: list[str] = Field(default_factory=list)
    oversizedScenesSplitCount: int = 0

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorDiagnostics":
        self.used_audio_as_content_source = _coerce_bool(self.used_audio_as_content_source, False)
        self.used_audio_only_as_mood = _coerce_bool(self.used_audio_only_as_mood, False)
        self.did_fallback_from_audio_content_truth = _coerce_bool(self.did_fallback_from_audio_content_truth, False)
        self.biggest_risk = str(self.biggest_risk or "").strip()
        self.what_may_be_wrong = str(self.what_may_be_wrong or "").strip()
        planner_mode = str(self.planner_mode or "text_fallback").strip().lower() or "text_fallback"
        self.planner_mode = planner_mode if planner_mode in {"full_audio_first", "partial_audio_first", "text_fallback"} else "text_fallback"
        self.how_director_note_was_integrated = str(self.how_director_note_was_integrated or "").strip()
        fallback_mode = str(self.no_text_fallback_mode or "off").strip().lower() or "off"
        self.no_text_fallback_mode = fallback_mode if fallback_mode in {"off", "neutral_audio_literal"} else "off"
        self.authorial_interpretation_level = str(self.authorial_interpretation_level or "balanced").strip().lower() or "balanced"
        self.audio_literalness_level = str(self.audio_literalness_level or "balanced").strip().lower() or "balanced"
        self.no_text_clip_policy = str(self.no_text_clip_policy or "off").strip().lower() or "off"
        self.no_text_clip_policy_applied = _coerce_bool(self.no_text_clip_policy_applied, False)
        self.phrase_loop_prevented = _coerce_bool(self.phrase_loop_prevented, False)
        self.phrase_loop_detected = _coerce_bool(self.phrase_loop_detected, False)
        self.phrase_loop_prevention_action = str(self.phrase_loop_prevention_action or "").strip()
        self.phrase_loop_prevention_reason = str(self.phrase_loop_prevention_reason or "").strip()
        self.scene_merge_or_reuse_reason = str(self.scene_merge_or_reuse_reason or "").strip()
        self.clip_formula_target = self.clip_formula_target if isinstance(self.clip_formula_target, dict) else {}
        self.clip_formula_actual = self.clip_formula_actual if isinstance(self.clip_formula_actual, dict) else {}
        self.clip_formula_rebalance_applied = _coerce_bool(self.clip_formula_rebalance_applied, False)
        self.clip_formula_rebalance_detected_need = _coerce_bool(self.clip_formula_rebalance_detected_need, False)
        self.duration_span_debug = _safe_float(self.duration_span_debug, 0.0)
        self.rebalance_reason = str(self.rebalance_reason or "").strip()
        self.rebalance_actions = [str(item).strip() for item in (self.rebalance_actions or []) if str(item).strip()]
        self.clip_formula_rebalance_notes = [str(item).strip() for item in (self.clip_formula_rebalance_notes or []) if str(item).strip()]
        self.phrase_loop_prevented_reason = str(self.phrase_loop_prevented_reason or "").strip()
        self.strong_first_last_candidate_count = max(0, int(_safe_float(self.strong_first_last_candidate_count, 0.0)))
        self.first_last_shortage_reason = str(self.first_last_shortage_reason or "").strip()
        self.chorus_detected = _coerce_bool(self.chorus_detected, False)
        self.active_connected_character_roles = [str(item).strip().lower() for item in (self.active_connected_character_roles or []) if str(item).strip()]
        self.single_character_mode_enforced = _coerce_bool(self.single_character_mode_enforced, False)
        self.removed_inactive_roles = [str(item).strip().lower() for item in (self.removed_inactive_roles or []) if str(item).strip()]
        self.direct_gemini_storyboard_mode = _coerce_bool(self.direct_gemini_storyboard_mode, False)
        self.intro_logic_applied = _coerce_bool(self.intro_logic_applied, False)
        self.final_scene_split_applied = _coerce_bool(self.final_scene_split_applied, False)
        self.final_scene_split_reason = str(self.final_scene_split_reason or "").strip()
        self.final_scene_split_source_scene_id = str(self.final_scene_split_source_scene_id or "").strip()
        self.final_scene_split_created_ids = [str(item).strip() for item in (self.final_scene_split_created_ids or []) if str(item).strip()]
        self.final_scene_split_strategy = str(self.final_scene_split_strategy or "").strip()
        self.sentenceBoundaryCandidates = [str(item).strip() for item in (self.sentenceBoundaryCandidates or []) if str(item).strip()]
        self.clauseBoundaryCandidates = [str(item).strip() for item in (self.clauseBoundaryCandidates or []) if str(item).strip()]
        self.finalSceneOversizeDetected = _coerce_bool(self.finalSceneOversizeDetected, False)
        self.finalSceneSplitConsidered = _coerce_bool(self.finalSceneSplitConsidered, False)
        self.segmentationRepairSource = str(self.segmentationRepairSource or "").strip().lower()
        self.oversizedScenesDetected = [str(item).strip() for item in (self.oversizedScenesDetected or []) if str(item).strip()]
        self.oversizedScenesSplitCount = max(0, int(_safe_float(self.oversizedScenesSplitCount, 0.0)))
        return self


class ScenarioDirectorStoryboardOut(BaseModel):
    story_summary: str = ""
    full_scenario: str = ""
    voice_script: str = ""
    music_prompt: str = ""
    director_summary: str = ""
    audio_understanding: ScenarioDirectorAudioUnderstanding = Field(default_factory=ScenarioDirectorAudioUnderstanding)
    conflict_analysis: ScenarioDirectorConflictAnalysis = Field(default_factory=ScenarioDirectorConflictAnalysis)
    narrative_strategy: ScenarioDirectorNarrativeStrategy = Field(default_factory=ScenarioDirectorNarrativeStrategy)
    story: ScenarioDirectorStoryMeta = Field(default_factory=ScenarioDirectorStoryMeta)
    diagnostics: ScenarioDirectorDiagnostics = Field(default_factory=ScenarioDirectorDiagnostics)
    scenes: list[ScenarioDirectorScene] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorStoryboardOut":
        self.story_summary = str(self.story_summary or "").strip()
        self.full_scenario = str(self.full_scenario or "").strip()
        self.voice_script = str(self.voice_script or "").strip()
        self.music_prompt = str(self.music_prompt or "").strip()
        self.director_summary = str(self.director_summary or "").strip()
        if not self.scenes:
            raise ValueError("director output must contain at least one scene")
        return self


def _safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return fallback
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return fallback
    return round(parsed, 3)


def _coerce_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    clean = str(value).strip().lower()
    if clean in {"true", "1", "yes", "y", "on"}:
        return True
    if clean in {"false", "0", "no", "n", "off", "null", "none", ""}:
        return False
    return fallback


def _stringify_sfx(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _normalize_start_frame_source(value: Any, *, continuation: bool = False) -> str:
    clean = str(value or "").strip()
    if not clean and continuation:
        return "previous_frame"
    normalized = LEGACY_START_FRAME_ALIASES.get(clean, clean)
    if normalized in {"new", "first_frame", "previous_frame", "generated"}:
        return normalized
    if continuation:
        return "previous_frame"
    if normalized == "":
        return "new"
    return normalized


def _normalize_ltx_mode(value: Any, *, continuation: bool, needs_two_frames: bool, narration_mode: str) -> str:
    clean = str(value or "").strip()
    if clean in ALLOWED_LTX_MODES:
        if clean in {"lip_sync", "lip_sync_music"} and not _is_music_vocal_mode(narration_mode):
            return "i2v_as"
        return clean
    if continuation:
        return "continuation"
    if needs_two_frames:
        return "f_l"
    return "i2v"


def _normalize_ltx_reason(reason: str, ltx_mode: str, *, narration_mode: str) -> str:
    if reason:
        if ltx_mode in {"lip_sync", "lip_sync_music"} and not _is_music_vocal_mode(narration_mode):
            return f"{reason}; normalized from lip_sync because narration is not music-vocal driven"
        return reason
    defaults = {
        "i2v": "Static or atmospheric scene with clean single-frame animation.",
        "i2v_as": "Audio-sensitive motion without speech articulation.",
        "f_l": "A-to-B transition that requires two frames.",
        "f_l_as": "Audio-accented A-to-B transition that requires two frames.",
        "continuation": "Direct continuation of the previous shot.",
        "lip_sync": "Music-vocal rhythm shot with visible articulation support.",
        "lip_sync_music": "Music-vocal rhythm shot with visible articulation support.",
    }
    return defaults.get(ltx_mode, "Production render mode selected by Scenario Director.")


def _is_music_vocal_mode(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return any(token in lowered for token in ("music", "vocal", "lyric", "sing", "chorus"))


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            texts = [str(part.get("text") or "") for part in parts if isinstance(part, dict) and str(part.get("text") or "").strip()]
            if texts:
                return "\n".join(texts).strip()
    return ""


def _extract_gemini_finish_reason(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp, dict) else None
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        finish = str(candidate.get("finishReason") or "").strip()
        if finish:
            return finish
    return ""


def _extract_json_blob(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _extract_balanced_json_candidate(text: str) -> str | None:
    in_string = False
    escape = False
    depth = 0
    start_index: int | None = None
    for index, char in enumerate(text):
        if start_index is None:
            if char == "{":
                start_index = index
                depth = 1
            continue
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_index:index + 1]
    return None


def _clean_dirty_json_blob(raw_text: str) -> str:
    candidate = _extract_json_blob(raw_text)
    candidate = re.sub(r"^```(?:json)?\s*", "", candidate.strip(), flags=re.IGNORECASE)
    candidate = re.sub(r"\s*```$", "", candidate, flags=re.IGNORECASE)
    candidate = _extract_balanced_json_candidate(candidate) or candidate
    candidate = candidate.strip().strip("`")
    return candidate


def _try_parse_dirty_json(text: str) -> dict[str, Any] | None:
    candidate = _clean_dirty_json_blob(text)
    if not candidate.startswith("{"):
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    recovered = _recover_partial_json_candidate(candidate)
    if recovered:
        try:
            parsed = json.loads(recovered)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

    pythonish = re.sub(r"\btrue\b", "True", candidate, flags=re.IGNORECASE)
    pythonish = re.sub(r"\bfalse\b", "False", pythonish, flags=re.IGNORECASE)
    pythonish = re.sub(r"\bnull\b", "None", pythonish, flags=re.IGNORECASE)
    try:
        parsed = ast.literal_eval(pythonish)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _recover_partial_json_candidate(candidate: str) -> str | None:
    text = str(candidate or "").strip()
    if not text.startswith("{"):
        return None
    in_string = False
    escape = False
    closers: list[str] = []
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            closers.append("}")
        elif char == "[":
            closers.append("]")
        elif char in {"}", "]"} and closers:
            expected = closers[-1]
            if char == expected:
                closers.pop()
    repaired = text.rstrip()
    repaired = re.sub(r",\s*$", "", repaired)
    if in_string:
        repaired += '"'
    if closers:
        repaired += "".join(reversed(closers))
    return repaired


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    return _try_parse_dirty_json(raw_text)


def _normalize_legacy_scene_shape(scene: dict) -> dict:
    normalized = dict(scene or {})
    visual = normalized.pop("visual", None)
    audio = normalized.pop("audio", None)
    ltx = normalized.pop("ltx", None)

    applied = False
    if isinstance(visual, dict):
        normalized.setdefault("frame_description", visual.get("frame_description"))
        normalized.setdefault("action_in_frame", visual.get("action_in_frame"))
        normalized.setdefault("camera", visual.get("camera"))
        applied = True
    if isinstance(audio, dict):
        normalized.setdefault("narration_mode", audio.get("narration"))
        normalized.setdefault("local_phrase", audio.get("local_phrase"))
        normalized.setdefault("sfx", audio.get("sfx"))
        applied = True
    if isinstance(ltx, dict):
        normalized.setdefault("ltx_mode", ltx.get("mode"))
        normalized.setdefault("ltx_reason", ltx.get("reason"))
        normalized.setdefault("start_frame_source", ltx.get("start_frame_source"))
        normalized.setdefault("needs_two_frames", ltx.get("needs_two_frames"))
        applied = True

    normalized["start_frame_source"] = _normalize_start_frame_source(
        normalized.get("start_frame_source"),
        continuation=_coerce_bool(normalized.get("continuation_from_previous"), False),
    )
    normalized["needs_two_frames"] = _coerce_bool(normalized.get("needs_two_frames"), False)
    normalized["continuation_from_previous"] = _coerce_bool(
        normalized.get("continuation_from_previous") or normalized.get("start_frame_source") == "previous_frame",
        False,
    )
    if normalized["start_frame_source"] == "previous_frame":
        normalized["continuation_from_previous"] = True
    normalized["sfx"] = _stringify_sfx(normalized.get("sfx"))
    normalized.setdefault("what_from_audio_this_scene_uses", normalized.get("whatFromAudioThisSceneUses"))
    normalized.setdefault("director_note_layer", normalized.get("directorNoteLayer"))
    normalized.setdefault("boundary_reason", normalized.get("boundaryReason"))
    normalized.setdefault("audio_anchor_evidence", normalized.get("audioAnchorEvidence"))
    normalized.setdefault("confidence", normalized.get("confidence"))
    normalized.setdefault("render_mode", normalized.get("renderMode"))
    normalized.setdefault("resolved_workflow_key", normalized.get("resolvedWorkflowKey"))
    normalized.setdefault("resolved_workflow_file", normalized.get("resolvedWorkflowFile"))
    normalized.setdefault("transition_type", normalized.get("transitionType"))
    normalized.setdefault("shot_type", normalized.get("shotType"))
    normalized.setdefault("requested_duration_sec", normalized.get("requestedDurationSec"))
    normalized.setdefault("scene_purpose", normalized.get("scenePurpose"))
    normalized.setdefault("viewer_hook", normalized.get("viewerHook"))
    normalized.setdefault("performance_framing", normalized.get("performanceFraming"))
    normalized.setdefault("clip_arc_stage", normalized.get("clipArcStage"))
    normalized.setdefault("story_function", normalized.get("storyFunction"))
    normalized.setdefault("display_index", normalized.get("displayIndex"))
    normalized.setdefault("absorbed_story_functions", normalized.get("absorbedStoryFunctions"))
    normalized.setdefault("beat_function", normalized.get("beatFunction"))
    normalized.setdefault("progression_reason", normalized.get("progressionReason"))
    normalized.setdefault("transition_family", normalized.get("transitionFamily"))
    normalized.setdefault("start_visual_state", normalized.get("startVisualState"))
    normalized.setdefault("end_visual_state", normalized.get("endVisualState"))
    normalized.setdefault("delta_axes", normalized.get("deltaAxes"))
    normalized.setdefault("visual_intensity_level", normalized.get("visualIntensityLevel"))
    normalized.setdefault("crowd_relation_state", normalized.get("crowdRelationState"))
    normalized.setdefault("performance_phase", normalized.get("performancePhase"))
    normalized.setdefault("lip_sync", normalized.get("lipSync"))
    normalized.setdefault("lip_sync_text", normalized.get("lipSyncText"))
    normalized.setdefault("send_audio_to_generator", normalized.get("sendAudioToGenerator"))
    normalized.setdefault("audio_slice_kind", normalized.get("audioSliceKind"))
    normalized.setdefault("music_vocal_lipsync_allowed", normalized.get("musicVocalLipSyncAllowed"))
    normalized.setdefault("audio_slice_start_sec", normalized.get("audioSliceStartSec"))
    normalized.setdefault("audio_slice_end_sec", normalized.get("audioSliceEndSec"))
    normalized.setdefault("audio_slice_expected_duration_sec", normalized.get("audioSliceExpectedDurationSec"))
    normalized.setdefault("clip_decision_reason", normalized.get("clipDecisionReason"))
    normalized.setdefault("role_influence_applied", normalized.get("roleInfluenceApplied"))
    normalized.setdefault("role_influence_reason", normalized.get("roleInfluenceReason"))
    normalized.setdefault("scene_role_dynamics", normalized.get("sceneRoleDynamics"))
    normalized.setdefault("multi_character_identity_lock", normalized.get("multiCharacterIdentityLock"))
    normalized.setdefault("distinct_character_separation", normalized.get("distinctCharacterSeparation"))
    normalized.setdefault("duet_lock_enabled", normalized.get("duetLockEnabled"))
    normalized.setdefault("duet_composition_mode", normalized.get("duetCompositionMode"))
    normalized.setdefault("secondary_role_visibility_requirement", normalized.get("secondaryRoleVisibilityRequirement"))
    normalized.setdefault("character2_drift_guard", normalized.get("character2DriftGuard"))
    normalized.setdefault("duet_identity_contract", normalized.get("duetIdentityContract"))
    normalized.setdefault("appearance_drift_risk", normalized.get("appearanceDriftRisk"))
    normalized.setdefault("director_genre_intent", normalized.get("directorGenreIntent"))
    normalized.setdefault("director_genre_reason", normalized.get("directorGenreReason"))
    normalized.setdefault("director_tone_bias", normalized.get("directorToneBias"))
    normalized.setdefault("workflow_decision_reason", normalized.get("workflowDecisionReason"))
    normalized.setdefault("lip_sync_decision_reason", normalized.get("lipSyncDecisionReason"))
    normalized.setdefault("audio_slice_decision_reason", normalized.get("audioSliceDecisionReason"))
    normalized.setdefault("video_ready", normalized.get("videoReady"))
    normalized.setdefault("video_block_reason_code", normalized.get("videoBlockReasonCode"))
    normalized.setdefault("video_block_reason_message", normalized.get("videoBlockReasonMessage"))
    normalized.setdefault("video_downgrade_reason_code", normalized.get("videoDowngradeReasonCode"))
    normalized.setdefault("video_downgrade_reason_message", normalized.get("videoDowngradeReasonMessage"))
    normalized.setdefault("video_generation_route", normalized.get("videoGenerationRoute"))
    normalized.setdefault("planned_video_generation_route", normalized.get("plannedVideoGenerationRoute"))
    normalized.setdefault("identity_lock_applied", normalized.get("identityLockApplied"))
    normalized.setdefault("identity_lock_notes", normalized.get("identityLockNotes"))
    normalized.setdefault("identity_lock_fields_used", normalized.get("identityLockFieldsUsed"))
    normalized.setdefault("video_negative_prompt", normalized.get("videoNegativePrompt"))

    if applied:
        logger.debug(
            "[SCENARIO_DIRECTOR] legacy scene normalized scene_id=%s ltx_mode=%s start_frame=%s",
            str(normalized.get("scene_id") or "").strip() or "unknown",
            str(normalized.get("ltx_mode") or "").strip() or "auto",
            normalized.get("start_frame_source") or "new",
        )
    return normalized


def _normalize_scenario_director_scene_defaults(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    if not isinstance(payload, dict):
        return payload, [], []
    repaired = dict(payload)
    scenes = repaired.get("scenes")
    if not isinstance(scenes, list):
        return repaired, [], []
    normalized_fields: list[str] = []
    warnings: list[str] = []
    normalized_scenes: list[Any] = []
    for idx, raw_scene in enumerate(scenes):
        if not isinstance(raw_scene, dict):
            normalized_scenes.append(raw_scene)
            continue
        scene = dict(raw_scene)
        narration_mode = str(scene.get("narration_mode") or "").strip().lower()
        if narration_mode not in ALLOWED_NARRATION_MODES:
            scene["narration_mode"] = "full"
            normalized_fields.append(f"scenes[{idx}].narration_mode")
            warnings.append("scenario_director_normalized_narration_mode_default")
        video_negative_prompt = str(scene.get("video_negative_prompt") or scene.get("videoNegativePrompt") or "").strip()
        if not video_negative_prompt:
            video_negative_prompt = build_ltx_video_negative_prompt(scene)
            scene["video_negative_prompt"] = video_negative_prompt
            scene["videoNegativePrompt"] = video_negative_prompt
            normalized_fields.append(f"scenes[{idx}].video_negative_prompt")
        normalized_scenes.append(scene)
    repaired["scenes"] = normalized_scenes
    return repaired, normalized_fields, list(dict.fromkeys(warnings))


def _repair_scenario_director_payload(payload: dict, *, parse_stage: str = "initial") -> dict:
    def _is_compact_director_payload(candidate_payload: dict[str, Any]) -> bool:
        if not isinstance(candidate_payload, dict):
            return False
        input_understanding = candidate_payload.get("input_understanding")
        storyboard = candidate_payload.get("storyboard")
        if not isinstance(input_understanding, dict) or not isinstance(storyboard, dict):
            return False
        scenes = storyboard.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            return False
        first_scene = scenes[0] if isinstance(scenes[0], dict) else {}
        return "start_time_sec" in first_scene and "end_time_sec" in first_scene

    def _map_compact_director_to_legacy(candidate_payload: dict[str, Any]) -> dict[str, Any]:
        storyboard = candidate_payload.get("storyboard") if isinstance(candidate_payload.get("storyboard"), dict) else {}
        input_understanding = (
            candidate_payload.get("input_understanding")
            if isinstance(candidate_payload.get("input_understanding"), dict)
            else {}
        )
        compact_scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
        same_character = _coerce_bool(input_understanding.get("same_character_across_all_scenes"), False)
        continuity_lock_fields = [
            "face_identity",
            "hair_identity",
            "body_identity",
            "age_consistency",
            "garment_category",
            "coverage_identity",
            "construction_identity",
            "silhouette_identity",
            "material_identity",
            "signature_details_identity",
            "color_identity",
            "footwear_identity",
            "accessory_identity",
            "world_identity",
        ]
        mapped_scenes: list[dict[str, Any]] = []
        for idx, compact_scene in enumerate(compact_scenes):
            if not isinstance(compact_scene, dict):
                continue
            scene_start = _safe_float(compact_scene.get("start_time_sec"), 0.0)
            scene_end = _safe_float(compact_scene.get("end_time_sec"), scene_start)
            if scene_end < scene_start:
                scene_end = scene_start
            route = _parse_gemini_scene_route_strict(compact_scene.get("route"), scene_index=idx, parse_stage=parse_stage)
            mapped_workflow_key = GEMINI_ROUTE_TO_WORKFLOW_KEY[route]
            description = str(compact_scene.get("description") or "").strip()
            content_tags = [str(tag).strip() for tag in (compact_scene.get("content_tags") or []) if str(tag).strip()]
            shot_type = content_tags[0] if content_tags else "medium"
            performance_framing = str(compact_scene.get("performance_framing") or compact_scene.get("performanceFraming") or "").strip()
            if not performance_framing:
                performance_framing = ", ".join(content_tags[:3]) if content_tags else ""
            is_lip_sync = route == "lip_sync_music"
            needs_two_frames = route == "first_last"
            internal_route = "f_l" if route == "first_last" else route
            render_mode = "lip_sync_music" if is_lip_sync else ("first_last" if needs_two_frames else "image_video")
            ltx_mode = "lip_sync_music" if is_lip_sync else ("f_l" if needs_two_frames else "i2v")
            description, performance_framing = _normalize_scene_canon_by_route(
                route=route,
                description=description,
                performance_framing=performance_framing,
                content_tags=content_tags,
            )
            route_lc_description = str(description or "").strip().lower()
            description_is_risky = any(
                marker in route_lc_description for marker in (*LIP_SYNC_SPIN_RISK_MARKERS, *NON_LIP_RISKY_ROTATION_MARKERS)
            )
            if is_lip_sync:
                action_in_frame = (
                    description
                    or "Singer-performance-first shot with emotional lyric delivery and beat-driven energy escalation."
                )
                if description_is_risky:
                    action_in_frame = (
                        "Singer-performance-first shot: emotional lyric delivery with phrase-driven expressive hands, chest-open outward phrasing, and gentle upper-body pulse; "
                        "avoid spin-first/full-body spectacle as the main idea."
                    )
                camera_text = (
                    "gentle push/pull or side arc, maintain close readability of face/mouth/neck/shoulders/upper torso"
                )
            else:
                action_in_frame = (
                    description
                    or "Beat-led scene progression through space with safe step/pivot/gesture and camera-led reveal."
                )
                if description_is_risky:
                    action_in_frame = (
                        "Beat-led spatial progression through venue zones with safe step/pivot/gesture and evolving body angles; "
                        "avoid sharp spins/twirls/whip-turns as primary choreography."
                    )
                camera_text = "tracking/reveal/parallax progression that follows scene action-space development"
            scene_duration = max(0.0, round(scene_end - scene_start, 3))
            mapped_scenes.append(
                {
                    "scene_id": str(compact_scene.get("scene_id") or f"S{idx + 1}"),
                    "time_start": scene_start,
                    "time_end": scene_end,
                    "duration": scene_duration,
                    "requested_duration_sec": scene_duration,
                    "local_phrase": description,
                    "scene_goal": description,
                    "frame_description": description or "Performance-led visual beat aligned with audio timing.",
                    "action_in_frame": action_in_frame,
                    "camera": camera_text,
                    "what_from_audio_this_scene_uses": description,
                    "render_mode": render_mode,
                    "resolved_workflow_key": mapped_workflow_key,
                    "video_generation_route": internal_route,
                    "planned_video_generation_route": route,
                    "ltx_mode": ltx_mode,
                    "needs_two_frames": needs_two_frames,
                    "lip_sync": is_lip_sync,
                    "send_audio_to_generator": is_lip_sync,
                    "music_vocal_lipsync_allowed": is_lip_sync,
                    "audio_slice_start_sec": scene_start if is_lip_sync else 0.0,
                    "audio_slice_end_sec": scene_end if is_lip_sync else 0.0,
                    "audio_slice_expected_duration_sec": scene_duration if is_lip_sync else 0.0,
                    "shot_type": shot_type,
                    "image_prompt": _normalize_image_prompt_by_route(
                        route=route,
                        image_prompt=description,
                        fallback_text=action_in_frame,
                    ),
                    "video_prompt": action_in_frame,
                    "performance_framing": performance_framing,
                    "identity_lock_applied": same_character,
                    "identity_lock_fields_used": continuity_lock_fields if same_character else [],
                }
            )

        diagnostics = storyboard.get("diagnostics") if isinstance(storyboard.get("diagnostics"), dict) else {}
        diagnostics = dict(diagnostics)
        diagnostics.setdefault("gemini_input_understanding", input_understanding)
        return {
            "story_summary": str(storyboard.get("story_summary") or "").strip(),
            "full_scenario": str(storyboard.get("full_scenario") or "").strip(),
            "voice_script": str(storyboard.get("voice_script") or "").strip(),
            "director_summary": str(storyboard.get("director_summary") or "").strip(),
            "audio_understanding": storyboard.get("audio_understanding") if isinstance(storyboard.get("audio_understanding"), dict) else {},
            "narrative_strategy": storyboard.get("narrative_strategy") if isinstance(storyboard.get("narrative_strategy"), dict) else {},
            "diagnostics": diagnostics,
            "scenes": mapped_scenes,
        }

    candidate = payload
    changed = False
    if isinstance(candidate.get("storyboard_out"), dict):
        candidate = candidate["storyboard_out"]
        changed = True
    elif isinstance(candidate.get("storyboardOut"), dict):
        candidate = candidate["storyboardOut"]
        changed = True
    elif isinstance(candidate.get("output"), dict) and isinstance(candidate["output"].get("scenes"), list):
        candidate = candidate["output"]
        changed = True

    if _is_compact_director_payload(candidate):
        candidate = _map_compact_director_to_legacy(candidate)
        changed = True

    repaired = dict(candidate)
    scenes = repaired.get("scenes")
    if isinstance(scenes, list):
        normalized_scenes = [_normalize_legacy_scene_shape(scene) if isinstance(scene, dict) else scene for scene in scenes]
        if normalized_scenes != scenes:
            changed = True
        repaired["scenes"] = normalized_scenes
        repaired, normalized_fields, _ = _normalize_scenario_director_scene_defaults(repaired)
        if normalized_fields:
            changed = True

    if not repaired.get("story_summary"):
        repaired["story_summary"] = repaired.get("summary") or repaired.get("storySummary") or ""
        changed = changed or bool(repaired["story_summary"])
    if not repaired.get("full_scenario"):
        repaired["full_scenario"] = repaired.get("scenario") or repaired.get("fullScenario") or repaired.get("story") or ""
        changed = changed or bool(repaired["full_scenario"])
    if not repaired.get("voice_script"):
        repaired["voice_script"] = repaired.get("voiceScript") or repaired.get("narration_script") or ""
        changed = changed or bool(repaired["voice_script"])
    if not repaired.get("music_prompt"):
        repaired["music_prompt"] = repaired.get("musicPrompt") or repaired.get("music_direction") or ""
        changed = changed or bool(repaired["music_prompt"])
    if not repaired.get("director_summary"):
        repaired["director_summary"] = repaired.get("directorSummary") or repaired.get("direction_summary") or ""
        changed = changed or bool(repaired["director_summary"])
    if not isinstance(repaired.get("audio_understanding"), dict):
        candidate_audio_understanding = repaired.get("audioUnderstanding")
        if isinstance(candidate_audio_understanding, dict):
            repaired["audio_understanding"] = candidate_audio_understanding
            changed = True
    if isinstance(repaired.get("audio_understanding"), dict):
        audio_understanding = dict(repaired.get("audio_understanding") or {})
        audio_understanding.setdefault("main_topic", audio_understanding.get("mainTopic"))
        audio_understanding.setdefault("world_context", audio_understanding.get("worldContext"))
        audio_understanding.setdefault("implied_events", audio_understanding.get("impliedEvents"))
        audio_understanding.setdefault("emotional_tone_from_audio", audio_understanding.get("emotionalToneFromAudio"))
        audio_understanding.setdefault("confidence_audio_understood", audio_understanding.get("confidenceAudioUnderstood"))
        audio_understanding.setdefault("what_from_audio_defines_world", audio_understanding.get("whatFromAudioDefinesWorld"))
        repaired["audio_understanding"] = audio_understanding
    if not isinstance(repaired.get("conflict_analysis"), dict):
        candidate_conflict = repaired.get("conflictAnalysis")
        if isinstance(candidate_conflict, dict):
            repaired["conflict_analysis"] = candidate_conflict
            changed = True
    if isinstance(repaired.get("conflict_analysis"), dict):
        conflict_analysis = dict(repaired.get("conflict_analysis") or {})
        conflict_analysis.setdefault("audio_vs_director_note_conflict", conflict_analysis.get("audioVsDirectorNoteConflict"))
        conflict_analysis.setdefault("conflict_description", conflict_analysis.get("conflictDescription"))
        conflict_analysis.setdefault("resolution_strategy", conflict_analysis.get("resolutionStrategy"))
        repaired["conflict_analysis"] = conflict_analysis
    if not isinstance(repaired.get("narrative_strategy"), dict):
        candidate_strategy = repaired.get("narrativeStrategy")
        if isinstance(candidate_strategy, dict):
            repaired["narrative_strategy"] = candidate_strategy
            changed = True
    if isinstance(repaired.get("narrative_strategy"), dict):
        narrative_strategy = dict(repaired.get("narrative_strategy") or {})
        narrative_strategy.setdefault("story_core_source", narrative_strategy.get("storyCoreSource"))
        narrative_strategy.setdefault("story_frame_source", narrative_strategy.get("storyFrameSource"))
        narrative_strategy.setdefault("rhythm_source", narrative_strategy.get("rhythmSource"))
        narrative_strategy.setdefault("story_frame_source_reason", narrative_strategy.get("storyFrameSourceReason"))
        narrative_strategy.setdefault("rhythm_source_reason", narrative_strategy.get("rhythmSourceReason"))
        narrative_strategy.setdefault("did_audio_remain_primary", narrative_strategy.get("didAudioRemainPrimary"))
        narrative_strategy.setdefault("did_director_note_override_audio", narrative_strategy.get("didDirectorNoteOverrideAudio"))
        repaired["narrative_strategy"] = narrative_strategy
    if not isinstance(repaired.get("story"), dict):
        candidate_story = repaired.get("story")
        if isinstance(candidate_story, dict):
            repaired["story"] = candidate_story
            changed = True
    if not isinstance(repaired.get("diagnostics"), dict):
        candidate_diagnostics = repaired.get("diagnostics")
        if isinstance(candidate_diagnostics, dict):
            repaired["diagnostics"] = candidate_diagnostics
            changed = True
    if isinstance(repaired.get("story"), dict):
        story = dict(repaired.get("story") or {})
        story.setdefault("how_director_note_was_integrated", story.get("howDirectorNoteWasIntegrated"))
        story.setdefault("how_romance_exists_inside_audio_world", story.get("howRomanceExistsInsideAudioWorld"))
        repaired["story"] = story
    if isinstance(repaired.get("diagnostics"), dict):
        diagnostics = dict(repaired.get("diagnostics") or {})
        diagnostics.setdefault("used_audio_as_content_source", diagnostics.get("usedAudioAsContentSource"))
        diagnostics.setdefault("used_audio_only_as_mood", diagnostics.get("usedAudioOnlyAsMood"))
        diagnostics.setdefault("did_fallback_from_audio_content_truth", diagnostics.get("didFallbackFromAudioContentTruth"))
        diagnostics.setdefault("biggest_risk", diagnostics.get("biggestRisk"))
        diagnostics.setdefault("what_may_be_wrong", diagnostics.get("whatMayBeWrong"))
        diagnostics.setdefault("planner_mode", diagnostics.get("plannerMode"))
        diagnostics.setdefault("how_director_note_was_integrated", diagnostics.get("howDirectorNoteWasIntegrated"))
        diagnostics.setdefault("no_text_fallback_mode", diagnostics.get("noTextFallbackMode"))
        diagnostics.setdefault("authorial_interpretation_level", diagnostics.get("authorialInterpretationLevel"))
        diagnostics.setdefault("audio_literalness_level", diagnostics.get("audioLiteralnessLevel"))
        repaired["diagnostics"] = diagnostics

    if changed:
        logger.debug(
            "[SCENARIO_DIRECTOR] repair applied scenes=%s story_summary=%s",
            len(repaired.get("scenes") or []),
            bool(str(repaired.get("story_summary") or "").strip()),
        )
    return repaired


def _extract_structured_diagnostics(parsed_payload: dict[str, Any]) -> dict[str, Any]:
    audio_understanding_raw = parsed_payload.get("audio_understanding") if isinstance(parsed_payload.get("audio_understanding"), dict) else {}
    conflict_analysis_raw = parsed_payload.get("conflict_analysis") if isinstance(parsed_payload.get("conflict_analysis"), dict) else {}
    narrative_strategy_raw = parsed_payload.get("narrative_strategy") if isinstance(parsed_payload.get("narrative_strategy"), dict) else {}
    diagnostics_raw = parsed_payload.get("diagnostics") if isinstance(parsed_payload.get("diagnostics"), dict) else {}
    story_raw = parsed_payload.get("story") if isinstance(parsed_payload.get("story"), dict) else {}
    return {
        "audioUnderstanding": {
            "mainTopic": str(audio_understanding_raw.get("main_topic") or audio_understanding_raw.get("mainTopic") or "").strip(),
            "worldContext": str(audio_understanding_raw.get("world_context") or audio_understanding_raw.get("worldContext") or "").strip(),
            "impliedEvents": [
                str(item).strip()
                for item in (audio_understanding_raw.get("implied_events") or audio_understanding_raw.get("impliedEvents") or [])
                if str(item).strip()
            ],
            "emotionalToneFromAudio": str(audio_understanding_raw.get("emotional_tone_from_audio") or audio_understanding_raw.get("emotionalToneFromAudio") or "").strip(),
            "confidenceAudioUnderstood": _safe_float(
                audio_understanding_raw.get("confidence_audio_understood") if audio_understanding_raw.get("confidence_audio_understood") is not None else audio_understanding_raw.get("confidenceAudioUnderstood"),
                0.0,
            ),
            "whatFromAudioDefinesWorld": str(audio_understanding_raw.get("what_from_audio_defines_world") or audio_understanding_raw.get("whatFromAudioDefinesWorld") or "").strip(),
        },
        "conflictAnalysis": {
            "audioVsDirectorNoteConflict": _coerce_bool(
                conflict_analysis_raw.get("audio_vs_director_note_conflict")
                if conflict_analysis_raw.get("audio_vs_director_note_conflict") is not None
                else conflict_analysis_raw.get("audioVsDirectorNoteConflict"),
                False,
            ),
            "conflictDescription": str(conflict_analysis_raw.get("conflict_description") or conflict_analysis_raw.get("conflictDescription") or "").strip(),
            "resolutionStrategy": str(conflict_analysis_raw.get("resolution_strategy") or conflict_analysis_raw.get("resolutionStrategy") or "").strip(),
        },
        "narrativeStrategy": {
            "storyCoreSource": str(narrative_strategy_raw.get("story_core_source") or narrative_strategy_raw.get("storyCoreSource") or "").strip().lower() or "mixed",
            "storyFrameSource": str(narrative_strategy_raw.get("story_frame_source") or narrative_strategy_raw.get("storyFrameSource") or "").strip().lower(),
            "rhythmSource": str(narrative_strategy_raw.get("rhythm_source") or narrative_strategy_raw.get("rhythmSource") or "").strip().lower(),
            "storyFrameSourceReason": str(
                narrative_strategy_raw.get("story_frame_source_reason")
                or narrative_strategy_raw.get("storyFrameSourceReason")
                or ""
            ).strip(),
            "rhythmSourceReason": str(
                narrative_strategy_raw.get("rhythm_source_reason")
                or narrative_strategy_raw.get("rhythmSourceReason")
                or ""
            ).strip(),
            "didAudioRemainPrimary": _coerce_bool(
                narrative_strategy_raw.get("did_audio_remain_primary")
                if narrative_strategy_raw.get("did_audio_remain_primary") is not None
                else narrative_strategy_raw.get("didAudioRemainPrimary"),
                False,
            ),
            "didDirectorNoteOverrideAudio": _coerce_bool(
                narrative_strategy_raw.get("did_director_note_override_audio")
                if narrative_strategy_raw.get("did_director_note_override_audio") is not None
                else narrative_strategy_raw.get("didDirectorNoteOverrideAudio"),
                False,
            ),
            "why": str(narrative_strategy_raw.get("why") or "").strip(),
            "story_core_source": str(narrative_strategy_raw.get("story_core_source") or narrative_strategy_raw.get("storyCoreSource") or "").strip().lower() or "mixed",
            "story_frame_source": str(narrative_strategy_raw.get("story_frame_source") or narrative_strategy_raw.get("storyFrameSource") or "").strip().lower(),
            "rhythm_source": str(narrative_strategy_raw.get("rhythm_source") or narrative_strategy_raw.get("rhythmSource") or "").strip().lower(),
            "story_frame_source_reason": str(
                narrative_strategy_raw.get("story_frame_source_reason")
                or narrative_strategy_raw.get("storyFrameSourceReason")
                or ""
            ).strip(),
            "rhythm_source_reason": str(
                narrative_strategy_raw.get("rhythm_source_reason")
                or narrative_strategy_raw.get("rhythmSourceReason")
                or ""
            ).strip(),
        },
        "story": {
            "title": str(story_raw.get("title") or "").strip(),
            "summary": str(story_raw.get("summary") or "").strip(),
            "howDirectorNoteWasIntegrated": str(story_raw.get("how_director_note_was_integrated") or story_raw.get("howDirectorNoteWasIntegrated") or "").strip(),
            "howRomanceExistsInsideAudioWorld": str(story_raw.get("how_romance_exists_inside_audio_world") or story_raw.get("howRomanceExistsInsideAudioWorld") or "").strip(),
        },
        "diagnostics": {
            "usedAudioAsContentSource": _coerce_bool(
                diagnostics_raw.get("used_audio_as_content_source")
                if diagnostics_raw.get("used_audio_as_content_source") is not None
                else diagnostics_raw.get("usedAudioAsContentSource"),
                False,
            ),
            "usedAudioOnlyAsMood": _coerce_bool(
                diagnostics_raw.get("used_audio_only_as_mood")
                if diagnostics_raw.get("used_audio_only_as_mood") is not None
                else diagnostics_raw.get("usedAudioOnlyAsMood"),
                False,
            ),
            "didFallbackFromAudioContentTruth": _coerce_bool(
                diagnostics_raw.get("did_fallback_from_audio_content_truth")
                if diagnostics_raw.get("did_fallback_from_audio_content_truth") is not None
                else diagnostics_raw.get("didFallbackFromAudioContentTruth"),
                False,
            ),
            "biggestRisk": str(diagnostics_raw.get("biggest_risk") or diagnostics_raw.get("biggestRisk") or "").strip(),
            "whatMayBeWrong": str(diagnostics_raw.get("what_may_be_wrong") or diagnostics_raw.get("whatMayBeWrong") or "").strip(),
            "plannerMode": str(diagnostics_raw.get("planner_mode") or diagnostics_raw.get("plannerMode") or "").strip().lower() or "text_fallback",
            "howDirectorNoteWasIntegrated": str(diagnostics_raw.get("how_director_note_was_integrated") or diagnostics_raw.get("howDirectorNoteWasIntegrated") or "").strip(),
            "noTextFallbackMode": str(diagnostics_raw.get("no_text_fallback_mode") or diagnostics_raw.get("noTextFallbackMode") or "").strip().lower() or "off",
            "authorialInterpretationLevel": str(diagnostics_raw.get("authorial_interpretation_level") or diagnostics_raw.get("authorialInterpretationLevel") or "").strip().lower() or "balanced",
            "audioLiteralnessLevel": str(diagnostics_raw.get("audio_literalness_level") or diagnostics_raw.get("audioLiteralnessLevel") or "").strip().lower() or "balanced",
        },
    }


def _build_reference_role_map(payload: dict[str, Any]) -> dict[str, str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    role_map: dict[str, str] = {}
    for role, item in refs.items():
        if not isinstance(item, dict):
            continue
        normalized_role = _normalize_scenario_role(role)
        if not normalized_role:
            continue
        label = str(item.get("preview") or item.get("label") or item.get("source_label") or role).strip()
        role_map[normalized_role] = label or normalized_role
    return role_map


def _build_display_label_by_role(payload: dict[str, Any], known_roles: list[str]) -> dict[str, str]:
    role_labels = _build_reference_role_map(payload)
    return {
        role: role_labels.get(role, role)
        for role in known_roles
    }


def _scene_participants(scene: ScenarioDirectorScene, role_lookup: dict[str, str]) -> list[str]:
    participants: list[str] = []
    for actor in scene.actors:
        clean = _normalize_scenario_role(actor, role_lookup=role_lookup)
        if not clean:
            continue
        participants.append(clean)
    return participants


def _build_character_roles(payload: dict[str, Any], role_labels: dict[str, str], known_roles: list[str]) -> list[dict[str, str]]:
    ordered_roles = [role for role in known_roles if role in SCENARIO_CAST_ROLES]
    effective_role_types, _, _ = _resolve_effective_role_type_by_role(payload)
    role_copy_by_type = {
        "hero": "Главный герой / главный носитель действия",
        "support": "Партнёр по сцене / поддерживающий акцент",
        "antagonist": "Антагонист / контр-сила конфликта",
    }
    default_role_copy = {
        "character_1": "Главный герой / главный носитель действия",
        "character_2": "Партнёр по сцене / вторичный акцент",
        "character_3": "Поддерживающий персонаж или смысловой объект",
        "animal": "Животное / поддерживающий участник кадра",
        "animal_1": "Животное / поддерживающий участник кадра",
        "group": "Группа / массовка",
        "group_faces": "Группа / массовка",
    }
    out: list[dict[str, str]] = []
    for role in ordered_roles:
        label = role_labels.get(role)
        if not label:
            continue
        explicit_type = str(effective_role_types.get(role) or "").strip().lower()
        role_copy = role_copy_by_type.get(explicit_type) or default_role_copy.get(role, "Поддерживающая роль")
        out.append({"name": label, "role": role_copy})
    return out


def _resolve_audio_duration_info(payload: dict[str, Any]) -> tuple[float, str]:
    normalized = _normalize_audio_context(payload)
    return _safe_float(normalized.get("audioDurationSec"), 0.0), str(normalized.get("audioDurationSource") or "missing")


def _resolve_effective_director_note_text(payload: dict[str, Any]) -> str:
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    for candidate in (
        controls.get("directorNote"),
        controls.get("director_note"),
        payload.get("text"),
    ):
        if isinstance(candidate, str):
            clean = candidate.strip()
            if clean:
                return clean
    return ""


def _resolve_audio_asset_path(audio_url: str | None) -> str | None:
    clean = str(audio_url or "").strip()
    if not clean:
        return None
    parsed = urlparse(clean)
    filename = os.path.basename(parsed.path)
    if not filename:
        return None
    base = os.path.splitext(filename)[0]
    candidates = [filename, base, f"{base}.mp3", f"{base}.wav", f"{base}.ogg", f"{base}.m4a"]
    for name in candidates:
        path = os.path.join(str(ASSETS_DIR), name)
        if os.path.isfile(path):
            return path
    return None


def _resolve_audio_source_for_analysis(audio_url: str | None) -> dict[str, Any]:
    clean = str(audio_url or "").strip()
    if not clean:
        return {"ok": False, "mode": "missing", "path": None, "url": None, "normalized": "", "hint": "audio_url_missing", "reason": "audio_url_missing"}

    parsed = urlparse(clean)
    if parsed.scheme in {"http", "https"}:
        local_static_path = _resolve_local_static_asset_path(parsed.path or "")
        if local_static_path:
            return {
                "ok": True,
                "mode": "local_file",
                "path": local_static_path,
                "url": None,
                "normalized": local_static_path,
                "hint": "audio_local_static_asset_from_http_url",
                "reason": "http_url_points_to_static_assets_local_file_exists",
            }
        return {
            "ok": True,
            "mode": "http",
            "path": None,
            "url": clean,
            "normalized": clean,
            "hint": "audio_url_absolute",
            "reason": "absolute_http_url_fallback",
        }

    if parsed.scheme:
        return {"ok": False, "mode": "invalid", "path": None, "url": None, "normalized": clean, "hint": "audio_url_not_absolute", "reason": "unsupported_url_scheme"}

    normalized = clean.lstrip("/")
    path_variants = [normalized]
    if normalized.startswith("static/"):
        path_variants.append(normalized[len("static/"):])
    if normalized.startswith("assets/"):
        path_variants.append(f"static/{normalized}")

    for variant in path_variants:
        variant_clean = variant.strip("/")
        if not variant_clean:
            continue
        candidate = (BACKEND_DIR / variant_clean).resolve()
        if candidate.is_file():
            return {
                "ok": True,
                "mode": "local_file",
                "path": str(candidate),
                "url": None,
                "normalized": str(candidate),
                "hint": "audio_local_file_resolved",
                "reason": "relative_path_resolved_to_backend_file",
            }

    asset_path = _resolve_audio_asset_path(clean)
    if asset_path:
        return {
            "ok": True,
            "mode": "local_file",
            "path": asset_path,
            "url": None,
            "normalized": asset_path,
            "hint": "audio_asset_resolved",
            "reason": "asset_filename_resolved",
        }

    public_base = (getattr(settings, "PUBLIC_BASE_URL", None) or "").strip()
    if public_base:
        normalized_url_path = clean if clean.startswith("/") else f"/{clean}"
        fallback_url = urljoin(public_base.rstrip("/") + "/", normalized_url_path.lstrip("/"))
        return {
            "ok": True,
            "mode": "http",
            "path": None,
            "url": fallback_url,
            "normalized": fallback_url,
            "hint": "audio_public_base_url_fallback",
            "reason": "public_base_url_fallback",
        }

    return {"ok": False, "mode": "missing", "path": None, "url": None, "normalized": clean, "hint": "audio_asset_not_found", "reason": "asset_not_found_locally_and_no_http_fallback"}


def _resolve_local_static_asset_path(path_value: Any) -> str | None:
    path_clean = str(path_value or "").strip()
    if not path_clean:
        return None
    parsed = urlparse(path_clean)
    candidate_path = parsed.path if parsed.scheme in {"http", "https"} else path_clean
    normalized_path = str(candidate_path or "").split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if normalized_path.startswith("backend/static/"):
        normalized_path = normalized_path[len("backend/"):]
    if normalized_path.startswith("assets/"):
        normalized_path = f"static/{normalized_path}"
    elif not normalized_path.startswith("static/"):
        normalized_path = f"static/{normalized_path}"
    if not normalized_path.startswith("static/assets/"):
        return None
    relative_path = normalized_path[len("static/"):].strip("/")
    if not relative_path:
        return None
    candidate = (BACKEND_DIR / "static" / relative_path).resolve()
    try:
        candidate.relative_to((BACKEND_DIR / "static").resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return str(candidate)
    return None


def _build_reference_image_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    refs_by_role: dict[str, list[str]] = {}
    for source_map in (
        payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {},
        payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {},
        (payload.get("connected_context_summary") or {}).get("refsByRole")
        if isinstance((payload.get("connected_context_summary") or {}).get("refsByRole"), dict)
        else {},
    ):
        for role, refs in source_map.items():
            normalized_role = _normalize_scenario_role(role)
            if not normalized_role or not isinstance(refs, list):
                continue
            refs_by_role.setdefault(normalized_role, []).extend([str(ref).strip() for ref in refs if str(ref).strip()])
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    for role, item in context_refs.items():
        normalized_role = _normalize_scenario_role(role)
        if not normalized_role or not isinstance(item, dict):
            continue
        refs = item.get("refs") if isinstance(item.get("refs"), list) else []
        refs_by_role.setdefault(normalized_role, []).extend([str(ref).strip() for ref in refs if str(ref).strip()])

    image_parts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for role in SCENARIO_CANONICAL_ROLES:
        for ref in refs_by_role.get(role, []):
            local_path = _resolve_local_static_asset_path(ref)
            if not local_path or local_path in seen_paths:
                continue
            mime_type = (mimetypes.guess_type(local_path)[0] or "application/octet-stream").strip().lower()
            if not mime_type.startswith("image/"):
                continue
            try:
                with open(local_path, "rb") as image_file:
                    raw_image = image_file.read()
            except OSError:
                continue
            if not raw_image:
                continue
            image_parts.append({"text": f"Reference image for role {role}:"})
            image_parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64.b64encode(raw_image).decode("utf-8"),
                    }
                }
            )
            seen_paths.add(local_path)
    return image_parts


def _normalize_audio_context(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    source_audio_meta = source_metadata.get("audio") if isinstance(source_metadata.get("audio"), dict) else {}
    metadata_audio = metadata.get("audio") if isinstance(metadata.get("audio"), dict) else {}
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}

    source_mode = str(
        source.get("source_mode")
        or payload.get("source_mode")
        or payload.get("sourceMode")
        or metadata.get("sourceMode")
        or "audio"
    ).strip().lower()
    duration_candidates = [
        ("payload.audioDurationSec", payload.get("audioDurationSec")),
        ("source.audioDurationSec", source.get("audioDurationSec")),
        ("source.metadata.audioDurationSec", source_metadata.get("audioDurationSec")),
        ("metadata.audioDurationSec", metadata.get("audioDurationSec")),
        ("metadata.audio.durationSec", metadata_audio.get("durationSec")),
        ("source.metadata.audio.durationSec", source_audio_meta.get("durationSec")),
        ("source_metadata.audioDurationSec", (payload.get("source_metadata") or {}).get("audioDurationSec") if isinstance(payload.get("source_metadata"), dict) else None),
    ]
    audio_duration_sec = 0.0
    duration_source = "missing"
    for key, value in duration_candidates:
        parsed = _safe_float(value, 0.0)
        if parsed > 0:
            audio_duration_sec = parsed
            duration_source = key
            break

    source_origin_raw = str(
        payload.get("source_origin")
        or source.get("source_origin")
        or source.get("origin")
        or metadata_audio.get("origin")
        or payload.get("sourceOrigin")
        or ("connected" if source_mode == "audio" else "")
    ).strip()
    source_origin_lower = source_origin_raw.lower()
    source_origin = "connected" if source_origin_lower in {"connected", "audio_node", "audio_upload", "audio_generated"} else source_origin_raw
    audio_url = str(
        source.get("source_value")
        or source.get("value")
        or payload.get("source_value")
        or metadata_audio.get("url")
        or source_audio_meta.get("url")
        or ""
    ).strip()
    content_type = str(controls.get("contentType") or "music_video").strip().lower() or "music_video"
    prefer_audio_over_text = _coerce_bool(controls.get("preferAudioOverText"), source_mode == "audio")
    has_audio = source_mode == "audio" and bool(audio_url)

    return {
        "hasAudio": has_audio,
        "audioUrl": audio_url or None,
        "audioDurationSec": audio_duration_sec,
        "audioDurationSource": duration_source,
        "sourceMode": source_mode.upper(),
        "sourceOrigin": source_origin or None,
        "sourceOriginRaw": source_origin_raw or None,
        "preferAudioOverText": prefer_audio_over_text,
        "timelineSource": "audio" if source_mode == "audio" else "text",
        "useAudioPhraseBoundaries": source_mode == "audio",
        "contentType": content_type,
    }


def _build_audio_analysis_fallback(duration_sec: float, hint: str, source: str = "none") -> dict[str, Any]:
    return {
        "ok": False,
        "audioDurationSec": _safe_float(duration_sec, 0.0),
        "phrases": [],
        "pauseWindows": [],
        "energyTransitions": [],
        "sections": [],
        "beats": [],
        "bars": [],
        "source": source,
        "hint": hint,
        "errors": [hint] if hint else [],
    }


def _analyze_audio_for_scenario_director(audio_context: dict[str, Any]) -> dict[str, Any]:
    audio_url = str(audio_context.get("audioUrl") or "").strip()
    payload_duration = _safe_float(audio_context.get("audioDurationSec"), 0.0)
    resolution = _resolve_audio_source_for_analysis(audio_url)
    if not resolution.get("ok"):
        return {
            **_build_audio_analysis_fallback(payload_duration, str(resolution.get("hint") or "audio_url_missing"), source="missing"),
            "audioUrlRaw": audio_url or None,
            "audioUrlNormalized": resolution.get("normalized"),
            "audioUrlResolutionMode": resolution.get("mode"),
            "audioResolvedPath": resolution.get("path"),
            "audioResolutionReason": resolution.get("reason"),
        }

    source = "local_file" if resolution.get("mode") == "local_file" else "http_download"
    temp_path: str | None = None
    errors: list[str] = []
    try:
        analysis = analyze_audio(str(resolution.get("path"))) if resolution.get("mode") == "local_file" and resolution.get("path") else None
        if analysis is None:
            fetch_url = str(resolution.get("url") or "")
            if not fetch_url:
                return {
                    **_build_audio_analysis_fallback(payload_duration, "audio_url_not_absolute", source=source),
                    "audioUrlRaw": audio_url or None,
                    "audioUrlNormalized": resolution.get("normalized"),
                    "audioUrlResolutionMode": "invalid",
                    "audioResolvedPath": resolution.get("path"),
                    "audioResolutionReason": resolution.get("reason"),
                }
            suffix = os.path.splitext(urlparse(fetch_url).path)[1] or ".audio"
            response = requests.get(fetch_url, timeout=30)
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(response.content)
                temp_path = tmp.name
            analysis = analyze_audio(temp_path)
        semantic = derive_audio_semantic_profile(analysis)
        return {
            "ok": True,
            "audioDurationSec": _safe_float(analysis.get("duration"), payload_duration),
            "phrases": analysis.get("vocalPhrases") if isinstance(analysis.get("vocalPhrases"), list) else [],
            "pauseWindows": [
                {"start": _safe_float(item, 0.0), "end": _safe_float(item, 0.0)}
                for item in (analysis.get("pausePoints") or [])
            ],
            "energyTransitions": [{"timeSec": _safe_float(item, 0.0)} for item in (analysis.get("energyPeaks") or [])],
            "sections": analysis.get("sections") if isinstance(analysis.get("sections"), list) else [],
            "beats": analysis.get("beats") if isinstance(analysis.get("beats"), list) else [],
            "bars": analysis.get("bars") if isinstance(analysis.get("bars"), list) else [],
            "source": source,
            "hint": "analysis_ok",
            "errors": errors,
            "semantic": semantic,
            "audioUrlRaw": audio_url or None,
            "audioUrlNormalized": resolution.get("normalized"),
            "audioUrlResolutionMode": resolution.get("mode"),
            "audioResolvedPath": resolution.get("path"),
            "audioResolutionReason": resolution.get("reason"),
        }
    except Exception as exc:
        errors.append(f"audio_analysis_failed:{str(exc)[:180]}")
        return {
            **_build_audio_analysis_fallback(payload_duration, "audio_fetch_failed", source=source),
            "errors": errors,
            "audioUrlRaw": audio_url or None,
            "audioUrlNormalized": resolution.get("normalized"),
            "audioUrlResolutionMode": resolution.get("mode"),
            "audioResolvedPath": resolution.get("path"),
            "audioResolutionReason": resolution.get("reason"),
        }
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _analyze_audio_semantics_for_scenario_director(payload: dict[str, Any], audio_context: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    transcript_candidates = [
        metadata.get("audioTranscript"),
        metadata.get("transcript"),
        source_metadata.get("audioTranscript"),
        source_metadata.get("transcript"),
    ]
    transcript = ""
    for candidate in transcript_candidates:
        if isinstance(candidate, str) and candidate.strip():
            transcript = candidate.strip()
            break

    audio_url = str(audio_context.get("audioUrl") or "").strip() or None
    if not transcript:
        semantics = analyze_audio_semantics_fallback("", hint="no_asr_transcript")
    else:
        semantics = analyze_audio_semantics(audio_url, transcript_text=transcript)

    normalized = semantics if isinstance(semantics, dict) else {}
    return {
        "ok": _coerce_bool(normalized.get("ok"), bool(transcript)),
        "transcript": str(normalized.get("transcript") or transcript or "").strip(),
        "semanticSummary": str(normalized.get("semanticSummary") or "").strip(),
        "narrativeCore": str(normalized.get("narrativeCore") or "").strip(),
        "worldContext": str(normalized.get("worldContext") or "").strip(),
        "entities": [str(item).strip() for item in (normalized.get("entities") or []) if str(item).strip()],
        "impliedEvents": [str(item).strip() for item in (normalized.get("impliedEvents") or []) if str(item).strip()],
        "tone": str(normalized.get("tone") or "").strip(),
        "confidence": _safe_float(normalized.get("confidence"), 0.0),
        "hint": str(normalized.get("hint") or ("transcript_semantic_ok" if transcript else "no_asr_transcript")).strip(),
    }


def _build_audio_timeline_guidance(audio_analysis: dict[str, Any], audio_context: dict[str, Any]) -> dict[str, Any]:
    phrase_candidates = [
        {"timeSec": _safe_float((phrase or {}).get("end"), 0.0), "reason": "phrase_end", "weight": 10}
        for phrase in (audio_analysis.get("phrases") or [])
        if _safe_float((phrase or {}).get("end"), -1) > 0
    ]
    pause_candidates = [
        {"timeSec": _safe_float((pause or {}).get("start"), 0.0), "reason": "pause", "weight": 9}
        for pause in (audio_analysis.get("pauseWindows") or [])
        if _safe_float((pause or {}).get("start"), -1) > 0
    ]
    energy_candidates = [
        {"timeSec": _safe_float((transition or {}).get("timeSec"), 0.0), "reason": "energy", "weight": 5}
        for transition in (audio_analysis.get("energyTransitions") or [])
        if _safe_float((transition or {}).get("timeSec"), -1) > 0
    ]
    section_candidates: list[dict[str, Any]] = []
    for section in (audio_analysis.get("sections") or []):
        start = _safe_float((section or {}).get("start"), -1)
        end = _safe_float((section or {}).get("end"), -1)
        if start > 0:
            section_candidates.append({"timeSec": start, "reason": "section_start", "weight": 6})
        if end > 0:
            section_candidates.append({"timeSec": end, "reason": "section_end", "weight": 7})
    return {
        "timelineSource": "audio",
        "segmentationMode": "performance_arc_audio_timed"
        if str(audio_context.get("clipModeCanon") or "").strip() == "visual_performance_arc_v1"
        else "phrase-first",
        "sourceMode": audio_context.get("sourceMode"),
        "phraseCandidates": phrase_candidates,
        "pauseCandidates": pause_candidates,
        "energyCandidates": energy_candidates,
        "sectionCandidates": section_candidates,
        "hints": [
            "audio is source of timing truth",
            "align boundaries to phrase endings and pauses",
            "use section and energy transitions as secondary boundaries",
        ],
    }


def _build_phrase_first_segmentation_guidance(audio_analysis: dict[str, Any], audio_context: dict[str, Any]) -> dict[str, Any]:
    guidance = _build_audio_timeline_guidance(audio_analysis, audio_context)
    all_candidates = [
        *guidance.get("phraseCandidates", []),
        *guidance.get("pauseCandidates", []),
        *guidance.get("energyCandidates", []),
        *guidance.get("sectionCandidates", []),
    ]
    ordered = sorted(
        all_candidates,
        key=lambda item: (_safe_float(item.get("timeSec"), 0.0), -int(item.get("weight") or 0)),
    )
    dedup: list[dict[str, Any]] = []
    for item in ordered:
        t = _safe_float(item.get("timeSec"), -1)
        if t <= 0:
            continue
        if dedup and abs(_safe_float(dedup[-1].get("timeSec"), -99) - t) < 0.35:
            if int(item.get("weight") or 0) > int(dedup[-1].get("weight") or 0):
                dedup[-1] = item
            continue
        dedup.append(item)
    guidance["boundaryCandidates"] = dedup[:80]
    return guidance


def _resolve_effective_role_type_by_role(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], bool]:
    explicit_map = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    effective: dict[str, str] = {}
    source_map: dict[str, str] = {}
    role_override_applied = False
    for role in ("character_1", "character_2", "character_3"):
        explicit = str(explicit_map.get(role) or "").strip().lower()
        if explicit in ALLOWED_EXPLICIT_ROLE_TYPES and explicit != "auto":
            effective[role] = explicit
            source_map[role] = "explicit"
            role_override_applied = True
            continue
        source_map[role] = "default"
    if not any(value == "hero" for value in effective.values()):
        effective.setdefault("character_1", "hero")
        source_map["character_1"] = source_map.get("character_1") if source_map.get("character_1") == "explicit" else "default"
    return effective, source_map, role_override_applied


def _build_role_lookup_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    lookup: dict[str, str] = {}
    for role, item in refs.items():
        normalized_role = _normalize_scenario_role(role)
        if not normalized_role or not isinstance(item, dict):
            continue
        candidates = [
            item.get("preview"),
            item.get("label"),
            item.get("source_label"),
            role,
        ]
        for candidate in candidates:
            clean = str(candidate or "").strip().lower()
            if clean:
                lookup[clean] = normalized_role
    return lookup


def _normalize_scenario_role(role: Any, *, role_lookup: dict[str, str] | None = None) -> str:
    clean = str(role or "").strip().lower()
    if not clean:
        return ""
    if role_lookup and clean in role_lookup:
        return role_lookup[clean]
    match = re.fullmatch(r"character[\s_-]*(\d+)", clean)
    if match:
        return f"character_{match.group(1)}"
    return SCENARIO_ROLE_ALIASES.get(clean, clean)


def _normalize_content_type(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in SCENARIO_CONTENT_TYPE_REGISTRY else "story"


def _resolve_requested_content_type(payload: dict[str, Any]) -> str:
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    return _normalize_content_type(controls.get("contentType") or payload.get("contentType"))


def _is_content_type_enabled(content_type: Any) -> bool:
    normalized = _normalize_content_type(content_type)
    policy = SCENARIO_CONTENT_TYPE_REGISTRY.get(normalized) or SCENARIO_CONTENT_TYPE_REGISTRY["story"]
    return bool(policy.get("is_enabled", True))


def _get_safe_content_type(content_type: Any, fallback_content_type: str = "story") -> str:
    normalized = _normalize_content_type(content_type)
    if _is_content_type_enabled(normalized):
        return normalized
    fallback_normalized = _normalize_content_type(fallback_content_type)
    if _is_content_type_enabled(fallback_normalized):
        return fallback_normalized
    for key, policy in SCENARIO_CONTENT_TYPE_REGISTRY.items():
        if bool(policy.get("is_enabled", True)):
            return key
    return "story"


def _get_content_type_policy(payload: dict[str, Any]) -> dict[str, Any]:
    requested = _resolve_requested_content_type(payload)
    effective = _get_safe_content_type(requested, "story")
    base = SCENARIO_CONTENT_TYPE_REGISTRY.get(effective) or SCENARIO_CONTENT_TYPE_REGISTRY["story"]
    return {
        "value": effective,
        **base,
        "requestedValue": requested,
        "requestedEnabled": _is_content_type_enabled(requested),
        "fallbackApplied": requested != effective,
    }


def _resolve_effective_global_music_prompt(payload: dict[str, Any], raw_music_prompt: str) -> str:
    clean_prompt = str(raw_music_prompt or "").strip()
    policy = _get_content_type_policy(payload)
    if policy.get("uses_global_music_prompt", True):
        return clean_prompt
    return clean_prompt if clean_prompt else ""


def _collect_known_roles(payload: dict[str, Any], scenes: list[ScenarioDirectorScene]) -> list[str]:
    role_lookup = _build_role_lookup_from_payload(payload)
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    refs_by_role_raw = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    connected_refs = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    connected_summary_refs = connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {}
    raw_role_types = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    effective_role_types, _, _ = _resolve_effective_role_type_by_role(payload)
    ordered: list[str] = []

    def _push(role: Any) -> None:
        normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    for role in refs.keys():
        _push(role)
    for role in refs_by_role_raw.keys():
        _push(role)
    for role in connected_refs.keys():
        _push(role)
    for role in connected_summary_refs.keys():
        _push(role)
    for role in raw_role_types.keys():
        _push(role)
    for role in effective_role_types.keys():
        _push(role)
    for scene in scenes:
        for actor in scene.actors:
            _push(actor)
        if str(scene.location or "").strip():
            _push("location")
        if any(str(prop or "").strip() for prop in (scene.props or [])):
            _push("props")
    for role in SCENARIO_CANONICAL_ROLES:
        if role in ordered:
            continue
        if role in refs or role in refs_by_role_raw or role in connected_refs or role in connected_summary_refs:
            _push(role)
    return ordered


def _collect_refs_by_role(payload: dict[str, Any], known_roles: list[str]) -> tuple[dict[str, list[str]], dict[str, bool]]:
    role_lookup = _build_role_lookup_from_payload(payload)
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    top_level_refs = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    connected_refs = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    connected_summary_refs = connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {}
    out: dict[str, list[str]] = {role: [] for role in known_roles}

    def _extend(role: Any, refs_value: Any) -> None:
        normalized_role = _normalize_scenario_role(role, role_lookup=role_lookup)
        if not normalized_role or normalized_role not in out or not isinstance(refs_value, list):
            return
        clean_refs = [str(ref).strip() for ref in refs_value if str(ref).strip()]
        if clean_refs:
            out[normalized_role].extend(clean_refs)

    for role, refs_value in top_level_refs.items():
        _extend(role, refs_value)
    for role, item in refs.items():
        if isinstance(item, dict):
            _extend(role, item.get("refs"))
    for role, refs_value in connected_refs.items():
        _extend(role, refs_value)
    for role, refs_value in connected_summary_refs.items():
        _extend(role, refs_value)
    merged = {role: list(dict.fromkeys(items)) for role, items in out.items() if items}
    source_flags = {
        "hasTopLevelRefsByRole": bool(top_level_refs),
        "hasContextRefs": bool(refs),
        "hasConnectedRefsByRole": bool(connected_refs or connected_summary_refs),
    }
    return merged, source_flags


def _collect_connected_refs_by_role(payload: dict[str, Any], known_roles: list[str]) -> dict[str, list[str]]:
    role_lookup = _build_role_lookup_from_payload(payload)
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    connected_raw = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    summary_refs = connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {}
    out: dict[str, list[str]] = {role: [] for role in known_roles}
    for source_map in (connected_raw, summary_refs):
        for role, refs in source_map.items():
            normalized_role = _normalize_scenario_role(role, role_lookup=role_lookup)
            if not normalized_role or normalized_role not in out:
                continue
            if isinstance(refs, list):
                out[normalized_role].extend([str(ref).strip() for ref in refs if str(ref).strip()])
    return {role: list(dict.fromkeys(items)) for role, items in out.items() if items}


def _has_connected_ref_for_role(payload: dict[str, Any], role: str) -> bool:
    normalized_role = _normalize_scenario_role(role)
    if not normalized_role:
        return False
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    item = refs.get(normalized_role) if isinstance(refs.get(normalized_role), dict) else {}
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    if _coerce_bool(meta.get("connected"), False):
        return True
    if isinstance(item.get("refs"), list) and any(str(ref).strip() for ref in item.get("refs") or []):
        return True
    try:
        if int(item.get("count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    for source_map in (
        payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {},
        connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {},
    ):
        refs_list = source_map.get(normalized_role)
        if isinstance(refs_list, list) and any(str(ref).strip() for ref in refs_list):
            return True
    return False


def _extract_scene_actor_roles(
    scene: ScenarioDirectorScene,
    raw_scene: dict[str, Any],
    *,
    role_lookup: dict[str, str],
    known_roles: list[str],
    hero_participants: list[str],
    supporting_participants: list[str],
) -> list[str]:
    actor_roles: list[str] = []
    scene_signal_parts: list[str] = []

    def _collect_signal(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text:
            scene_signal_parts.append(text)

    def _push(role: Any) -> None:
        normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
        if normalized and normalized in known_roles and normalized in SCENARIO_CAST_ROLES and normalized not in actor_roles:
            actor_roles.append(normalized)

    for key in ("primaryRole", "primary_role"):
        _collect_signal(raw_scene.get(key))
        _push(raw_scene.get(key))
    for key in ("secondaryRoles", "secondary_roles", "sceneActiveRoles", "scene_active_roles", "refsUsed", "refs_used"):
        for role in (raw_scene.get(key) or []):
            _push(role)
    for key in ("sceneType", "scene_type", "shotType", "shot_type", "title", "description", "prompt", "action", "beat", "imagePrompt", "videoPrompt"):
        _collect_signal(raw_scene.get(key))
    _collect_signal(scene.scene_goal)
    _collect_signal(scene.frame_description)
    _collect_signal(scene.action_in_frame)
    _collect_signal(scene.image_prompt)
    _collect_signal(scene.video_prompt)
    for actor in (scene.actors or []):
        _push(actor)
    for role in (raw_scene.get("participants") or []):
        _push(role)
    for key in ("mustAppear", "must_appear"):
        for role in (raw_scene.get(key) or []):
            _push(role)
    ref_directives = raw_scene.get("refDirectives") if isinstance(raw_scene.get("refDirectives"), dict) else {}
    for role, directive in ref_directives.items():
        if str(directive or "").strip().lower() in {"hero", "required"}:
            _push(role)
        _collect_signal(directive)
    if not actor_roles:
        for role in [*hero_participants, *supporting_participants]:
            _push(role)
    scene_signal = " ".join(scene_signal_parts)
    group_is_explicit = (
        "group" in {
            _normalize_scenario_role(role, role_lookup=role_lookup)
            for role in (raw_scene.get("mustAppear") or raw_scene.get("must_appear") or [])
        }
        or str(ref_directives.get("group") or "").strip().lower() in {"hero", "required"}
        or any(hint in scene_signal for hint in GROUP_NARRATIVE_REQUIRED_HINTS)
    )
    if "group" in actor_roles and not group_is_explicit:
        actor_roles = [role for role in actor_roles if role != "group"]
    return actor_roles


def _scene_has_shared_hint(scene: ScenarioDirectorScene, raw_scene: dict[str, Any]) -> bool:
    signal_parts: list[str] = []
    for key in (
        "sceneType",
        "scene_type",
        "shotType",
        "shot_type",
        "title",
        "description",
        "beat",
        "action",
        "imagePrompt",
        "image_prompt",
        "videoPrompt",
        "video_prompt",
    ):
        signal_parts.append(str(raw_scene.get(key) or "").strip().lower())
    signal_parts.extend(
        [
            str(scene.scene_goal or "").strip().lower(),
            str(scene.frame_description or "").strip().lower(),
            str(scene.action_in_frame or "").strip().lower(),
            str(scene.image_prompt or "").strip().lower(),
            str(scene.video_prompt or "").strip().lower(),
        ]
    )
    for list_key in ("participants", "mustAppear", "must_appear", "secondaryRoles", "secondary_roles", "refsUsed", "refs_used"):
        values = raw_scene.get(list_key)
        if isinstance(values, list):
            signal_parts.append(" ".join(str(value or "").strip().lower() for value in values))
    scene_signal = " ".join(part for part in signal_parts if part)
    if any(hint in scene_signal for hint in DUO_SCENE_HINTS):
        return True
    return any(hint in scene_signal for hint in DUO_SCENE_EXTRA_HINTS)


def _extract_scene_world_anchor_roles(raw_scene: dict[str, Any], actor_roles: list[str], *, role_lookup: dict[str, str]) -> list[str]:
    anchor_roles: list[str] = []

    def _push(role: Any) -> None:
        normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
        if (
            normalized
            and normalized in WORLD_ANCHOR_ROLES
            and normalized not in actor_roles
            and normalized not in anchor_roles
        ):
            anchor_roles.append(normalized)

    for key in ("sceneActiveRoles", "scene_active_roles", "refsUsed", "refs_used", "refRoles", "activeRoles"):
        for role in (raw_scene.get(key) or []):
            _push(role)
    ref_directives = raw_scene.get("refDirectives") if isinstance(raw_scene.get("refDirectives"), dict) else {}
    for role, directive in ref_directives.items():
        if str(directive or "").strip():
            _push(role)
    must_appear = raw_scene.get("mustAppear") if raw_scene.get("mustAppear") is not None else raw_scene.get("must_appear")
    if isinstance(must_appear, list):
        for role in must_appear:
            normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
            if normalized in {"location", "style", "animal", "animal_1", "group", "group_faces"}:
                _push(normalized)
            if normalized == "props":
                _push(normalized)
    return anchor_roles


def _resolve_scene_must_appear(
    scene: ScenarioDirectorScene,
    raw_scene: dict[str, Any],
    *,
    role_lookup: dict[str, str],
    actor_roles: list[str],
    primary_role: str,
    hero_participants: list[str],
) -> list[str]:
    def _normalize_actor_roles(values: Any) -> list[str]:
        out: list[str] = []
        if not isinstance(values, list):
            return out
        for role in values:
            normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
            if normalized and normalized in SCENARIO_CAST_ROLES and normalized in actor_roles and normalized not in out:
                out.append(normalized)
        return out

    explicit = _normalize_actor_roles(raw_scene.get("mustAppear") if raw_scene.get("mustAppear") is not None else raw_scene.get("must_appear"))
    if "group" in explicit:
        group_required = _is_group_narratively_required(
            scene=scene,
            raw_scene=raw_scene,
            ref_directives=raw_scene.get("refDirectives") if isinstance(raw_scene.get("refDirectives"), dict) else {},
            must_appear_roles=explicit,
        )
        if not group_required:
            explicit = [role for role in explicit if role != "group"]
    if explicit:
        return explicit
    explicit_primary = _normalize_scenario_role(
        raw_scene.get("primaryRole") if raw_scene.get("primaryRole") is not None else raw_scene.get("primary_role"),
        role_lookup=role_lookup,
    )
    explicit_secondary = _normalize_actor_roles(raw_scene.get("secondaryRoles") if raw_scene.get("secondaryRoles") is not None else raw_scene.get("secondary_roles"))
    if explicit_primary and explicit_primary in actor_roles and explicit_primary in SCENARIO_CAST_ROLES and explicit_secondary:
        return list(dict.fromkeys([explicit_primary, *explicit_secondary]))

    scene_signal = " ".join(
        [
            str(raw_scene.get("sceneType") or raw_scene.get("scene_type") or "").strip().lower(),
            str(raw_scene.get("shotType") or raw_scene.get("shot_type") or "").strip().lower(),
            str(raw_scene.get("title") or "").strip().lower(),
            str(raw_scene.get("description") or "").strip().lower(),
            str(raw_scene.get("prompt") or "").strip().lower(),
            str(raw_scene.get("beat") or "").strip().lower(),
            str(raw_scene.get("action") or "").strip().lower(),
            str(raw_scene.get("imagePrompt") or raw_scene.get("image_prompt") or scene.image_prompt or "").strip().lower(),
            str(raw_scene.get("videoPrompt") or raw_scene.get("video_prompt") or scene.video_prompt or "").strip().lower(),
            str(scene.scene_goal or "").strip().lower(),
            str(scene.frame_description or "").strip().lower(),
            str(scene.action_in_frame or "").strip().lower(),
        ]
    )
    if any(hint in scene_signal for hint in DUO_SCENE_HINTS):
        duo_candidates = [role for role in hero_participants if role in actor_roles]
        for role in actor_roles:
            if role not in duo_candidates:
                duo_candidates.append(role)
        if len(duo_candidates) >= 2:
            return duo_candidates[:2]

    if primary_role and primary_role in SCENARIO_CAST_ROLES:
        return [primary_role]
    if actor_roles:
        return [actor_roles[0]]
    return []


def _is_audio_connected(payload: dict[str, Any]) -> bool:
    audio_context = _normalize_audio_context(payload)
    source_origin = str(audio_context.get("sourceOrigin") or "connected").strip().lower()
    return bool(audio_context.get("hasAudio") and source_origin in {"connected", "audio_node", "audio_upload", "audio_generated"})


def _estimate_text_overlap(text: str, anchor: str) -> float:
    base = re.findall(r"[a-zA-Zа-яА-Я0-9_]+", str(text or "").lower())
    ref = set(re.findall(r"[a-zA-Zа-яА-Я0-9_]+", str(anchor or "").lower()))
    if not base or not ref:
        return 0.0
    shared = sum(1 for token in base if token in ref)
    return round(shared / max(1, len(base)), 4)


def _append_decision_flag(reason: str, flag: str, value: Any = True) -> str:
    base = str(reason or "").strip()
    token = f"{flag}={str(value).lower() if isinstance(value, bool) else value}"
    if token in base:
        return base
    return f"{base}; {token}".strip("; ").strip()


def _is_group_narratively_required(
    *,
    scene: ScenarioDirectorScene,
    raw_scene: dict[str, Any],
    ref_directives: dict[str, Any] | None = None,
    must_appear_roles: list[str] | None = None,
) -> bool:
    directives = ref_directives if isinstance(ref_directives, dict) else {}
    must_appear = [str(role or "").strip().lower() for role in (must_appear_roles or []) if str(role or "").strip()]
    if "group" in must_appear:
        return True
    if str(directives.get("group") or "").strip().lower() in {"hero", "required"}:
        return True
    scene_signal = " ".join(
        [
            str(raw_scene.get("sceneType") or raw_scene.get("scene_type") or "").strip().lower(),
            str(raw_scene.get("shotType") or raw_scene.get("shot_type") or "").strip().lower(),
            str(raw_scene.get("summary") or raw_scene.get("description") or "").strip().lower(),
            str(raw_scene.get("goal") or raw_scene.get("sceneGoal") or raw_scene.get("scene_goal") or "").strip().lower(),
            str(raw_scene.get("prompt") or raw_scene.get("imagePrompt") or raw_scene.get("videoPrompt") or "").strip().lower(),
            str(raw_scene.get("action") or raw_scene.get("beat") or "").strip().lower(),
            str(scene.scene_goal or "").strip().lower(),
            str(scene.frame_description or "").strip().lower(),
            str(scene.action_in_frame or "").strip().lower(),
            str(scene.image_prompt or "").strip().lower(),
            str(scene.video_prompt or "").strip().lower(),
        ]
    )
    return any(hint in scene_signal for hint in GROUP_NARRATIVE_REQUIRED_HINTS)


def _merge_must_not_appear(*role_sets: Any) -> list[str]:
    merged: list[str] = []
    for role_set in role_sets:
        if not isinstance(role_set, list):
            continue
        for role in role_set:
            normalized = str(role or "").strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
    return merged


def _build_director_output(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> dict[str, Any]:
    content_type_policy = _get_content_type_policy(payload)
    is_music_video = str(content_type_policy.get("value") or "").strip().lower() == "music_video"
    prefer_explicit_duo_roles = (
        is_music_video
        and _has_connected_ref_for_role(payload, "character_1")
        and _has_connected_ref_for_role(payload, "character_2")
    )
    role_lookup = _build_role_lookup_from_payload(payload)
    known_roles = _collect_known_roles(payload, storyboard_out.scenes)
    display_label_by_role = _build_display_label_by_role(payload, known_roles)
    refs_by_role, refs_merge_flags = _collect_refs_by_role(payload, known_roles)
    connected_refs_by_role = _collect_connected_refs_by_role(payload, known_roles)
    effective_role_types, role_type_source, _ = _resolve_effective_role_type_by_role(payload)
    role_type_by_role: dict[str, str] = {}
    raw_role_types = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    for role in known_roles:
        normalized = _normalize_scenario_role(role)
        raw_type = str(raw_role_types.get(role) or raw_role_types.get(normalized) or "").strip().lower()
        if raw_type in ALLOWED_EXPLICIT_ROLE_TYPES:
            role_type_by_role[normalized] = raw_type
    for role, role_type in effective_role_types.items():
        normalized = _normalize_scenario_role(role)
        if normalized:
            role_type_by_role[normalized] = str(role_type or "").strip().lower() or "auto"
    for role in known_roles:
        normalized = _normalize_scenario_role(role)
        if normalized and normalized not in role_type_by_role:
            role_type_by_role[normalized] = "auto"

    connected_context_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    role_presence = {
        role for role in known_roles
        if len(refs_by_role.get(role) or []) > 0 or len(connected_refs_by_role.get(role) or []) > 0
    }
    for scene in storyboard_out.scenes:
        for actor in scene.actors:
            normalized_actor = _normalize_scenario_role(actor, role_lookup=role_lookup)
            if normalized_actor:
                role_presence.add(normalized_actor)
        if str(scene.location or "").strip():
            role_presence.add("location")
        if any(str(item or "").strip() for item in (scene.props or [])):
            role_presence.add("props")
    present_cast_roles = [role for role in known_roles if role in SCENARIO_CAST_ROLES and role in role_presence]
    present_world_roles = [role for role in known_roles if role in SCENARIO_WORLD_ROLES and role in role_presence]
    hero_participants = [role for role in known_roles if role in SCENARIO_CAST_ROLES and role_type_by_role.get(role) == "hero" and role in role_presence]
    supporting_participants = [
        role for role in known_roles
        if role in SCENARIO_CAST_ROLES and role in role_presence and role_type_by_role.get(role) in {"support", "antagonist"}
    ]
    if not hero_participants and "character_1" in role_presence:
        hero_participants = ["character_1"]
    must_appear_roles = [role for role in known_roles if role in SCENARIO_CAST_ROLES and role in role_presence]
    ref_directives = {
        role: ("hero" if role in hero_participants else ("required" if role in must_appear_roles or role in {"location", "props"} else "optional"))
        for role in known_roles
        if role in refs_by_role or role in connected_refs_by_role
    }

    history = {
        "summary": storyboard_out.story_summary,
        "fullScenario": storyboard_out.full_scenario,
        "characterRoles": _build_character_roles(payload, display_label_by_role, known_roles),
        "toneStyleDirection": str(payload.get("director_controls", {}).get("styleProfile") or "").strip() or "Scenario Director tone guidance from Gemini.",
        "directorSummary": storyboard_out.director_summary,
        "presentCastRoles": present_cast_roles,
        "presentWorldRoles": present_world_roles,
        "refsPresentByRole": refs_by_role,
        "connectedRefsPresentByRole": connected_refs_by_role,
        "hasProps": "props" in present_world_roles,
        "hasLocation": "location" in present_world_roles,
        "hasStyle": "style" in present_world_roles,
    }
    scenes = []
    video = []
    sound = []
    payload_scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    for scene in storyboard_out.scenes:
        scene_index = len(scenes)
        raw_scene = payload_scenes[scene_index] if scene_index < len(payload_scenes) and isinstance(payload_scenes[scene_index], dict) else {}
        scene_ref_directives = raw_scene.get("refDirectives") if isinstance(raw_scene.get("refDirectives"), dict) else {}
        participants = _scene_participants(scene, role_lookup)
        actor_roles = _extract_scene_actor_roles(
            scene,
            raw_scene,
            role_lookup=role_lookup,
            known_roles=known_roles,
            hero_participants=hero_participants,
            supporting_participants=supporting_participants,
        )
        actor_roles_before_rescue = list(actor_roles)
        shared_scene_hint = _scene_has_shared_hint(scene, raw_scene)
        if actor_roles and shared_scene_hint and len(actor_roles) < 2:
            rescue_pool = [*hero_participants, *supporting_participants]
            for role in rescue_pool:
                normalized = _normalize_scenario_role(role)
                if normalized and normalized in SCENARIO_CAST_ROLES and normalized not in actor_roles:
                    actor_roles.append(normalized)
                if len(actor_roles) >= 2:
                    break
        if prefer_explicit_duo_roles and shared_scene_hint:
            actor_roles = [role for role in actor_roles if role not in {"group", "group_faces"}]
            for explicit_role in ("character_1", "character_2"):
                if explicit_role not in actor_roles:
                    actor_roles.append(explicit_role)
        if prefer_explicit_duo_roles and shared_scene_hint:
            participants = [role for role in participants if role not in {"group", "group_faces"}]
            for explicit_role in ("character_1", "character_2"):
                if explicit_role in actor_roles and explicit_role not in participants:
                    participants.append(explicit_role)
        raw_anchor_roles = _extract_scene_world_anchor_roles(raw_scene, actor_roles, role_lookup=role_lookup)
        scene_anchor_roles: list[str] = list(raw_anchor_roles)
        if str(scene.location or "").strip() and (refs_by_role.get("location") or connected_refs_by_role.get("location")):
            scene_anchor_roles.append("location")
        if scene.props and (refs_by_role.get("props") or connected_refs_by_role.get("props")):
            scene_anchor_roles.append("props")
        if (
            (refs_by_role.get("style") or connected_refs_by_role.get("style"))
            and any(str(value or "").strip() for value in [scene.frame_description, scene.action_in_frame, scene.image_prompt, scene.video_prompt, scene.camera])
        ):
            scene_anchor_roles.append("style")
        scene_anchor_roles = list(dict.fromkeys(scene_anchor_roles))
        scene_active_roles = list(dict.fromkeys([*actor_roles, *scene_anchor_roles]))
        refs_used_roles: list[str] = []
        refs_used_map: dict[str, list[str]] = {}
        for role in dict.fromkeys([*scene_active_roles, *scene_anchor_roles]):
            role_refs = list(dict.fromkeys([*(refs_by_role.get(role) or []), *(connected_refs_by_role.get(role) or [])]))
            if not role_refs:
                continue
            refs_used_roles.append(role)
            refs_used_map[role] = role_refs
        explicit_primary_role = _normalize_scenario_role(
            raw_scene.get("primaryRole") if raw_scene.get("primaryRole") is not None else raw_scene.get("primary_role"),
            role_lookup=role_lookup,
        )
        primary_role = explicit_primary_role if explicit_primary_role in actor_roles else next(
            (role for role in actor_roles if role_type_by_role.get(role) == "hero"),
            actor_roles[0] if actor_roles else "",
        )
        secondary_roles = [role for role in actor_roles if role != primary_role]
        must_appear = _resolve_scene_must_appear(
            scene,
            raw_scene,
            role_lookup=role_lookup,
            actor_roles=actor_roles,
            primary_role=primary_role,
            hero_participants=hero_participants,
        )
        group_narratively_required = _is_group_narratively_required(
            scene=scene,
            raw_scene=raw_scene,
            ref_directives=scene_ref_directives,
            must_appear_roles=must_appear,
        )
        if prefer_explicit_duo_roles and shared_scene_hint:
            must_appear = [role for role in must_appear if role not in {"group", "group_faces"}]
            for explicit_role in ("character_1", "character_2"):
                if explicit_role in actor_roles and explicit_role not in must_appear:
                    must_appear.append(explicit_role)
        scene_role_dynamics = str(scene.scene_role_dynamics or "").strip().lower()
        active_character_roles = [role for role in actor_roles if role.startswith("character_")]
        is_environment_only_scene = bool(scene_role_dynamics == "environment" and not actor_roles and not active_character_roles)
        if "character_1" in actor_roles and "character_2" in actor_roles and "group" in actor_roles:
            actor_roles = [role for role in actor_roles if role != "group"]
            scene_active_roles = [role for role in scene_active_roles if role != "group"]
            refs_used_roles = [role for role in refs_used_roles if role != "group"]
            refs_used_map.pop("group", None)
            must_appear = [role for role in must_appear if role != "group"]
        if not group_narratively_required:
            actor_roles = [role for role in actor_roles if role != "group"]
            participants = [role for role in participants if role != "group"]
            scene_active_roles = [role for role in scene_active_roles if role != "group"]
            refs_used_roles = [role for role in refs_used_roles if role != "group"]
            refs_used_map.pop("group", None)
            must_appear = [role for role in must_appear if role != "group"]
        if is_environment_only_scene:
            scene_active_roles = [role for role in scene_active_roles if role in {"location", "style", "props"}]
            refs_used_roles = [role for role in refs_used_roles if role in {"location", "style", "props"}]
            refs_used_map = {role: refs for role, refs in refs_used_map.items() if role in {"location", "style", "props"}}
            must_appear = []
        primary_role = primary_role if primary_role in actor_roles else (actor_roles[0] if actor_roles else "")
        secondary_roles = [role for role in actor_roles if role != primary_role]
        support_entity_ids = [role for role in secondary_roles if role != "group" or group_narratively_required]
        start_frame_prompt = ""
        end_frame_prompt = ""
        if _scene_requires_explicit_first_last_prompts(scene):
            start_frame_prompt, end_frame_prompt = _derive_first_last_frame_prompts(scene, raw_scene, payload=payload)
            scene.start_frame_prompt = start_frame_prompt
            scene.end_frame_prompt = end_frame_prompt
        if _is_lip_sync_music_scene(scene):
            # Final prompt-level guard: enforce text canon after all per-scene adjustments.
            _enforce_lip_sync_music_visual_canon(scene)

        scene_must_not_appear = ["character_1", "character_2", "character_3"] if is_environment_only_scene else []
        if is_environment_only_scene or not group_narratively_required:
            scene_must_not_appear = _merge_must_not_appear(scene_must_not_appear, ["group"])
        scene_item = {
            "sceneId": scene.scene_id,
            "title": scene.scene_id,
            "displayIndex": scene.display_index or (scene_index + 1),
            "timeStart": scene.time_start,
            "timeEnd": scene.time_end,
            "duration": scene.duration,
            "participants": participants,
            "location": scene.location,
            "props": scene.props,
            "action": scene.action_in_frame,
            "emotion": scene.emotion,
            "audioEmotionDirection": scene.audio_emotion_direction,
            "sceneGoal": scene.scene_goal,
            "frameDescription": scene.frame_description,
            "actionInFrame": scene.action_in_frame,
            "cameraIdea": scene.camera,
            "imagePrompt": scene.image_prompt,
            "videoPrompt": scene.video_prompt,
            "video_negative_prompt": scene.video_negative_prompt,
            "videoNegativePrompt": scene.video_negative_prompt,
            "startFramePrompt": start_frame_prompt,
            "endFramePrompt": end_frame_prompt,
            "startFramePromptRu": start_frame_prompt,
            "startFramePromptEn": start_frame_prompt,
            "endFramePromptRu": end_frame_prompt,
            "endFramePromptEn": end_frame_prompt,
            "ltxMode": scene.ltx_mode,
            "whyThisMode": scene.ltx_reason,
            "renderMode": scene.render_mode,
            "resolvedWorkflowKey": scene.resolved_workflow_key,
            "resolvedWorkflowFile": scene.resolved_workflow_file,
            "scenePurpose": scene.scene_purpose,
            "clipArcStage": scene.clip_arc_stage,
            "storyFunction": scene.story_function or scene.clip_arc_stage,
            "absorbedStoryFunctions": scene.absorbed_story_functions,
            "beatFunction": scene.beat_function,
            "progressionReason": scene.progression_reason,
            "transitionFamily": scene.transition_family,
            "startVisualState": scene.start_visual_state,
            "endVisualState": scene.end_visual_state,
            "deltaAxes": scene.delta_axes,
            "visualIntensityLevel": scene.visual_intensity_level,
            "crowdRelationState": scene.crowd_relation_state,
            "performancePhase": scene.performance_phase,
            "viewerHook": scene.viewer_hook,
            "startFrameSource": scene.start_frame_source,
            "needsTwoFrames": scene.needs_two_frames,
            "continuation": scene.continuation_from_previous,
            "transitionType": scene.transition_type,
            "shotType": scene.shot_type,
            "requestedDurationSec": scene.requested_duration_sec,
            "narrationMode": scene.narration_mode,
            "localPhrase": scene.local_phrase,
            "sfx": scene.sfx,
            "soundNotes": scene.sfx,
            "pauseDuckSilenceNotes": "",
            "musicMixHint": scene.music_mix_hint,
            "lipSync": scene.lip_sync,
            "isLipSync": scene.lip_sync,
            "lipSyncText": scene.lip_sync_text,
            "sendAudioToGenerator": scene.send_audio_to_generator,
            "audioSliceKind": scene.audio_slice_kind,
            "musicVocalLipSyncAllowed": scene.music_vocal_lipsync_allowed,
            "performerPresentation": scene.performer_presentation,
            "vocalPresentation": scene.vocal_presentation,
            "lipSyncVoiceCompatibility": scene.lip_sync_voice_compatibility,
            "lipSyncVoiceCompatibilityReason": scene.lip_sync_voice_compatibility_reason,
            "audioSliceStartSec": scene.audio_slice_start_sec,
            "audioSliceEndSec": scene.audio_slice_end_sec,
            "audioSliceExpectedDurationSec": scene.audio_slice_expected_duration_sec,
            "performanceFraming": scene.performance_framing,
            "clipDecisionReason": _append_decision_flag(scene.clip_decision_reason, "groupNarrativelyRequired", group_narratively_required),
            "roleInfluenceApplied": scene.role_influence_applied or ("roleInfluenceApplied=true" in str(scene.clip_decision_reason or "")),
            "roleInfluenceReason": scene.role_influence_reason
            or (
                (re.search(r"roleInfluenceReason=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"roleInfluenceReason=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "sceneRoleDynamics": scene.scene_role_dynamics
            or (
                (re.search(r"sceneRoleDynamics=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"sceneRoleDynamics=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "multiCharacterIdentityLock": scene.multi_character_identity_lock or ("multiCharacterIdentityLock=true" in str(scene.clip_decision_reason or "")),
            "distinctCharacterSeparation": scene.distinct_character_separation or ("distinctCharacterSeparation=true" in str(scene.clip_decision_reason or "")),
            "duetLockEnabled": scene.duet_lock_enabled or ("duetLockEnabled=true" in str(scene.clip_decision_reason or "")),
            "duetCompositionMode": scene.duet_composition_mode
            or (
                (re.search(r"duetCompositionMode=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"duetCompositionMode=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "secondaryRoleVisibilityRequirement": scene.secondary_role_visibility_requirement
            or (
                (re.search(r"secondaryRoleVisibilityRequirement=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"secondaryRoleVisibilityRequirement=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "character2DriftGuard": scene.character2_drift_guard
            or (
                (re.search(r"character2DriftGuard=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"character2DriftGuard=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "duetIdentityContract": scene.duet_identity_contract,
            "appearanceDriftRisk": scene.appearance_drift_risk
            or (
                (re.search(r"appearanceDriftRisk=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"appearanceDriftRisk=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "directorGenreIntent": scene.director_genre_intent
            or (
                (re.search(r"directorGenreIntent=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"directorGenreIntent=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "directorGenreReason": scene.director_genre_reason,
            "directorToneBias": scene.director_tone_bias
            or (
                (re.search(r"directorToneBias=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"directorToneBias=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "workflowDecisionReason": _append_decision_flag(scene.workflow_decision_reason, "groupNarrativelyRequired", group_narratively_required),
            "lipSyncDecisionReason": scene.lip_sync_decision_reason,
            "audioSliceDecisionReason": scene.audio_slice_decision_reason,
            "videoReady": scene.video_ready,
            "videoBlockReasonCode": scene.video_block_reason_code,
            "videoBlockReasonMessage": scene.video_block_reason_message,
            "videoDowngradeReasonCode": scene.video_downgrade_reason_code,
            "videoDowngradeReasonMessage": scene.video_downgrade_reason_message,
            "videoGenerationRoute": scene.video_generation_route,
            "plannedVideoGenerationRoute": scene.planned_video_generation_route,
            "phraseBoundaryTrimApplied": scene.phrase_boundary_trim_applied,
            "phraseBoundaryTrimReason": scene.phrase_boundary_trim_reason,
            "originalSceneEnd": scene.original_scene_end,
            "trimmedSceneEnd": scene.trimmed_scene_end,
            "lipSyncRouteStateConsistent": scene.lip_sync_route_state_consistent,
            "audioSliceBoundsFilledFromScene": scene.audio_slice_bounds_filled_from_scene,
            "identityLockApplied": scene.identity_lock_applied,
            "identityLockNotes": scene.identity_lock_notes,
            "identityLockFieldsUsed": scene.identity_lock_fields_used,
            "heroAppearanceContract": scene.hero_appearance_contract,
            "previousStableImageAnchorApplied": scene.previous_stable_image_anchor_applied,
            "previousStableImageAnchorAvailable": scene.previous_stable_image_anchor_available,
            "previousStableImageAnchorUrlResolved": scene.previous_stable_image_anchor_url_resolved,
            "previousStableImageAnchorUsed": scene.previous_stable_image_anchor_used,
            "previousStableImageAnchorReason": scene.previous_stable_image_anchor_reason,
            "primaryRole": primary_role,
            "secondaryRoles": secondary_roles,
            "sceneActiveRoles": scene_active_roles,
            "refsUsed": refs_used_roles,
            "refsUsedByRole": refs_used_map,
            "mustAppear": must_appear,
            "mustNotAppear": scene_must_not_appear,
            "heroEntityId": primary_role if primary_role else "",
            "supportEntityIds": support_entity_ids,
            "refDirectives": {role: ref_directives.get(role, "optional") for role in refs_used_roles},
        }
        scene_task_mode = str(
            payload.get("taskMode")
            or payload.get("task_mode")
            or payload.get("mode")
            or "keep_identity"
        ).strip().lower()
        if scene_task_mode not in {"keep_identity", "virtual_try_on", "story_costume_change", "motion_only", "camera_only", "style_transfer"}:
            scene_task_mode = "keep_identity"
        source_outfit_profile = _build_scene_outfit_profile_from_payload(payload, role="character_1")
        target_outfit_profile = _extract_target_outfit_profile_from_payload(payload)
        source_outfit_replaced = scene_task_mode == "virtual_try_on" and bool(target_outfit_profile)
        effective_outfit_profile = target_outfit_profile if source_outfit_replaced else source_outfit_profile
        confidence_scores = _normalize_scene_outfit_confidence_scores(
            payload.get("confidenceScores") if isinstance(payload.get("confidenceScores"), dict) else {},
            fallback=_safe_float(scene.confidence, 0.5),
        )
        scene_item["taskMode"] = scene_task_mode
        scene_item["task_mode"] = scene_task_mode
        scene_item["sourceOutfitProfile"] = source_outfit_profile
        scene_item["targetOutfitProfile"] = target_outfit_profile
        scene_item["effectiveOutfitProfile"] = effective_outfit_profile
        scene_item["outfitProfile"] = effective_outfit_profile
        scene_item["sourceOutfitReplaced"] = source_outfit_replaced
        scene_item["outfitIdentitySource"] = "targetOutfitProfile" if source_outfit_replaced else "sourceOutfitProfile"
        scene_item["confidenceScores"] = confidence_scores
        scenes.append(scene_item)
        video.append(
            {
                "sceneId": scene.scene_id,
                "frameDescription": scene.frame_description,
                "actionInFrame": scene.action_in_frame,
                "cameraIdea": scene.camera,
                "imagePrompt": scene.image_prompt,
                "videoPrompt": scene.video_prompt,
                "video_negative_prompt": scene.video_negative_prompt,
                "videoNegativePrompt": scene.video_negative_prompt,
                "startFramePrompt": start_frame_prompt,
                "endFramePrompt": end_frame_prompt,
                "startFramePromptRu": start_frame_prompt,
                "startFramePromptEn": start_frame_prompt,
                "endFramePromptRu": end_frame_prompt,
                "endFramePromptEn": end_frame_prompt,
                "ltxMode": scene.ltx_mode,
                "whyThisMode": scene.ltx_reason,
                "renderMode": scene.render_mode,
                "resolvedWorkflowKey": scene.resolved_workflow_key,
                "resolvedWorkflowFile": scene.resolved_workflow_file,
                "audioSliceKind": scene.audio_slice_kind,
                "musicVocalLipSyncAllowed": scene.music_vocal_lipsync_allowed,
                "performerPresentation": scene.performer_presentation,
                "vocalPresentation": scene.vocal_presentation,
                "lipSyncVoiceCompatibility": scene.lip_sync_voice_compatibility,
                "lipSyncVoiceCompatibilityReason": scene.lip_sync_voice_compatibility_reason,
                "audioEmotionDirection": scene.audio_emotion_direction,
                "videoReady": scene.video_ready,
                "videoBlockReasonCode": scene.video_block_reason_code,
                "videoBlockReasonMessage": scene.video_block_reason_message,
                "videoDowngradeReasonCode": scene.video_downgrade_reason_code,
                "videoDowngradeReasonMessage": scene.video_downgrade_reason_message,
                "videoGenerationRoute": scene.video_generation_route,
                "plannedVideoGenerationRoute": scene.planned_video_generation_route,
                "startFrameSource": scene.start_frame_source,
                "needsTwoFrames": scene.needs_two_frames,
                "continuation": scene.continuation_from_previous,
                "transitionType": scene.transition_type,
                "shotType": scene.shot_type,
                "performanceFraming": scene.performance_framing,
                "requestedDurationSec": scene.requested_duration_sec,
                "sceneActiveRoles": scene_active_roles,
                "duetLockEnabled": scene.duet_lock_enabled,
                "duetIdentityContract": scene.duet_identity_contract,
                "directorGenreIntent": scene.director_genre_intent,
                "sceneContract": {
                    "activeRoles": scene_active_roles,
                    "duetLockEnabled": scene.duet_lock_enabled,
                    "duetIdentityContract": scene.duet_identity_contract,
                    "directorGenreIntent": scene.director_genre_intent,
                    "taskMode": scene_task_mode,
                    "task_mode": scene_task_mode,
                    "sourceOutfitProfile": source_outfit_profile,
                    "targetOutfitProfile": target_outfit_profile,
                    "effectiveOutfitProfile": effective_outfit_profile,
                    "outfitProfile": effective_outfit_profile,
                    "sourceOutfitReplaced": source_outfit_replaced,
                    "outfitIdentitySource": "targetOutfitProfile" if source_outfit_replaced else "sourceOutfitProfile",
                    "confidenceScores": confidence_scores,
                    "heroAppearanceContract": scene.hero_appearance_contract,
                    "previousStableImageAnchorApplied": scene.previous_stable_image_anchor_applied,
                    "previousStableImageAnchorAvailable": scene.previous_stable_image_anchor_available,
                    "previousStableImageAnchorUrlResolved": scene.previous_stable_image_anchor_url_resolved,
                    "previousStableImageAnchorUsed": scene.previous_stable_image_anchor_used,
                    "previousStableImageAnchorReason": scene.previous_stable_image_anchor_reason,
                    "audioEmotionDirection": scene.audio_emotion_direction,
                },
            }
        )
        sound.append(
            {
                "sceneId": scene.scene_id,
                "narrationMode": scene.narration_mode,
                "localPhrase": scene.local_phrase,
                "sfx": scene.sfx,
                "soundNotes": scene.sfx,
                "pauseDuckSilenceNotes": "",
                "lipSync": scene.lip_sync,
                "lipSyncText": scene.lip_sync_text,
                "sendAudioToGenerator": scene.send_audio_to_generator,
                "audioSliceKind": scene.audio_slice_kind,
                "musicVocalLipSyncAllowed": scene.music_vocal_lipsync_allowed,
                "audioSliceStartSec": scene.audio_slice_start_sec,
                "audioSliceEndSec": scene.audio_slice_end_sec,
                "audioSliceExpectedDurationSec": scene.audio_slice_expected_duration_sec,
            }
        )
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s primaryRole=%s", scene.scene_id, primary_role)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s secondaryRoles=%s", scene.scene_id, secondary_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s actorRoles(before)=%s", scene.scene_id, actor_roles_before_rescue)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s actorRoles(after rescue)=%s", scene.scene_id, actor_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s sharedSceneHint=%s", scene.scene_id, shared_scene_hint)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s anchorRoles(raw)=%s", scene.scene_id, raw_anchor_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s anchorRoles(final)=%s", scene.scene_id, scene_anchor_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s sceneActiveRoles=%s", scene.scene_id, scene_active_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s refsUsed=%s", scene.scene_id, refs_used_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s mustAppear=%s", scene.scene_id, must_appear)
    effective_global_music_prompt = storyboard_out.music_prompt if content_type_policy.get("uses_global_music_prompt", True) else ""
    music = {
        "globalMusicPrompt": effective_global_music_prompt,
        "mood": str(payload.get("director_controls", {}).get("styleProfile") or "").strip(),
        "style": f"{payload.get('director_controls', {}).get('contentType') or ''} / {payload.get('director_controls', {}).get('styleProfile') or ''}".strip(" /"),
        "pacingHints": "Use the Gemini scene pacing to build intro, escalation, climax, and resolution.",
    }
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package knownRoles=%s", known_roles)
    logger.debug(
        "[SCENARIO DIRECTOR OUTPUT] package refs merge sources top=%s context=%s connected=%s",
        refs_merge_flags.get("hasTopLevelRefsByRole"),
        refs_merge_flags.get("hasContextRefs"),
        refs_merge_flags.get("hasConnectedRefsByRole"),
    )
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package refsByRole keys=%s", sorted(refs_by_role.keys()))
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package connectedRefsByRole keys=%s", sorted(connected_refs_by_role.keys()))
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package roleTypeByRole=%s", role_type_by_role)
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package heroParticipants=%s", hero_participants)
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package supportingParticipants=%s", supporting_participants)
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package mustAppearRoles=%s", must_appear_roles)
    logger.debug(
        "[SCENARIO DIRECTOR OUTPUT] package worldRefs location=%s style=%s props=%s animal_group=%s",
        bool(refs_by_role.get("location") or connected_refs_by_role.get("location")),
        bool(refs_by_role.get("style") or connected_refs_by_role.get("style")),
        bool(refs_by_role.get("props") or connected_refs_by_role.get("props")),
        bool(
            refs_by_role.get("animal")
            or connected_refs_by_role.get("animal")
            or refs_by_role.get("animal_1")
            or connected_refs_by_role.get("animal_1")
            or refs_by_role.get("group")
            or connected_refs_by_role.get("group")
            or refs_by_role.get("group_faces")
            or connected_refs_by_role.get("group_faces")
        ),
    )
    return {
        "history": history,
        "scenes": scenes,
        "video": video,
        "sound": sound,
        "music": music,
        "knownRoles": known_roles,
        "refsByRole": refs_by_role,
        "connectedRefsByRole": connected_refs_by_role,
        "roleTypeByRole": role_type_by_role,
        "connected_context_summary": connected_context_summary,
        "presentCastRoles": present_cast_roles,
        "presentWorldRoles": present_world_roles,
        "refsPresentByRole": refs_by_role,
        "connectedRefsPresentByRole": connected_refs_by_role,
        "hasLocation": "location" in present_world_roles,
        "hasProps": "props" in present_world_roles,
        "hasStyle": "style" in present_world_roles,
        "heroParticipants": hero_participants,
        "supportingParticipants": supporting_participants,
        "mustAppearRoles": must_appear_roles,
        "context_refs": context_refs,
        "displayLabelByRole": display_label_by_role,
        "refDirectives": ref_directives,
        "contentTypePolicy": content_type_policy,
        "debugRoleContract": {
            "knownRoles": known_roles,
            "roleTypeByRole": role_type_by_role,
            "roleTypeSourceByRole": role_type_source,
            "refsByRoleKeys": sorted(refs_by_role.keys()),
            "connectedRefsByRoleKeys": sorted(connected_refs_by_role.keys()),
            "refsMergeSourceFlags": refs_merge_flags,
            "hasLocationRef": bool(refs_by_role.get("location") or connected_refs_by_role.get("location")),
            "hasStyleRef": bool(refs_by_role.get("style") or connected_refs_by_role.get("style")),
            "hasPropsRef": bool(refs_by_role.get("props") or connected_refs_by_role.get("props")),
            "hasAnimalGroupRef": bool(
                refs_by_role.get("animal")
                or connected_refs_by_role.get("animal")
                or refs_by_role.get("animal_1")
                or connected_refs_by_role.get("animal_1")
                or refs_by_role.get("group")
                or connected_refs_by_role.get("group")
                or refs_by_role.get("group_faces")
                or connected_refs_by_role.get("group_faces")
            ),
            "presentCastRoles": present_cast_roles,
            "presentWorldRoles": present_world_roles,
            "contentTypePolicy": content_type_policy,
        },
    }


def _build_brain_package(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    known_roles = _collect_known_roles(payload, storyboard_out.scenes)
    entities = list(known_roles)
    content_type_policy = _get_content_type_policy(payload)
    content_type = content_type_policy.get("value") or "story"
    global_music_prompt = _resolve_effective_global_music_prompt(payload, storyboard_out.music_prompt)
    return {
        "contentType": content_type,
        "contentTypeLabel": content_type_policy.get("label") or content_type,
        "styleProfile": controls.get("styleProfile") or "realistic",
        "styleLabel": controls.get("styleProfile") or "realistic",
        "sourceMode": str(source.get("source_mode") or "audio").upper(),
        "sourceOrigin": str(source.get("source_origin") or payload.get("sourceOrigin") or "connected"),
        "sourceLabel": source.get("source_mode") or "audio",
        "sourcePreview": source.get("source_preview") or source.get("source_value") or "",
        "connectedContext": summary,
        "entities": entities,
        "sceneLogic": [scene.scene_goal or scene.frame_description or scene.action_in_frame for scene in storyboard_out.scenes],
        "audioStrategy": storyboard_out.voice_script or global_music_prompt,
        "directorNote": controls.get("directorNote") or "",
        "contentTypePolicy": content_type_policy,
    }


def _estimate_narrative_bias(
    payload: dict[str, Any],
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    audio_connected: bool,
    prefer_audio_over_text: bool,
) -> tuple[str, float, float, list[str]]:
    warnings: list[str] = []
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    director_note = str(controls.get("directorNote") or "").strip()
    text_hint_present = bool(director_note)
    aggregate_story = " ".join(
        [
            storyboard_out.story_summary,
            storyboard_out.full_scenario,
            storyboard_out.director_summary,
            " ".join(scene.scene_goal for scene in storyboard_out.scenes),
            " ".join(scene.action_in_frame for scene in storyboard_out.scenes),
        ]
    )
    text_overlap = _estimate_text_overlap(aggregate_story, director_note) if text_hint_present else 0.0
    audio_influence = 0.75 if audio_connected else 0.3
    if prefer_audio_over_text and audio_connected:
        audio_influence = 0.9
    text_influence = min(1.0, 0.2 + text_overlap) if text_hint_present else 0.0
    if audio_connected and prefer_audio_over_text and text_overlap >= 0.6:
        warnings.append("scenario_may_be_text_led_not_audio_led")
    if audio_influence - text_influence >= 0.2:
        return "audio", text_influence, audio_influence, warnings
    if text_influence - audio_influence >= 0.2:
        return "text", text_influence, audio_influence, warnings
    return "mixed", text_influence, audio_influence, warnings


def _apply_scene_count_limit(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if len(storyboard_out.scenes) > 20:
        storyboard_out.scenes = storyboard_out.scenes[:20]
    return storyboard_out


def _scene_text_bundle(scene: ScenarioDirectorScene) -> str:
    return " | ".join(
        part
        for part in [
            scene.scene_goal,
            scene.frame_description,
            scene.action_in_frame,
            scene.camera,
            scene.image_prompt,
            scene.video_prompt,
            scene.ltx_reason,
        ]
        if str(part or "").strip()
    ).strip()


def _count_scene_signal_hits(bundle: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in bundle)


def _scene_specificity_score(scene: ScenarioDirectorScene) -> int:
    score = 0
    bundle = _scene_text_bundle(scene).lower()
    object_markers = (
        "alarm key", "hatch", "beam", "door", "window", "mirror", "monitor", "console", "switch", "helmet",
        "hand", "face", "dust", "light", "shadow", "blood", "machine", "siren", "corridor", "stair",
    )
    action_markers = (
        "freezes", "opens", "shuts", "turns", "presses", "reaches", "stares", "flinches", "steps", "grips",
        "pulls", "reveals", "cuts out", "pulses", "cracks", "ignites", "crosses", "locks", "unlocks",
    )
    camera_markers = (
        "close-up", "locked frontal", "wide", "overhead", "profile", "insert", "tracking", "dolly", "push-in",
        "static", "macro", "two-shot", "over-the-shoulder", "silhouette",
    )
    sensory_markers = (
        "red", "blue", "neon", "steam", "dust", "beam", "glow", "hum", "siren", "pulse", "echo", "flicker",
    )
    if len(bundle) >= 70:
        score += 1
    if len(scene.frame_description.split()) >= 4:
        score += 1
    if len(scene.action_in_frame.split()) >= 2:
        score += 1
    if len(scene.camera.split()) >= 2:
        score += 1
    if scene.location or scene.props:
        score += 1
    if _count_scene_signal_hits(bundle, object_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, action_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, camera_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, sensory_markers) >= 1:
        score += 1
    return score


def _scene_weak_assessment(scene: ScenarioDirectorScene) -> tuple[bool, str]:
    bundle = _scene_text_bundle(scene).lower()
    specificity = _scene_specificity_score(scene)
    directing_fields = sum(
        1
        for field in (scene.frame_description, scene.action_in_frame, scene.camera)
        if str(field or "").strip()
    )
    if any(pattern in bundle for pattern in WEAK_SCENE_PATTERNS):
        return True, "generic"
    if directing_fields == 0:
        return True, "missing_directing"
    if specificity >= 4:
        return False, "short_but_specific" if len(bundle) < 80 else "specific"
    if len(bundle) < 28 and specificity < 2:
        return True, "too_thin"
    if directing_fields < 2 and specificity < 3:
        return True, "missing_directing"
    if len(bundle) < 55 and specificity < 3:
        return True, "vague"
    if specificity < 2:
        return True, "vague"
    return False, "specific"


def _is_scene_weak(scene: ScenarioDirectorScene) -> bool:
    weak, _ = _scene_weak_assessment(scene)
    return weak


def _infer_scene_purpose(scene: ScenarioDirectorScene) -> str:
    bundle = _scene_text_bundle(scene).lower()
    if scene.continuation_from_previous:
        return "transition"
    if any(token in bundle for token in ("final", "last image", "aftertaste", "hold", "lingers", "lingering")):
        return "final image / ending hold"
    if any(token in bundle for token in ("climax", "breaks", "collapse", "scream", "impact", "erupts", "peak")):
        return "emotional climax"
    if any(token in bundle for token in ("reveal", "door opens", "discovers", "unveils", "transformation", "new space")):
        return "reveal"
    if any(token in bundle for token in ("confronts", "faces", "standoff", "conflict")):
        return "confrontation"
    if any(token in bundle for token in ("intrudes", "wrong", "glitch", "alarm", "destabil")):
        return "destabilization"
    if any(token in bundle for token in ("arrives", "enters", "descends", "steps into")):
        return "entry"
    if any(token in bundle for token in ("wide tableau", "iconic image", "silhouette", "burning frame")):
        return "peak image"
    if any(token in bundle for token in ("escalates", "rushes", "tightens", "rises", "closing in")):
        return "escalation"
    return "hook" if scene.time_start <= 0.1 else "transition"


def _repair_missing_scene_goal(scene: ScenarioDirectorScene) -> ScenarioDirectorScene:
    goal = str(scene.scene_goal or "").strip().lower()
    if goal and goal not in GENERIC_SCENE_GOALS:
        return scene
    scene.scene_goal = _infer_scene_purpose(scene)
    return scene


def _filter_or_repair_weak_scenes(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not storyboard_out.scenes:
        return storyboard_out
    kept: list[ScenarioDirectorScene] = []
    weak_count = 0
    allow_repair_only = len(storyboard_out.scenes) <= 3
    for scene in storyboard_out.scenes:
        scene = _repair_missing_scene_goal(scene)
        weak, reason = _scene_weak_assessment(scene)
        if not weak:
            if reason == "short_but_specific":
                logger.debug("[SCENARIO_DIRECTOR] weak scene kept short_but_specific scene_id=%s", scene.scene_id)
            kept.append(scene)
            continue
        weak_count += 1
        logger.debug("[SCENARIO_DIRECTOR] weak scene detected scene_id=%s reason=%s", scene.scene_id, reason)
        if allow_repair_only or len(storyboard_out.scenes) - weak_count < 1:
            if not scene.scene_goal or scene.scene_goal.lower() in GENERIC_SCENE_GOALS:
                scene.scene_goal = _infer_scene_purpose(scene)
            kept.append(scene)
            continue
        if _scene_specificity_score(scene) >= 4:
            kept.append(scene)
    if not kept:
        raise ScenarioDirectorError(
            "scenario_director_empty_after_filter",
            "Scenario Director filtered out all scenes after quality checks.",
            status_code=502,
        )
    if weak_count:
        logger.debug("[SCENARIO_DIRECTOR] weak scenes filtered count=%s", max(0, len(storyboard_out.scenes) - len(kept)))
    storyboard_out.scenes = kept
    return storyboard_out


def _normalize_scene_timeline(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    previous_end = 0.0
    for scene in storyboard_out.scenes:
        original_end = _safe_float(scene.time_end, scene.time_start)
        original_duration = _safe_float(scene.duration, max(0.0, original_end - scene.time_start))
        scene.time_start = max(_safe_float(scene.time_start, previous_end), previous_end)
        if original_end < scene.time_start:
            original_end = round(scene.time_start + max(0.0, original_duration), 3)
        scene.time_end = max(original_end, scene.time_start)
        scene.duration = round(max(0.0, scene.time_end - scene.time_start), 3)
        previous_end = scene.time_end
    return storyboard_out


def _absorb_clip_leading_intro_gap(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    if not scenes:
        return storyboard_out
    first = scenes[0]
    original_start = _safe_float(first.time_start, 0.0)
    if original_start <= 0.0 or original_start > CLIP_LEADING_INTRO_GAP_ABSORB_MAX_SEC:
        return storyboard_out
    first_end = max(original_start, _safe_float(first.time_end, original_start))
    first.time_start = 0.0
    first.time_end = round(max(first_end, 0.0), 3)
    first.duration = round(max(0.0, first.time_end - first.time_start), 3)
    first.requested_duration_sec = round(max(_safe_float(first.requested_duration_sec, 0.0), first.duration), 3)
    first.audio_slice_start_sec = 0.0
    first.audio_slice_end_sec = round(max(_safe_float(first.audio_slice_end_sec, first.time_end), first.time_end), 3)
    first.audio_slice_expected_duration_sec = round(max(0.0, first.audio_slice_end_sec - first.audio_slice_start_sec), 3)
    logger.info(
        "[SCENARIO_DIRECTOR] absorbed leading intro gap into first scene scene_id=%s original_start=%.3f new_start=0.000",
        first.scene_id,
        original_start,
    )
    return storyboard_out


def _limit_lip_sync_usage(
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    content_type_policy: dict[str, Any] | None = None,
) -> ScenarioDirectorStoryboardOut:
    content_policy = content_type_policy or {}
    is_music_video = str(content_policy.get("value") or "").strip().lower() == "music_video"
    duration = max((scene.time_end for scene in storyboard_out.scenes), default=0.0)
    lip_sync_cap = 2 if is_music_video and 25.0 <= duration <= 35.0 else 3
    lip_sync_seen = 0
    for scene in storyboard_out.scenes:
        if scene.ltx_mode not in {"lip_sync", "lip_sync_music"}:
            continue
        lip_sync_seen += 1
        if lip_sync_seen <= lip_sync_cap:
            continue
        scene.render_mode = "image_video"
        scene.resolved_workflow_key, scene.resolved_workflow_file = _resolve_workflow_key_and_file(
            str(content_policy.get("clipWorkflowDefault") or "i2v"),
            fallback_key="i2v",
        )
        scene.ltx_mode = "i2v"
        scene.lip_sync = False
        scene.send_audio_to_generator = False
        scene.lip_sync_text = ""
        scene.audio_slice_start_sec = 0.0
        scene.audio_slice_end_sec = 0.0
        scene.audio_slice_expected_duration_sec = 0.0
        scene.audio_slice_decision_reason = "Audio slice disabled after lip-sync limit downgrade."
        replacement_reason = "Lip-sync quota reached; downgraded to base i2v workflow."
        scene.workflow_decision_reason = replacement_reason
        scene.lip_sync_decision_reason = f"Lip-sync disabled because Scenario Director allows at most {lip_sync_cap} lip_sync_music scenes for this clip."
        scene.ltx_reason = _normalize_ltx_reason(replacement_reason, scene.ltx_mode, narration_mode=scene.narration_mode)
    return storyboard_out


def _infer_music_video_shot_type(scene: ScenarioDirectorScene) -> str:
    explicit_framing = str(scene.performance_framing or "").strip().lower()
    if explicit_framing in {"wide_action", "wide_performance"}:
        return "wide"
    if explicit_framing in {"full_body_action", "three_quarter"}:
        return "medium"
    if explicit_framing in {"close_emotional", "face_close", "close_performance", "medium_close"}:
        return "close_up"
    if explicit_framing in {"tight_medium", "medium_performance"}:
        return "medium"
    if explicit_framing in {"duet_frame", "asymmetric_duet"}:
        return "duet_shared"
    bundle = " ".join([scene.camera, scene.frame_description, scene.action_in_frame]).lower()
    if any(token in bundle for token in ("two-shot", "two shot", "duet", "shared frame", "both in frame", "side by side")):
        return "duet_shared"
    if any(token in bundle for token in ("extreme close", "ecu", "macro", "insert", "detail")):
        return "detail_insert"
    if any(token in bundle for token in ("close-up", "close up", "portrait", "face", "frontal", "tight")):
        return "close_up"
    if any(token in bundle for token in ("medium close", "medium shot", "waist-up", "waist up")):
        return "medium"
    if any(token in bundle for token in ("wide", "establishing", "drone", "aerial", "long shot", "vast")):
        return "wide"
    return "medium"


def _normalize_scene_shot_type_from_camera(scene: ScenarioDirectorScene) -> str:
    explicit_framing = str(scene.performance_framing or "").strip().lower()
    if explicit_framing in {"wide_action", "wide_performance"}:
        return "wide"
    if explicit_framing in {"full_body_action", "three_quarter", "tight_medium", "medium_performance"}:
        return "medium"
    if explicit_framing in {"close_emotional", "face_close", "close_performance", "medium_close"}:
        return "close_up"
    if explicit_framing in {"duet_frame", "asymmetric_duet"}:
        return "duet_shared"
    shot_type = str(scene.shot_type or "").strip().lower() or "medium"
    camera_text = " ".join(
        [
            str(scene.camera or "").strip().lower(),
            str(scene.frame_description or "").strip().lower(),
        ]
    )
    if not camera_text:
        return shot_type
    has_tight_close = any(token in camera_text for token in ("tight close-up", "tight close up", "extreme close-up", "extreme close up", "ecu"))
    has_close = any(token in camera_text for token in ("close-up", "close up", "close shot", "face close", "faces close")) or has_tight_close
    has_medium_close = any(token in camera_text for token in ("medium close-up", "medium close up", "medium shot", "waist-up", "waist up"))
    has_wide = any(token in camera_text for token in ("wide", "full shot", "full body", "long shot", "distant", "from afar", "establishing"))
    if has_tight_close and shot_type == "wide":
        return "close_up"
    if has_close and shot_type == "wide":
        return "close_up"
    if has_medium_close and shot_type in {"wide", "detail_insert"}:
        return "medium"
    if has_wide and shot_type in {"close_up", "detail_insert"}:
        return "medium"
    return shot_type


def _should_keep_first_last_for_scene(
    scene: ScenarioDirectorScene,
    *,
    transition_candidate: bool,
    forced_transition_scene: bool,
) -> tuple[bool, str]:
    if forced_transition_scene:
        return True, "forced_transition_scene"
    if not transition_candidate:
        return True, "not_transition_candidate"
    duet_signal = (
        len([str(actor).strip() for actor in (scene.actors or []) if str(actor).strip()]) >= 2
        or str(scene.scene_role_dynamics or "").strip().lower().startswith("duet")
        or str(scene.shot_type or "").strip().lower() == "duet_shared"
    )
    if not duet_signal:
        return True, "non_duet_scene"
    semantic_bundle = " ".join(
        [
            str(scene.scene_goal or ""),
            str(scene.local_phrase or ""),
            str(scene.what_from_audio_this_scene_uses or ""),
            str(scene.frame_description or ""),
            str(scene.action_in_frame or ""),
            str(scene.boundary_reason or ""),
            str(scene.transition_type or ""),
            str(scene.camera or ""),
        ]
    ).strip().lower()
    strong_visual_transition_tokens = (
        "approach",
        "lean in",
        "whisper",
        "reveal",
        "shift",
        "transition",
        "change of distance",
        "change of intimacy",
        "before-after",
        "before after",
        "move closer",
        "step closer",
        "push in",
        "pull back",
    )
    if any(token in semantic_bundle for token in strong_visual_transition_tokens):
        return True, "strong_visual_transition_signal"
    reiteration_tokens = (
        "reaffirm",
        "restatement",
        "restate",
        "repeated refusal",
        "refusal",
        "refuse",
        "won't disclose",
        "will not disclose",
        "won't tell",
        "will not tell",
        "repeat",
        "reiterat",
        "emphasis",
        "insist",
    )
    if any(token in semantic_bundle for token in reiteration_tokens):
        return False, "reaffirmation_without_visual_shift"
    return True, "default_keep_first_last"


def _is_repeat_heavy_music_clip(scenes: list[ScenarioDirectorScene]) -> bool:
    if len(scenes) < 3:
        return False
    normalized_phrases: list[str] = []
    repeated_phrase_scenes = 0
    for scene in scenes:
        phrase = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(scene.local_phrase or "").lower())).strip()
        if phrase:
            normalized_phrases.append(phrase)
            phrase_parts = [part.strip() for part in re.split(r"(?:\s*[|/·]\s*|\n+|(?<=[\.\!\?;:])\s+)", phrase) if part.strip()]
            if len(set(phrase_parts)) < len(phrase_parts):
                repeated_phrase_scenes += 1
    if not normalized_phrases:
        return False
    duplicate_hits = len(normalized_phrases) - len(set(normalized_phrases))
    multi_phrase_scenes = sum(1 for scene in scenes if int(_safe_float(getattr(scene, "scene_phrase_count", 0), 0)) >= 2)
    return duplicate_hits >= 1 or repeated_phrase_scenes >= 1 or multi_phrase_scenes >= 2


def _collect_active_connected_character_roles(payload: dict[str, Any]) -> list[str]:
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    refs_present = connected_summary.get("refsPresentByRole") if isinstance(connected_summary.get("refsPresentByRole"), dict) else {}
    if not refs_present and isinstance(payload.get("context_refs"), dict):
        refs_present = {
            role: (value.get("refs") if isinstance(value, dict) else [])
            for role, value in payload.get("context_refs", {}).items()
        }
    active = []
    for role in ("character_1", "character_2", "character_3"):
        refs = refs_present.get(role) if isinstance(refs_present, dict) else []
        if isinstance(refs, list) and any(str(item).strip() for item in refs):
            active.append(role)
    return active


def _remove_single_character_summary_duet_phrases(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    cleaned = normalized
    duet_patterns = [
        r"\bher friend\b",
        r"\btwo young women\b",
        r"\btwo girls\b",
        r"\bboth of them\b",
        r"\btogether\b",
    ]
    for pattern in duet_patterns:
        cleaned = re.sub(pattern, "the performer", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _maybe_compact_repeat_heavy_ending_hold(
    scene: ScenarioDirectorScene,
    *,
    repeat_heavy_clip: bool,
) -> tuple[bool, str]:
    if not repeat_heavy_clip:
        return False, "not_repeat_heavy_clip"
    if str(scene.scene_purpose or "").strip().lower() != "ending_hold":
        return False, "not_ending_hold"
    duration = max(0.0, _safe_float(scene.duration, _safe_float(scene.time_end, 0.0) - _safe_float(scene.time_start, 0.0)))
    if duration <= 5.0:
        return False, "already_compact"
    complexity_bundle = " ".join(
        [
            str(scene.scene_goal or ""),
            str(scene.frame_description or ""),
            str(scene.action_in_frame or ""),
            str(scene.camera or ""),
            str(scene.transition_type or ""),
        ]
    ).strip().lower()
    if any(token in complexity_bundle for token in ("before-after", "state shift", "state_shift", "transition", "approach", "reveal", "enter", "exit", "transform")):
        return False, "complex_evolution_present"
    preferred_target = 4.8
    scene.requested_duration_sec = round(min(_safe_float(scene.requested_duration_sec, duration), preferred_target), 3)
    slice_start = _safe_float(scene.audio_slice_start_sec, _safe_float(scene.time_start, 0.0))
    current_slice_end = _safe_float(scene.audio_slice_end_sec, max(slice_start, _safe_float(scene.time_end, slice_start)))
    compact_slice_end = min(current_slice_end, slice_start + preferred_target)
    scene.audio_slice_start_sec = round(slice_start, 3)
    scene.audio_slice_end_sec = round(max(slice_start, compact_slice_end), 3)
    scene.audio_slice_expected_duration_sec = round(max(0.0, scene.audio_slice_end_sec - scene.audio_slice_start_sec), 3)
    return True, "repeat_heavy_ending_hold_compacted"


def _infer_music_video_scene_purpose(
    index: int,
    total: int,
    scene: ScenarioDirectorScene,
    *,
    transition_candidate: bool = False,
    performance_framing: str = "",
    performer_presentation: str = "unknown",
) -> str:
    continuation = _coerce_bool(scene.continuation_from_previous, False) or scene.ltx_mode == "continuation"
    render_mode = str(scene.render_mode or "").strip().lower()
    framing = str(performance_framing or "").strip().lower()
    has_human_cast = _scene_has_human_performer(scene) or performer_presentation in {"male", "female", "mixed"}
    if index == 0:
        return "hook"
    if index == 1 and framing in MUSIC_VIDEO_PERFORMANCE_FRAMINGS and has_human_cast:
        return "performance"
    if scene.needs_two_frames or render_mode in {"first_last", "first_last_sound"}:
        return "transition"
    if transition_candidate or continuation:
        return "transition"
    if index == max(0, total - 2):
        return "payoff"
    if index == total - 1:
        return "ending_hold"
    if framing in MUSIC_VIDEO_PERFORMANCE_FRAMINGS and has_human_cast:
        return "performance"
    return "build"


def _scene_has_human_performer(scene: ScenarioDirectorScene) -> bool:
    return any(actor.startswith("character_") or actor in {"group_faces", "group"} for actor in (scene.actors or []))


def _scene_has_lip_sync_signal(scene: ScenarioDirectorScene) -> bool:
    text = " ".join(
        [
            scene.local_phrase or "",
            scene.what_from_audio_this_scene_uses or "",
            scene.scene_goal or "",
            scene.action_in_frame or "",
            scene.frame_description or "",
        ]
    ).lower()
    lip_sync_signal_patterns = (
        r"\blip[\s\-]?sync(?:ing)?\b",
        r"\blyric(?:s|al)?\b",
        r"\bvocal(?:s| delivery| performance)?\b",
        r"\bchorus\b",
        r"\bverse\b",
        r"\bsing(?:s|ing|er)?\b",
        r"\bsinging to camera\b",
        r"\bvisible lyric delivery\b",
    )
    return any(re.search(pattern, text) for pattern in lip_sync_signal_patterns)


def _scene_text_blob(scene: ScenarioDirectorScene) -> str:
    return " ".join(
        [
            str(scene.scene_goal or ""),
            str(scene.frame_description or ""),
            str(scene.action_in_frame or ""),
            str(scene.camera or ""),
            str(scene.local_phrase or ""),
            str(scene.what_from_audio_this_scene_uses or ""),
            str(scene.story_function or ""),
            str(scene.scene_purpose or ""),
        ]
    ).strip().lower()


def _scene_has_final_line_performance_intent(scene: ScenarioDirectorScene) -> bool:
    text = _scene_text_blob(scene)
    has_final_line = any(marker in text for marker in FINAL_LINE_MARKERS)
    has_direct_perf = any(marker in text for marker in DIRECT_PERFORMANCE_MARKERS)
    return has_final_line and has_direct_perf


def _scene_has_ending_intent(scene: ScenarioDirectorScene) -> bool:
    purpose = str(scene.scene_purpose or "").strip().lower()
    story_function = str(scene.story_function or "").strip().lower()
    clip_arc_stage = str(scene.clip_arc_stage or "").strip().lower()
    combined = " ".join([purpose, story_function, clip_arc_stage, _scene_text_blob(scene)])
    if purpose in {"ending_hold", "payoff"}:
        return True
    return any(token in combined for token in ("ending", "outro", "resolution", "release", "afterimage", "final beat"))


def _build_afterimage_text(scene: ScenarioDirectorScene) -> tuple[str, str, str, str]:
    emotion = str(scene.emotion or "").strip() or "quiet emotional residue"
    scene_goal = "Holding the meaning after the final line with a lingering gaze, final breath, and emotional release."
    frame_description = (
        "No active singing; the performer stays present in a gentle ending hold, carrying emotional residue after the vocal climax."
    )
    action_in_frame = (
        f"She keeps eye contact briefly, breath settles, shoulders soften, and the feeling lingers without lyric articulation ({emotion})."
    )
    camera = "Slow subtle pull-back and hold to preserve afterimage and continuation feeling."
    return scene_goal, frame_description, action_in_frame, camera


def _split_sentence_units(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return []
    return [part.strip(" -–—") for part in re.split(r"(?:[\n\r]+|(?<=[\.\!\?])\s+)", cleaned) if part.strip(" -–—")]


def _split_clause_units(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return []
    return [
        part.strip(" -–—,")
        for part in re.split(r"(?:\s*[;:]\s+|\s+\b(?:then|and then|as music fades|ends with)\b\s+)", cleaned, flags=re.IGNORECASE)
        if part.strip(" -–—,")
    ]


def _collect_scene_boundary_candidates(scene: ScenarioDirectorScene) -> tuple[list[str], list[str]]:
    sentence_candidates: list[str] = []
    clause_candidates: list[str] = []
    for raw in (
        scene.scene_goal,
        scene.frame_description,
        scene.action_in_frame,
        scene.local_phrase or "",
        scene.what_from_audio_this_scene_uses,
    ):
        for sentence in _split_sentence_units(raw):
            if sentence not in sentence_candidates:
                sentence_candidates.append(sentence)
        for clause in _split_clause_units(raw):
            if clause not in clause_candidates:
                clause_candidates.append(clause)
    return sentence_candidates, clause_candidates


def _scene_has_multi_phase_action(scene: ScenarioDirectorScene, sentence_candidates: list[str], clause_candidates: list[str]) -> bool:
    if len(sentence_candidates) >= 2 or len(clause_candidates) >= 3:
        return True
    text = _scene_text_blob(scene)
    phase_markers = (" then ", " and then ", " as music fades", " ends with ", " finally ", " reveal ", " pull back ")
    return sum(1 for marker in phase_markers if marker in text) >= 2


def _pick_text_aware_split_point(start: float, end: float, sentence_candidates: list[str], clause_candidates: list[str]) -> float:
    duration = max(0.0, end - start)
    if duration <= 0.0:
        return start
    units = clause_candidates if len(clause_candidates) >= 2 else sentence_candidates
    if len(units) < 2:
        return round(start + duration * 0.58, 3)
    first = units[0]
    total_chars = sum(max(1, len(item)) for item in units)
    first_ratio = max(0.35, min(0.7, max(1, len(first)) / max(1, total_chars)))
    return round(start + duration * first_ratio, 3)


def _maybe_split_final_hybrid_outro_scene(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    diagnostics = storyboard_out.diagnostics
    clip_duration = max([_safe_float(scene.time_end, 0.0) for scene in scenes], default=0.0)
    short_clip = 20.0 <= clip_duration <= 40.0
    oversized_threshold = 5.5 if short_clip else 6.0
    diagnostics.oversizedScenesDetected = [
        str(scene.scene_id or "").strip()
        for scene in scenes
        if max(0.0, _safe_float(scene.duration, _safe_float(scene.time_end, 0.0) - _safe_float(scene.time_start, 0.0))) > oversized_threshold
    ]
    diagnostics.oversizedScenesSplitCount = 0
    diagnostics.final_scene_split_applied = False
    diagnostics.final_scene_split_reason = "not_evaluated"
    diagnostics.final_scene_split_source_scene_id = ""
    diagnostics.final_scene_split_created_ids = []
    diagnostics.final_scene_split_strategy = ""
    diagnostics.finalSceneOversizeDetected = False
    diagnostics.finalSceneSplitConsidered = False
    diagnostics.sentenceBoundaryCandidates = []
    diagnostics.clauseBoundaryCandidates = []
    diagnostics.segmentationRepairSource = ""
    if not scenes:
        diagnostics.final_scene_split_reason = "no_scenes"
        return storyboard_out
    last_scene = scenes[-1]
    diagnostics.final_scene_split_source_scene_id = str(last_scene.scene_id or "").strip()
    duration = max(0.0, _safe_float(last_scene.duration, _safe_float(last_scene.time_end, 0.0) - _safe_float(last_scene.time_start, 0.0)))
    route = str(last_scene.video_generation_route or last_scene.render_mode or last_scene.resolved_workflow_key or "").strip().lower()
    is_non_lip_route = route in {"i2v", "f_l", "image_video", "first_last", "downgraded_to_i2v", "blocked"}
    has_final_performance = _scene_has_final_line_performance_intent(last_scene)
    has_ending_intent = _scene_has_ending_intent(last_scene)
    ending_purpose = str(last_scene.scene_purpose or "").strip().lower()
    ending_purpose_hint = ending_purpose in {"ending", "ending_hold", "outro_resolution"}
    sentence_candidates, clause_candidates = _collect_scene_boundary_candidates(last_scene)
    multi_phase_action = _scene_has_multi_phase_action(last_scene, sentence_candidates, clause_candidates)
    long_duration = duration > oversized_threshold
    diagnostics.finalSceneOversizeDetected = bool(long_duration)
    diagnostics.sentenceBoundaryCandidates = sentence_candidates
    diagnostics.clauseBoundaryCandidates = clause_candidates
    diagnostics.finalSceneSplitConsidered = bool(
        is_non_lip_route and (
            long_duration
            or ending_purpose_hint
            or len(sentence_candidates) >= 2
            or len(clause_candidates) >= 2
        )
    )
    if not diagnostics.finalSceneSplitConsidered:
        diagnostics.final_scene_split_reason = "split_not_considered_scene_not_suspicious"
        return storyboard_out
    if not is_non_lip_route:
        diagnostics.final_scene_split_reason = "route_not_non_lip"
        return storyboard_out
    if not (has_ending_intent or ending_purpose_hint):
        diagnostics.final_scene_split_reason = "missing_ending_intent"
        return storyboard_out
    if not long_duration and not multi_phase_action:
        diagnostics.final_scene_split_reason = "duration_not_long_enough"
        return storyboard_out
    if long_duration and not multi_phase_action and len(sentence_candidates) < 2 and len(clause_candidates) < 2:
        diagnostics.final_scene_split_reason = "scene_unsplittable_single_phase"
        return storyboard_out

    existing_ids = {str(scene.scene_id or "").strip() for scene in scenes}
    base_id = str(last_scene.scene_id or "S").strip() or "S"

    def _unique_split_id(suffix: str) -> str:
        candidate = f"{base_id}_{suffix}"
        if candidate not in existing_ids:
            existing_ids.add(candidate)
            return candidate
        idx = 2
        while f"{candidate}_{idx}" in existing_ids:
            idx += 1
        unique = f"{candidate}_{idx}"
        existing_ids.add(unique)
        return unique

    lip_scene_id = _unique_split_id("A")
    hold_scene_id = _unique_split_id("B")
    start = _safe_float(last_scene.time_start, 0.0)
    end = max(start, _safe_float(last_scene.time_end, start))
    split_time = _pick_text_aware_split_point(start, end, sentence_candidates, clause_candidates)
    min_part = 1.7 if short_clip else 1.5
    lip_end = round(min(max(split_time, start + min_part), end - min_part), 3)
    if lip_end <= start + min_part or lip_end >= end - min_part:
        diagnostics.final_scene_split_reason = "split_window_too_narrow"
        return storyboard_out

    lip_data = last_scene.model_dump(mode="python")
    hold_data = last_scene.model_dump(mode="python")
    lip_data["scene_id"] = lip_scene_id
    hold_data["scene_id"] = hold_scene_id
    base_display_index = int(_safe_float(last_scene.display_index, len(scenes)))
    if base_display_index <= 0:
        base_display_index = len(scenes) if len(scenes) > 0 else 1
    lip_data["display_index"] = base_display_index
    hold_data["display_index"] = base_display_index + 1
    lip_data["time_start"] = round(start, 3)
    lip_data["time_end"] = round(lip_end, 3)
    lip_data["duration"] = round(max(0.0, lip_end - start), 3)
    lip_data["requested_duration_sec"] = lip_data["duration"]
    hold_data["time_start"] = round(lip_end, 3)
    hold_data["time_end"] = round(end, 3)
    hold_data["duration"] = round(max(0.0, end - lip_end), 3)
    hold_data["requested_duration_sec"] = hold_data["duration"]

    should_upgrade_first_half_to_lipsync = bool(has_final_performance and has_ending_intent)
    lip_data["scene_purpose"] = "payoff"
    lip_data["story_function"] = "ending_progression_beat"
    lip_data["clip_arc_stage"] = "power_return"
    lip_data["performance_phase"] = "ending_progression"
    lip_data["video_generation_route"] = "lip_sync_music" if should_upgrade_first_half_to_lipsync else "i2v"
    lip_data["planned_video_generation_route"] = "lip_sync_music" if should_upgrade_first_half_to_lipsync else "i2v"
    lip_data["resolved_workflow_key"] = "lip_sync_music" if should_upgrade_first_half_to_lipsync else "i2v"
    lip_data["resolved_workflow_file"] = CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY["lip_sync_music" if should_upgrade_first_half_to_lipsync else "i2v"]
    lip_data["render_mode"] = "lip_sync_music" if should_upgrade_first_half_to_lipsync else "image_video"
    lip_data["ltx_mode"] = "lip_sync_music" if should_upgrade_first_half_to_lipsync else "i2v"
    lip_data["lip_sync"] = should_upgrade_first_half_to_lipsync
    lip_data["send_audio_to_generator"] = should_upgrade_first_half_to_lipsync
    lip_data["music_vocal_lipsync_allowed"] = should_upgrade_first_half_to_lipsync
    lip_data["audio_slice_kind"] = "music_vocal" if should_upgrade_first_half_to_lipsync else "none"
    lip_data["audio_slice_start_sec"] = lip_data["time_start"]
    lip_data["audio_slice_end_sec"] = lip_data["time_end"]
    lip_data["audio_slice_expected_duration_sec"] = lip_data["duration"]
    lip_data["audio_slice_bounds_filled_from_scene"] = should_upgrade_first_half_to_lipsync
    lip_data["lip_sync_route_state_consistent"] = should_upgrade_first_half_to_lipsync
    if should_upgrade_first_half_to_lipsync and str(lip_data.get("performance_framing") or "").strip().lower() not in LIP_SYNC_PERFORMANCE_FRAMINGS:
        lip_data["performance_framing"] = "tight_medium"
    lip_data["camera"] = _lip_sync_safe_camera_line() if should_upgrade_first_half_to_lipsync else str(lip_data.get("camera") or "").strip()
    source_phrase = str(last_scene.local_phrase or "").strip()
    lip_local_phrase = ""
    if source_phrase:
        phrase_parts = [part.strip() for part in re.split(r"(?:\s*[|/·]\s*|\n+|(?<=[\.\!\?;:])\s+)", source_phrase) if part.strip()]
        hold_markers = ("afterimage", "outro", "hold", "release", "linger", "lingering", "resonance", "final breath", "post-vocal")
        pre_hold_candidates: list[str] = []
        fallback_candidates: list[str] = []
        hold_started = False
        for part in phrase_parts:
            normalized_part = re.sub(r"\s+", " ", part).strip()
            lower_part = normalized_part.lower()
            is_hold_like = any(marker in lower_part for marker in hold_markers)
            if not is_hold_like:
                fallback_candidates.append(normalized_part)
            if is_hold_like:
                hold_started = True
                continue
            if not hold_started:
                pre_hold_candidates.append(normalized_part)
        lip_local_phrase = (pre_hold_candidates[-1] if pre_hold_candidates else (fallback_candidates[-1] if fallback_candidates else "")).strip()
    if not lip_local_phrase:
        lip_local_phrase = "Ending progression beat completed before the final release hold."
    lip_data["local_phrase"] = lip_local_phrase
    lip_data["what_from_audio_this_scene_uses"] = "Completed ending progression beat before final release/afterglow."
    lip_summary = (
        "Final vocal payoff lands in-camera with emotionally precise lyric articulation and performance intensity."
        if should_upgrade_first_half_to_lipsync
        else "Ending progression beat completes as a coherent visual clause before final release."
    )
    lip_motion = (
        "Controlled singer-forward movement, expressive phrase-timed hands, subtle body pulse, and readable mouth articulation."
        if should_upgrade_first_half_to_lipsync
        else "Confident movement progression resolves as a complete action beat before the hold."
    )
    lip_visual_prompt = (
        "Final lip-sync payoff, tight-medium singer framing, eyes/mouth/neck/shoulders readable, direct emotional delivery, no outro hold."
        if should_upgrade_first_half_to_lipsync
        else "Ending progression beat, meaningful motion completion, no abrupt cut mid-gesture, preserve narrative continuity."
    )
    lip_video_prompt = (
        "Performer delivers the true final vocal line to camera with clear articulation and controlled emotional push."
        if should_upgrade_first_half_to_lipsync
        else "Scene completes the ending progression beat in full before transitioning to the final afterglow hold."
    )
    lip_data["scene_goal"] = (
        "Deliver the final vocal payoff directly to camera before release."
        if should_upgrade_first_half_to_lipsync
        else "Complete the ending progression beat before the final release hold."
    )
    lip_data["frame_description"] = lip_summary
    lip_data["action_in_frame"] = lip_video_prompt
    lip_data["summary"] = lip_summary
    lip_data["motion"] = lip_motion
    lip_data["visualPrompt"] = lip_visual_prompt
    lip_data["image_prompt"] = lip_visual_prompt
    lip_data["video_prompt"] = lip_video_prompt
    lip_data["workflow_decision_reason"] = (
        f"{str(last_scene.workflow_decision_reason or '').strip()} Final-scene repair split: sentence/clause complete progression isolated before ending release."
    ).strip()
    lip_data["lip_sync_decision_reason"] = (
        "forced_final_payoff_lipsync_split_applied" if should_upgrade_first_half_to_lipsync else "not_lip_sync_route_but_meaning_split_applied"
    )

    hold_goal, hold_frame, hold_action, hold_camera = _build_afterimage_text(last_scene)
    hold_data["scene_purpose"] = "ending_hold"
    hold_data["story_function"] = "ending_hold_afterimage"
    hold_data["clip_arc_stage"] = "afterimage_release"
    hold_data["performance_phase"] = "afterimage_release"
    hold_data["video_generation_route"] = "i2v"
    hold_data["planned_video_generation_route"] = "i2v"
    hold_data["resolved_workflow_key"] = "i2v"
    hold_data["resolved_workflow_file"] = CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY["i2v"]
    hold_data["render_mode"] = "image_video"
    hold_data["ltx_mode"] = "i2v"
    hold_data["lip_sync"] = False
    hold_data["lip_sync_text"] = ""
    hold_data["send_audio_to_generator"] = False
    hold_data["music_vocal_lipsync_allowed"] = False
    hold_data["audio_slice_kind"] = "none"
    hold_data["audio_slice_start_sec"] = hold_data["time_start"]
    hold_data["audio_slice_end_sec"] = hold_data["time_end"]
    hold_data["audio_slice_expected_duration_sec"] = hold_data["duration"]
    hold_data["audio_slice_bounds_filled_from_scene"] = False
    hold_data["lip_sync_route_state_consistent"] = True
    hold_summary = hold_frame
    hold_motion = "Lingering gaze, final breath, softened shoulders, minimal post-vocal micro-movement, and slow pull-back hold."
    hold_visual_prompt = (
        "Ending afterimage hold, no active singing, emotional residue, lingering gaze, final breath, gentle slow pull-back."
    )
    hold_video_prompt = hold_action
    hold_data["scene_goal"] = hold_goal
    hold_data["frame_description"] = hold_summary
    hold_data["action_in_frame"] = hold_video_prompt
    hold_data["camera"] = hold_camera
    hold_data["summary"] = hold_summary
    hold_data["motion"] = hold_motion
    hold_data["visualPrompt"] = hold_visual_prompt
    hold_data["image_prompt"] = hold_visual_prompt
    hold_data["video_prompt"] = hold_video_prompt
    hold_data["local_phrase"] = None
    hold_data["what_from_audio_this_scene_uses"] = "Final release/pose/afterglow beat after ending progression, without cutting mid-thought."
    hold_data["workflow_decision_reason"] = (
        f"{str(last_scene.workflow_decision_reason or '').strip()} Final-scene repair split: ending hold isolated as complete release/afterglow beat."
    ).strip()
    hold_data["lip_sync_decision_reason"] = "non_lip_afterimage_hold"

    updated_scenes = scenes[:-1]
    updated_scenes.append(ScenarioDirectorScene.model_validate(lip_data))
    updated_scenes.append(ScenarioDirectorScene.model_validate(hold_data))
    storyboard_out.scenes = updated_scenes
    diagnostics.final_scene_split_applied = True
    diagnostics.final_scene_split_reason = (
        "final_non_lip_hybrid_outro_scene_split_with_lipsync_upgrade"
        if should_upgrade_first_half_to_lipsync
        else "final_non_lip_outro_scene_split_sentence_clause_repair"
    )
    diagnostics.final_scene_split_created_ids = [lip_scene_id, hold_scene_id]
    diagnostics.final_scene_split_strategy = (
        "final_lipsync_plus_afterimage" if should_upgrade_first_half_to_lipsync else "ending_progression_plus_afterglow"
    )
    diagnostics.segmentationRepairSource = "final_scene_repair"
    diagnostics.clip_formula_rebalance_applied = True
    diagnostics.rebalance_reason = "final_scene_repair_split_applied"
    diagnostics.rebalance_actions = [
        f"split_final_scene:{diagnostics.final_scene_split_source_scene_id or base_id}->{lip_scene_id},{hold_scene_id}",
    ]
    diagnostics.oversizedScenesSplitCount = 1
    return storyboard_out


def _evaluate_lipsync_mouth_visibility(scene: ScenarioDirectorScene) -> tuple[bool, str]:
    shot = str(scene.shot_type or "").strip().lower()
    framing = str(scene.performance_framing or "").strip().lower()
    camera_text = str(scene.camera or "").strip().lower()
    frame_text = " ".join(
        [
            str(scene.frame_description or ""),
            str(scene.action_in_frame or ""),
            str(scene.viewer_hook or ""),
            str(scene.scene_goal or ""),
        ]
    ).lower()
    distant_shots = {"wide", "extreme_wide", "full_body", "aerial"}
    close_shots = {"close_up", "portrait", "medium_close", "chest_up", "shoulder_up", "face_close"}
    close_framings = {"face_close", "close_performance", "medium_close", "chest_up", "shoulder_up"}
    fallback_framings = {"tight_medium"}
    blocked_visibility_tokens = (
        "silhouette",
        "back view",
        "back-to-camera",
        "turned away",
        "profile only",
        "mouth hidden",
        "covered mouth",
        "hair over face",
        "hand over mouth",
        "mic covering mouth",
    )
    readability_tokens = (
        "visible mouth",
        "mouth clearly visible",
        "readable mouth",
        "readable facial articulation",
        "clear lyric articulation",
        "visible lyric delivery",
    )
    if shot in distant_shots or framing in {"wide_performance", "non_performance"}:
        return False, "framing_too_wide"
    if shot not in close_shots and framing not in close_framings and framing not in fallback_framings:
        return False, "framing_too_wide"
    if framing in fallback_framings and not any(token in frame_text for token in readability_tokens):
        return False, "framing_too_wide"
    if any(token in camera_text or token in frame_text for token in blocked_visibility_tokens):
        return False, "mouth_occluded"
    if "profile" in camera_text and "3/4" not in camera_text and "three-quarter" not in camera_text:
        return False, "profile_too_strong"
    return True, "mouth_visibility_clear_for_lipsync"


def _force_lipsync_friendly_composition(scene: ScenarioDirectorScene) -> None:
    scene.shot_type = "close_up"
    scene.performance_framing = "face_close"
    if not str(scene.scene_purpose or "").strip():
        scene.scene_purpose = "performance"
    scene.camera = _lip_sync_safe_camera_line()
    scene.viewer_hook = (
        "Face/mouth readability is primary: near-frontal or 3/4 angle, chest-up/shoulder-up framing, clear lyric articulation."
    )
    scene.frame_description = (
        "Close facial performance frame (face-close/chest-up), near-frontal or gentle 3/4 angle, mouth fully visible and unobstructed."
    )
    scene.action_in_frame = (
        "Performer delivers lyrics directly to camera with readable mouth articulation; no back view, no silhouette, no mouth occlusion."
    )
    scene.image_prompt = (
        "Tight performance portrait, near-frontal or 3/4, clear visible mouth, expressive lip articulation, no hand/mic/hair blocking lips."
    )
    scene.video_prompt = (
        "Maintain close lip-sync framing on performer face, stable near-frontal/3-4 angle, preserve readable mouth articulation throughout."
    )
    if not str(scene.scene_goal or "").strip() or "lip-sync" not in str(scene.scene_goal or "").strip().lower():
        scene.scene_goal = "Lip-sync performance with clearly readable mouth articulation in close framing."


def _infer_presentation_from_texts(texts: list[str]) -> str:
    lowered = " ".join(str(item or "").strip().lower() for item in texts if str(item or "").strip())
    if not lowered:
        return "unknown"
    has_mixed = any(token in lowered for token in PRESENTATION_MIXED_HINTS)
    has_male = any(token in lowered for token in PRESENTATION_MALE_HINTS) or bool(
        re.search(r"\b(male|man|boy|his|him)\b", lowered)
    )
    has_female = any(token in lowered for token in PRESENTATION_FEMALE_HINTS) or bool(
        re.search(r"\b(female|woman|girl|her|she)\b", lowered)
    )
    if has_mixed or (has_male and has_female):
        return "mixed"
    if has_male:
        return "male"
    if has_female:
        return "female"
    return "unknown"


def _find_raw_scene_payload(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> dict[str, Any]:
    payload_scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    scene_id = str(scene.scene_id or "").strip()
    for item in payload_scenes:
        if not isinstance(item, dict):
            continue
        item_scene_id = str(item.get("sceneId") or item.get("scene_id") or "").strip()
        if item_scene_id and scene_id and item_scene_id == scene_id:
            return item
    return {}


def _collect_text_fragments(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        clean = value.strip()
        if clean:
            out.append(clean)
        return out
    if isinstance(value, dict):
        for nested in value.values():
            out.extend(_collect_text_fragments(nested))
        return out
    if isinstance(value, list):
        for nested in value:
            out.extend(_collect_text_fragments(nested))
    return out


def _normalize_lookup_text(value: str) -> str:
    lowered = str(value or "").lower()
    lowered = re.sub(r"[_\-/]+", " ", lowered)
    lowered = re.sub(r"[^0-9a-zа-яё\s]+", " ", lowered, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", lowered).strip()


def _extract_vocal_signals(texts: list[str]) -> tuple[bool, bool]:
    normalized = _normalize_lookup_text(" ".join(str(item or "") for item in texts if str(item or "").strip()))
    if not normalized:
        return False, False
    has_mixed = any(hint in normalized for hint in VOCAL_MIXED_HINTS)
    has_male = has_mixed or any(hint in normalized for hint in VOCAL_MALE_HINTS)
    has_female = has_mixed or any(hint in normalized for hint in VOCAL_FEMALE_HINTS)
    return has_male, has_female


def _infer_vocal_presentation(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> str:
    audio_context = _normalize_audio_context(payload)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    raw_scene = _find_raw_scene_payload(scene, payload)
    prioritized_sources: list[list[str]] = []
    for key in VOCAL_PRIORITY_KEYS:
        bucket: list[str] = []
        bucket.extend(_collect_text_fragments(payload.get(key)))
        bucket.extend(_collect_text_fragments(metadata.get(key)))
        bucket.extend(_collect_text_fragments(source_meta.get(key)))
        bucket.extend(_collect_text_fragments(raw_scene.get(key)))
        if bucket:
            prioritized_sources.append(bucket)
    prioritized_sources.append(_collect_text_fragments(payload.get("transcriptHints") or payload.get("transcript_hints")))
    prioritized_sources.append([scene.local_phrase or "", scene.what_from_audio_this_scene_uses or "", scene.scene_goal or ""])
    prioritized_sources.append(_collect_text_fragments(raw_scene.get("lyricText") or raw_scene.get("lyrics")))
    prioritized_sources.append([str(metadata.get("transcript") or ""), str(source_meta.get("transcript") or "")])
    prioritized_sources.append(_collect_text_fragments({"metadata": metadata, "source_meta": source_meta, "raw_scene": raw_scene}))
    prioritized_sources.append(_collect_text_fragments(audio_context))

    for bucket in prioritized_sources:
        has_male, has_female = _extract_vocal_signals(bucket)
        if has_male and has_female:
            return "mixed"
        if has_male:
            return "male"
        if has_female:
            return "female"
    return "unknown"


def _role_is_human_performer(role: str) -> bool:
    return role.startswith("character_") or role in {"group", "group_faces"}


def _infer_role_presentation(role: str, payload: dict[str, Any], *, raw_scene: dict[str, Any] | None = None) -> str:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    display_labels = payload.get("displayLabelByRole") if isinstance(payload.get("displayLabelByRole"), dict) else {}
    role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    refs_used_by_role = raw_scene.get("refsUsedByRole") if isinstance(raw_scene, dict) and isinstance(raw_scene.get("refsUsedByRole"), dict) else {}
    role_ref = refs.get(role) if isinstance(refs.get(role), dict) else {}
    text_sources = [str(display_labels.get(role) or ""), str(role_type_by_role.get(role) or ""), str(role or "")]
    text_sources.extend(_collect_text_fragments(role_ref))
    text_sources.extend(_collect_text_fragments(refs_used_by_role.get(role)))
    return _infer_presentation_from_texts(text_sources)


def _infer_presentations_from_texts(texts: list[str], *, male_hints: tuple[str, ...], female_hints: tuple[str, ...]) -> str:
    normalized = _normalize_lookup_text(" ".join(str(item or "") for item in texts if str(item or "").strip()))
    if not normalized:
        return "unknown"
    has_male = any(hint in normalized for hint in male_hints)
    has_female = any(hint in normalized for hint in female_hints)
    if has_male and has_female:
        return "mixed"
    if has_male:
        return "male"
    if has_female:
        return "female"
    return "unknown"


def _collect_role_hint_texts(role: str, payload: dict[str, Any], raw_scene: dict[str, Any]) -> list[str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    display_labels = payload.get("displayLabelByRole") if isinstance(payload.get("displayLabelByRole"), dict) else {}
    role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    connected_refs_by_role = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    scene_refs_by_role = raw_scene.get("refsByRole") if isinstance(raw_scene.get("refsByRole"), dict) else {}
    scene_refs_used_by_role = raw_scene.get("refsUsedByRole") if isinstance(raw_scene.get("refsUsedByRole"), dict) else {}
    scene_connected_by_role = raw_scene.get("connectedRefsByRole") if isinstance(raw_scene.get("connectedRefsByRole"), dict) else {}
    role_metadata_by_role = payload.get("roleMetadataByRole") if isinstance(payload.get("roleMetadataByRole"), dict) else {}
    scene_role_metadata = raw_scene.get("roleMetadataByRole") if isinstance(raw_scene.get("roleMetadataByRole"), dict) else {}
    profile_by_role = payload.get("profileByRole") if isinstance(payload.get("profileByRole"), dict) else {}
    identity_by_role = payload.get("identityByRole") if isinstance(payload.get("identityByRole"), dict) else {}
    texts = [str(display_labels.get(role) or ""), str(role_type_by_role.get(role) or ""), str(role or "")]
    texts.extend(_collect_text_fragments(refs.get(role)))
    texts.extend(_collect_text_fragments(refs_by_role.get(role)))
    texts.extend(_collect_text_fragments(connected_refs_by_role.get(role)))
    texts.extend(_collect_text_fragments(scene_refs_by_role.get(role)))
    texts.extend(_collect_text_fragments(scene_refs_used_by_role.get(role)))
    texts.extend(_collect_text_fragments(scene_connected_by_role.get(role)))
    texts.extend(_collect_text_fragments(role_metadata_by_role.get(role)))
    texts.extend(_collect_text_fragments(scene_role_metadata.get(role)))
    texts.extend(_collect_text_fragments(profile_by_role.get(role)))
    texts.extend(_collect_text_fragments(identity_by_role.get(role)))
    return texts


def _infer_scene_performer_presentation(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> str:
    raw_scene = _find_raw_scene_payload(scene, payload)
    role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    role_candidates: list[str] = []
    for role in scene.actors or []:
        normalized = str(role or "").strip()
        if normalized and normalized not in role_candidates:
            role_candidates.append(normalized)
    for key in ("primaryRole", "primary_role"):
        normalized = str(raw_scene.get(key) or "").strip()
        if normalized and normalized not in role_candidates:
            role_candidates.append(normalized)
    for list_key in ("secondaryRoles", "secondary_roles", "sceneActiveRoles", "scene_active_roles", "participants", "refsUsed", "refs_used"):
        for role in (raw_scene.get(list_key) or []):
            normalized = str(role or "").strip()
            if normalized and normalized not in role_candidates:
                role_candidates.append(normalized)
    if "group" in role_candidates and any(role in role_candidates for role in ("character_1", "character_2")):
        role_candidates = [role for role in role_candidates if role != "group"]
    active_roles: list[str] = []
    for role in role_candidates:
        role_type = str(role_type_by_role.get(role) or "").strip().lower()
        if role_type and role_type in NON_PERFORMER_ROLE_TYPE_HINTS:
            continue
        if _role_is_human_performer(role):
            active_roles.append(role)
    active_roles = list(dict.fromkeys(active_roles))
    if not active_roles:
        return "unknown"
    preferred_character_roles = [role for role in ("character_1", "character_2") if role in active_roles]
    role_set = preferred_character_roles if preferred_character_roles else active_roles
    role_presentations: list[str] = []
    for role in role_set:
        label_based = _infer_presentations_from_texts(
            _collect_role_hint_texts(role, payload, raw_scene),
            male_hints=PERFORMER_MALE_HINTS,
            female_hints=PERFORMER_FEMALE_HINTS,
        )
        fallback = _infer_role_presentation(role, payload, raw_scene=raw_scene)
        chosen = label_based if label_based != "unknown" else fallback
        if chosen in {"male", "female", "mixed"}:
            role_presentations.append(chosen)
    if not role_presentations:
        return "unknown"
    normalized = set(role_presentations)
    if "mixed" in normalized or len(normalized) > 1:
        return "mixed"
    return next(iter(normalized))


def _role_has_explicit_refs(payload: dict[str, Any], role: str) -> bool:
    normalized_role = _normalize_scenario_role(role)
    if not normalized_role:
        return False
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    connected_refs_by_role = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    connected_summary_refs = connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {}
    for source_map in (refs_by_role, connected_refs_by_role, connected_summary_refs):
        refs = source_map.get(normalized_role)
        if isinstance(refs, list) and any(str(item or "").strip() for item in refs):
            return True
    context_item = context_refs.get(normalized_role) if isinstance(context_refs.get(normalized_role), dict) else {}
    if isinstance(context_item.get("refs"), list) and any(str(item or "").strip() for item in context_item.get("refs")):
        return True
    try:
        if int(context_item.get("count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    meta = context_item.get("meta") if isinstance(context_item.get("meta"), dict) else {}
    return _coerce_bool(meta.get("connected"), False)


def _build_music_video_cast_identity_lock(payload: dict[str, Any]) -> dict[str, Any]:
    locked_role_presentations: dict[str, str] = {}
    for role in ("character_1", "character_2", "character_3"):
        if not _role_has_explicit_refs(payload, role):
            continue
        texts = _collect_role_hint_texts(role, payload, {})
        presentation = _infer_presentations_from_texts(
            texts,
            male_hints=PERFORMER_MALE_HINTS,
            female_hints=PERFORMER_FEMALE_HINTS,
        )
        if presentation in {"male", "female", "mixed"}:
            locked_role_presentations[role] = presentation
    locked_roles = list(locked_role_presentations.keys())
    unique_presentations = {value for value in locked_role_presentations.values() if value in {"male", "female", "mixed"}}
    cast_identity_locked = bool(locked_roles)
    return {
        "enabled": cast_identity_locked,
        "lockedRoles": locked_roles,
        "lockedRolePresentationByRole": locked_role_presentations,
        "lockReason": (
            "explicit_character_refs_with_identity_hints"
            if cast_identity_locked
            else "no_explicit_character_refs_with_identity_hints"
        ),
        "globalPresentation": ("mixed" if "mixed" in unique_presentations or len(unique_presentations) > 1 else next(iter(unique_presentations), "unknown")),
    }


def _sanitize_cast_identity_text(text: str, *, preserve_presentation: str) -> tuple[str, bool]:
    cleaned = str(text or "")
    if not cleaned:
        return "", False
    if preserve_presentation == "female":
        patterns = (
            (r"\bman\b", "person"),
            (r"\bmen\b", "people"),
            (r"\bmale\b", "person"),
            (r"\bboy\b", "person"),
            (r"\bguy\b", "person"),
            (r"\bмужчина\b", "человек"),
            (r"\bпарень\b", "человек"),
            (r"\bмальчик\b", "человек"),
        )
    elif preserve_presentation == "male":
        patterns = (
            (r"\bwoman\b", "person"),
            (r"\bwomen\b", "people"),
            (r"\bfemale\b", "person"),
            (r"\bgirl\b", "person"),
            (r"\blady\b", "person"),
            (r"\bженщина\b", "человек"),
            (r"\bдевушка\b", "человек"),
            (r"\bдевочка\b", "человек"),
        )
    else:
        return cleaned, False
    changed = False
    for pattern, replacement in patterns:
        updated = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        if updated != cleaned:
            changed = True
            cleaned = updated
    return cleaned, changed


def _enforce_music_video_cast_identity_lock(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> tuple[ScenarioDirectorStoryboardOut, dict[str, Any]]:
    lock = _build_music_video_cast_identity_lock(payload)
    if not lock.get("enabled"):
        return storyboard_out, lock
    locked_by_role = lock.get("lockedRolePresentationByRole") if isinstance(lock.get("lockedRolePresentationByRole"), dict) else {}

    def _scene_target_presentation(scene: ScenarioDirectorScene) -> str:
        actor_presentations = [locked_by_role.get(role) for role in (scene.actors or []) if locked_by_role.get(role) in {"female", "male", "mixed"}]
        if not actor_presentations:
            actor_presentations = [value for value in locked_by_role.values() if value in {"female", "male", "mixed"}]
        normalized = set(actor_presentations)
        if not normalized:
            return "unknown"
        if "mixed" in normalized or len(normalized) > 1:
            return "mixed"
        return next(iter(normalized))

    total_rewrites = 0
    top_level_fields = ("story_summary", "director_summary", "full_scenario")
    global_presentation = str(lock.get("globalPresentation") or "unknown")
    for field_name in top_level_fields:
        value = str(getattr(storyboard_out, field_name, "") or "")
        rewritten, changed = _sanitize_cast_identity_text(value, preserve_presentation=global_presentation)
        if changed:
            setattr(storyboard_out, field_name, rewritten)
            total_rewrites += 1
    for scene in storyboard_out.scenes:
        target_presentation = _scene_target_presentation(scene)
        for field_name in ("frame_description", "action_in_frame", "image_prompt", "video_prompt", "scene_goal"):
            value = str(getattr(scene, field_name, "") or "")
            rewritten, changed = _sanitize_cast_identity_text(value, preserve_presentation=target_presentation)
            if changed:
                setattr(scene, field_name, rewritten)
                total_rewrites += 1
    lock["textRewritesApplied"] = total_rewrites
    return storyboard_out, lock


def _infer_performance_framing(
    scene: ScenarioDirectorScene,
    *,
    shot_type: str,
    performer_presentation: str,
    transition_candidate: bool,
) -> str:
    shot = str(shot_type or scene.shot_type or "").strip().lower()
    if transition_candidate:
        return "non_performance"
    if shot in {"duet_shared"}:
        return "duet_frame"
    has_human_focus = performer_presentation in {"male", "female", "mixed"} or _scene_has_human_performer(scene)
    if shot in {"close_up"}:
        return "face_close" if has_human_focus else "close_performance"
    if shot in {"medium_close", "medium"}:
        return "medium_performance" if has_human_focus else "non_performance"
    if shot in {"wide", "establishing"}:
        return "wide_performance" if has_human_focus else "non_performance"
    return "non_performance" if not has_human_focus else "medium_performance"


def _build_music_video_viewer_hook(scene: ScenarioDirectorScene, purpose: str, shot_type: str) -> str:
    action = str(scene.action_in_frame or scene.frame_description or scene.scene_goal or "").strip()
    action_tail = f" {action}" if action else ""
    duet_present = "character_1" in (scene.actors or []) and "character_2" in (scene.actors or [])
    if purpose == "hook" and shot_type == "wide":
        return (
            f"Open on a bold silhouette against scale so the clip starts with instant tension and visual authority.{action_tail}"
        ).strip()
    if purpose == "hook":
        return f"Start with an unmistakable first image that feels like the song already in motion.{action_tail}".strip()
    if purpose == "performance":
        if duet_present or shot_type == "duet_shared":
            return (
                f"Hold attention on the emotional connection between two performers inside one shared frame beat.{action_tail}"
            ).strip()
        return f"Lock the viewer on face, breath, and emotional micro-movement so the performance feels personal.{action_tail}".strip()
    if purpose == "transition":
        return f"Create a visible edit pivot where the state changes and the viewer wants the next cut immediately.{action_tail}".strip()
    if purpose == "payoff":
        return f"Deliver the emotional peak with a stronger image beat that releases built tension.{action_tail}".strip()
    if purpose == "ending_hold":
        return f"Leave a lingering final image that stays after the music resolves into distance.{action_tail}".strip()
    shot_label = str(shot_type or "").replace("_", " ").strip() or "scene"
    return f"Keep the clip breathing through contrast in scale, distance, and motion within this {shot_label} beat.{action_tail}".strip()


def _strip_ltx_meta_noise(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    banned_fragments = (
        "Scene intent in time",
        "Transition cue",
        "First→last transition",
        "Temporal feel",
        "Treat the motion as an edit pivot",
        "DUET IDENTITY CONTRACT",
        "Preserve slot",
        "Continuity contract",
    )
    lines = [segment.strip() for segment in re.split(r"[\n\r]+", cleaned) if segment.strip()]
    filtered = [line for line in lines if not any(fragment.lower() in line.lower() for fragment in banned_fragments)]
    return " ".join(filtered).strip()


VISIBLE_PROMPT_META_BANNED = (
    "baseline composition",
    "pre-change state",
    "resolved changed state",
    "a→b",
    "must be readable",
    "subject-position delta",
    "visibly evolve",
    "new state",
    "represents",
    "symbolizes",
    "cycle begins anew",
    "dramatic purpose",
    "beat function",
    "progression",
    "transition family",
    "hero arc",
    "world arc",
    "scene purpose",
    "by the end",
)


def _sanitize_visible_prompt_text(text: str) -> str:
    cleaned = _strip_ltx_meta_noise(text)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if any(phrase in lowered for phrase in VISIBLE_PROMPT_META_BANNED):
        sentences = [segment.strip() for segment in re.split(r"[.;\n\r]+", cleaned) if segment.strip()]
        filtered = [item for item in sentences if not any(phrase in item.lower() for phrase in VISIBLE_PROMPT_META_BANNED)]
        cleaned = ". ".join(filtered).strip()
    return re.sub(r"\s+", " ", cleaned).strip(" .")


def _join_visible_prompt_parts(parts: list[str]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for raw_part in parts:
        part = _sanitize_visible_prompt_text(raw_part)
        if not part:
            continue
        normalized = re.sub(r"\s+", " ", part).strip(" .;,:").lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        final_part = part if part[-1:] in ".!?" else f"{part}."
        result.append(final_part)
    return " ".join(result).strip()


def _remove_duplicate_prompt_sentences(text: str) -> str:
    sentences = [segment.strip(" .") for segment in re.split(r"[.!?]+", str(text or "").strip()) if segment.strip()]
    seen: set[str] = set()
    deduped: list[str] = []
    for sentence in sentences:
        norm = re.sub(r"\s+", " ", sentence).strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(sentence)
    return ". ".join(deduped).strip()


def _quality_filter_visible_prompt(text: str) -> str:
    cleaned = _sanitize_visible_prompt_text(text)
    cleaned = re.sub(
        r"\b(identity anchor|physical motion|camera movement|world and background motion|visible emotional state)\s*:\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = _remove_duplicate_prompt_sentences(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" .")


def _extract_scene_visual_motifs(scene: ScenarioDirectorScene) -> dict[str, str]:
    raw = " ".join(
        [
            str(scene.frame_description or ""),
            str(scene.action_in_frame or ""),
            str(scene.scene_goal or ""),
            str(scene.location or ""),
            str(scene.transition_type or ""),
        ]
    ).lower()
    motifs: dict[str, str] = {}
    if any(token in raw for token in ("dress", "fabric", "coat", "veil", "cape", "hem")):
        motifs["fabric"] = "fabric and hem"
    if "petal" in raw:
        motifs["petals"] = "petals"
    if any(token in raw for token in ("crowd", "audience", "people", "fans")):
        motifs["crowd"] = "crowd"
    if any(token in raw for token in ("stage", "neon", "spotlight", "strobe", "light")):
        motifs["light"] = "stage lights"
    if any(token in raw for token in ("smoke", "fog", "haze", "dust", "particle", "mist")):
        motifs["atmosphere"] = "haze and particles"
    if any(token in raw for token in ("mic", "microphone", "prop", "object", "rose", "mask")):
        motifs["prop"] = "props"
    return motifs


def _resolve_transition_family(scene: ScenarioDirectorScene) -> str:
    family = str(scene.transition_family or "").strip().lower()
    if family in {"transform", "reveal", "escalate", "resolve", "release", "afterimage"}:
        return family
    raw = " ".join([str(scene.transition_type or ""), str(scene.action_in_frame or ""), str(scene.scene_goal or "")]).lower()
    if any(token in raw for token in ("transform", "metamorph", "rose", "vortex", "change")):
        return "transform"
    if any(token in raw for token in ("reveal", "unmask", "show")):
        return "reveal"
    if any(token in raw for token in ("escalate", "build", "intens", "climax")):
        return "escalate"
    if any(token in raw for token in ("resolve", "calm", "settle")):
        return "resolve"
    if any(token in raw for token in ("release", "exhale", "drop")):
        return "release"
    if any(token in raw for token in ("afterimage", "fade", "residual")):
        return "afterimage"
    return "transform"


def _is_duet_scene(scene: ScenarioDirectorScene) -> bool:
    roles = {str(actor).strip().lower() for actor in (scene.actors or []) if str(actor).strip()}
    return "character_1" in roles and "character_2" in roles


def _build_duet_visible_hint(scene: ScenarioDirectorScene) -> str:
    if not _is_duet_scene(scene):
        return ""
    shot_type = str(scene.shot_type or "").strip().lower()
    render_mode = str(scene.render_mode or "").strip().lower()
    first_last = render_mode in {"first_last", "first_last_sound"} or bool(scene.needs_two_frames)
    if shot_type == "close_up" or first_last:
        return "Two distinct women stay clearly separated; one reads more 3/4, the other closer to profile, avoiding symmetric overlap"
    return "Two distinct women with different faces and hair stay readable in one frame"


def _build_props_visible_hint(scene: ScenarioDirectorScene) -> str:
    props = [str(prop).strip() for prop in (scene.props or []) if str(prop).strip()]
    if not props:
        return ""
    anchors = props[:2]
    return f"Visual anchors: {', '.join(anchors)}"


def _extract_character_identity_cues(payload: dict[str, Any], *, role: str = "character_1") -> dict[str, str]:
    payload = payload if isinstance(payload, dict) else {}
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    role_ctx = refs.get(role) if isinstance(refs.get(role), dict) else {}
    role_meta = role_ctx.get("meta") if isinstance(role_ctx.get("meta"), dict) else {}
    role_profile = role_meta.get("profile") if isinstance(role_meta.get("profile"), dict) else {}
    reference_profiles = payload.get("referenceProfiles") if isinstance(payload.get("referenceProfiles"), dict) else {}
    role_reference_profile = reference_profiles.get(role) if isinstance(reference_profiles.get(role), dict) else {}
    role_visual_profile = role_reference_profile.get("visualProfile") if isinstance(role_reference_profile.get("visualProfile"), dict) else {}
    role_invariants = role_reference_profile.get("invariants") if isinstance(role_reference_profile.get("invariants"), list) else []
    role_forbidden = role_reference_profile.get("forbiddenChanges") if isinstance(role_reference_profile.get("forbiddenChanges"), list) else []
    candidates = [
        str(role_ctx.get("label") or ""),
        str(role_ctx.get("shortLabel") or ""),
        str(role_meta.get("shortLabel") or ""),
        str(role_meta.get("hiddenProfile") or ""),
        str(role_profile.get("summary") or ""),
        str(role_profile.get("appearance") or ""),
        str(role_profile.get("identity") or ""),
        str(role_profile.get("wardrobe") or ""),
        str(role_profile.get("face") or ""),
        str(role_profile.get("hair") or ""),
        str(role_visual_profile.get("outfit") or ""),
        str(role_visual_profile.get("dominantColors") or ""),
        " ".join(str(item or "") for item in role_invariants),
        " ".join(str(item or "") for item in role_forbidden),
    ]
    blob = " ".join(candidates).lower()

    cues: dict[str, str] = {}
    strong_casual_separates = (
        any(token in blob for token in ("jeans", "denim"))
        and any(token in blob for token in ("top", "t-shirt", "tshirt", "tee", "crop top", "cropped top"))
    ) or (
        any(token in blob for token in ("jeans", "denim"))
        and "sneaker" in blob
    )
    strong_dress_evidence = any(
        token in blob
        for token in (
            "wearing a dress",
            "one-piece dress",
            "one piece dress",
            "dress bodice",
            "dress hem",
            "dress skirt",
            "gown",
            "maxi dress",
            "midi dress",
            "mini dress",
        )
    )
    garment_category = "unknown"
    if strong_casual_separates:
        garment_category = "casual_layered"
    elif any(token in blob for token in ("swimwear", "swimsuit", "bikini")):
        garment_category = "swimwear"
    elif any(token in blob for token in ("coat", "outerwear", "fur", "parka", "jacket")):
        garment_category = "outerwear"
    elif any(token in blob for token in ("suit", "blazer", "lapel", "trouser", "tuxedo")):
        garment_category = "suit"
    elif any(token in blob for token in ("armor", "armour", "plate", "metal cuirass")):
        garment_category = "armor"
    elif strong_dress_evidence:
        garment_category = "dress"
    elif any(token in blob for token in ("casual", "streetwear", "layered", "hoodie", "denim")):
        garment_category = "casual_layered"
    if any(token in blob for token in ("face", "woman", "girl", "female", "ethnic", "age", "same person")):
        cues["face_identity"] = "the same woman with the same face identity, ethnicity read, and age read"
    if any(token in blob for token in ("bun", "ponytail", "braid", "curl", "hair", "hairstyle")):
        cues["hair_identity"] = "the same hairstyle silhouette stays consistent"
        if "bun" in blob:
            cues["hair_identity"] = "the same hairstyle silhouette with the same bun shape stays consistent"
    if garment_category != "unknown":
        cues["garment_category"] = garment_category
    if any(token in blob for token in ("long sleeve", "long-sleeve", "sleeveless", "strapless", "cropped", "full length")):
        cues["coverage_identity"] = "coverage identity remains consistent with the reference garment"
    if any(token in blob for token in ("collar", "hood", "closure", "strap", "panel", "cuff", "waist", "bodice", "neckline")):
        cues["construction_identity"] = "construction identity remains consistent with reference cut/closure/paneling details"
    if any(token in blob for token in ("silhouette", "oversized", "fitted", "bodycon", "layered", "long", "short", "volume")):
        cues["silhouette_identity"] = "outfit silhouette identity remains unchanged from the reference"
    if any(token in blob for token in ("satin", "leather", "denim", "fur", "knit", "chiffon", "metallic", "sheer", "armor")):
        cues["material_identity"] = "material family identity remains stable"
    if any(token in blob for token in ("sneaker", "sneakers", "trainer", "trainers")):
        cues["footwear_identity"] = "footwear category stays fixed, with sneakers remaining sneakers"
    elif any(token in blob for token in ("boot", "heels", "footwear", "chunky boots")):
        cues["footwear_identity"] = "footwear category stays fixed, with boots remaining boots"
    if strong_casual_separates:
        cues["garment_top_identity"] = "top_with_jeans"
        cues.setdefault("silhouette_identity", "outfit silhouette identity remains unchanged from the reference")
        cues.setdefault("material_identity", "material family identity remains stable")
    if any(token in blob for token in ("v-neck", "v neck", "deep v")):
        cues["neckline_identity"] = "v_neck"
    elif any(token in blob for token in ("crew neck", "crewneck", "round neck", "round-neck")):
        cues["neckline_identity"] = "crew_neck"
    elif any(token in blob for token in ("square neck", "square-neck")):
        cues["neckline_identity"] = "square_neck"
    elif any(token in blob for token in ("sweetheart neckline", "sweetheart neck", "sweetheart")):
        cues["neckline_identity"] = "sweetheart_neckline"
    elif any(token in blob for token in ("halter neckline", "halter neck", "halter")):
        cues["neckline_identity"] = "halter_neckline"
    elif any(token in blob for token in ("off shoulder", "off-shoulder", "bare shoulder")):
        cues["neckline_identity"] = "off_shoulder_neckline"
    elif any(token in blob for token in ("high neck", "high-neck", "turtleneck", "turtle neck", "mock neck")):
        cues["neckline_identity"] = "high_neck"
    elif any(token in blob for token in ("collared", "collar shirt", "shirt collar", "polo collar")):
        cues["neckline_identity"] = "collared_neckline"
    if not cues.get("garment_top_identity"):
        if strong_casual_separates:
            cues["garment_top_identity"] = "top_with_jeans"
        elif any(token in blob for token in ("crop top", "cropped top")):
            cues["garment_top_identity"] = "crop_top"
        elif any(token in blob for token in ("t-shirt", "t shirt", "tshirt", "tee")):
            cues["garment_top_identity"] = "t_shirt_top"
        elif any(token in blob for token in ("tank top", "tank", "cami", "camisole", "spaghetti strap")):
            cues["garment_top_identity"] = "tank_or_cami_top"
        elif any(token in blob for token in ("blouse", "peasant top")):
            cues["garment_top_identity"] = "blouse_top"
        elif any(token in blob for token in ("hoodie", "hooded sweatshirt")):
            cues["garment_top_identity"] = "hoodie_top"
        elif any(token in blob for token in ("jacket", "coat", "parka", "trench", "outerwear")):
            cues["garment_top_identity"] = "jacket_or_coat_top"
        elif any(token in blob for token in ("tailored", "blazer", "suit jacket", "structured top", "lapel")):
            cues["garment_top_identity"] = "tailored_top"
        elif strong_dress_evidence or any(token in blob for token in ("dress", "gown")):
            cues["garment_top_identity"] = "dress_top"
        elif any(token in blob for token in ("swimwear", "swimsuit", "bikini")):
            cues["garment_top_identity"] = "swimwear_top"
    signature_parts: list[str] = []
    if any(token in blob for token in ("rose", "floral", "petal")):
        signature_parts.append("rose/floral garment details stay visible")
        cues["signature_details_identity"] = "signature floral/applique detail identity remains unchanged"
    if any(token in blob for token in ("magenta", "lining", "inner lining", "colored lining", "pink trim")):
        cues["color_identity"] = "base garment/accent color family remains unchanged"
    if any(token in blob for token in ("pink detail", "pink trim", "accent", "embroid", "trim", "ornament", "signature")):
        signature_parts.append("signature accents and trims remain intact")
    if signature_parts:
        cues["signature_details"] = ", ".join(signature_parts)
    return cues


def _build_scene_outfit_profile_from_payload(payload: dict[str, Any], *, role: str = "character_1") -> dict[str, Any]:
    cues = _extract_character_identity_cues(payload, role=role)
    garment_category = str(cues.get("garment_category") or "unknown").strip().lower() or "unknown"
    family_module = "unknown"
    if garment_category in {"dress", "gown"}:
        family_module = "dress"
    elif garment_category in {"swimwear", "swimsuit", "bikini"}:
        family_module = "swimwear"
    elif garment_category in {"outerwear", "coat", "fur_coat", "jacket"}:
        family_module = "outerwear"
    elif garment_category in {"suit", "tuxedo", "tailored"}:
        family_module = "suit"
    elif garment_category in {"armor", "armour"}:
        family_module = "armor"
    elif garment_category in {"casual", "casual_layered", "streetwear"}:
        family_module = "casual_layered"
    family_fields_map: dict[str, list[str]] = {
        "dress": ["sleeve_identity", "bodice_identity", "neckline_identity", "skirt_volume_identity", "hem_length_identity", "lining_identity", "applique_identity"],
        "swimwear": ["top_cut_identity", "bottom_cut_identity", "strap_layout_identity", "coverage_level_identity", "fabric_finish_identity", "no_added_skirt_reinterpretation"],
        "outerwear": ["coat_length_identity", "fur_volume_identity", "collar_or_hood_identity", "cuff_identity", "closure_identity", "outerwear_silhouette_identity"],
        "suit": ["jacket_cut_identity", "lapel_identity", "trouser_cut_identity", "shirt_layer_identity", "tie_or_accessory_identity"],
        "armor": ["plate_layout_identity", "rigid_segment_identity", "joint_coverage_identity", "helmet_or_headgear_identity"],
        "casual_layered": ["base_layer_identity", "mid_layer_identity", "outer_layer_identity", "layer_order_identity"],
        "unknown": [],
    }
    return {
        "garment_category": garment_category,
        "garment_top_identity": str(cues.get("garment_top_identity") or "unknown").strip() or "unknown",
        "neckline_identity": str(cues.get("neckline_identity") or "unknown").strip() or "unknown",
        "coverage_identity": str(cues.get("coverage_identity") or "unknown").strip() or "unknown",
        "construction_identity": str(cues.get("construction_identity") or "unknown").strip() or "unknown",
        "silhouette_identity": str(cues.get("silhouette_identity") or "unknown").strip() or "unknown",
        "material_identity": str(cues.get("material_identity") or "unknown").strip() or "unknown",
        "signature_details_identity": str(cues.get("signature_details_identity") or "unknown").strip() or "unknown",
        "color_identity": str(cues.get("color_identity") or "unknown").strip() or "unknown",
        "footwear_identity": str(cues.get("footwear_identity") or "unknown").strip() or "unknown",
        "accessory_identity": str(cues.get("accessory_identity") or "unknown").strip() or "unknown",
        "family_module": family_module,
        "family_fields": family_fields_map.get(family_module, []),
    }


def _build_normalized_hero_appearance_contract(payload: dict[str, Any], *, role: str = "character_1") -> dict[str, str]:
    cues = _extract_character_identity_cues(payload, role=role)
    outfit = _build_scene_outfit_profile_from_payload(payload, role=role)
    contract = {
        "face_identity": str(cues.get("face_identity") or "same face identity as hero reference").strip(),
        "face_shape": str(cues.get("face_shape") or "same face shape/impression as hero reference").strip(),
        "hair_identity": str(cues.get("hair_identity") or "same hair color and structure as hero reference").strip(),
        "body_identity": str(cues.get("body_identity") or "same body proportions/silhouette as hero reference").strip(),
        "body_fullness_identity": str(cues.get("body_fullness_identity") or "do not slim down or compress body fullness").strip(),
        "height_impression_identity": str(cues.get("height_impression_identity") or "same apparent height impression as hero reference").strip(),
        "garment_top_identity": str(cues.get("garment_top_identity") or str(outfit.get("garment_category") or "same garment category/top identity")).strip(),
        "neckline_identity": str(cues.get("neckline_identity") or "same neckline identity as hero reference").strip(),
        "silhouette_identity": str(outfit.get("silhouette_identity") or cues.get("silhouette_identity") or "same outfit silhouette identity").strip(),
        "color_identity": str(outfit.get("color_identity") or cues.get("color_identity") or "same garment/hair base color identity").strip(),
        "material_identity": str(outfit.get("material_identity") or cues.get("material_identity") or "same material family identity").strip(),
        "accessory_identity": str(outfit.get("accessory_identity") or cues.get("accessory_identity") or "same accessory identity").strip(),
        "footwear_identity": str(outfit.get("footwear_identity") or cues.get("footwear_identity") or "same footwear identity").strip(),
        "signature_details_identity": str(outfit.get("signature_details_identity") or cues.get("signature_details_identity") or "same signature garment details").strip(),
    }
    return {key: value for key, value in contract.items() if str(value).strip() and str(value).strip().lower() != "unknown"}


def _normalize_scene_outfit_confidence_scores(raw_scores: Any, *, fallback: float = 0.5) -> dict[str, float]:
    scores = raw_scores if isinstance(raw_scores, dict) else {}
    aliases = {
        "outfitProfile": "coarse",
        "outfit_profile": "coarse",
        "garmentCategory": "garment_category",
        "coverageIdentity": "coverage_identity",
        "constructionIdentity": "construction_identity",
        "silhouetteIdentity": "silhouette_identity",
        "materialIdentity": "material_identity",
        "signatureDetailsIdentity": "signature_details_identity",
        "colorIdentity": "color_identity",
        "footwearIdentity": "footwear_identity",
        "accessoryIdentity": "accessory_identity",
    }
    keys = [
        "garment_category",
        "coverage_identity",
        "construction_identity",
        "silhouette_identity",
        "material_identity",
        "signature_details_identity",
        "color_identity",
        "footwear_identity",
        "accessory_identity",
    ]
    normalized: dict[str, float] = {}
    coarse: float | None = None
    for key, value in scores.items():
        canonical = aliases.get(str(key), str(key))
        confidence = _safe_float(value)
        if confidence is None:
            continue
        confidence = max(0.0, min(1.0, float(confidence)))
        if canonical == "coarse":
            coarse = confidence
        elif canonical in keys:
            normalized[canonical] = confidence
    if coarse is None:
        coarse = max(0.0, min(1.0, float(_safe_float(fallback, 0.5))))
    for key in keys:
        normalized.setdefault(key, coarse)
    return normalized


def _extract_target_outfit_profile_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "targetOutfitProfile",
        "target_outfit_profile",
        "targetGarmentProfile",
        "target_garment_profile",
        "wardrobeOverride",
        "wardrobe_override",
        "outfitOverride",
        "outfit_override",
        "tryOnGarmentProfile",
        "try_on_garment_profile",
    ):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _build_character_identity_visible_lock(
    scene: ScenarioDirectorScene,
    payload: dict[str, Any] | None = None,
    *,
    role: str = "character_1",
) -> tuple[str, list[str]]:
    payload = payload if isinstance(payload, dict) else {}
    active_roles = {str(actor).strip().lower() for actor in (scene.actors or []) if str(actor).strip()}
    if role not in active_roles:
        return "", []
    has_explicit_refs = _role_has_explicit_refs(payload, role)
    cues = _extract_character_identity_cues(payload, role=role)
    if has_explicit_refs:
        cues.setdefault("face_identity", "same woman as character_1 reference across all scenes; no face redesign")
        cues.setdefault("hair_identity", "no hairstyle redesign across scenes; keep same hair color, cut/length, parting, and volume unless explicitly requested")
        cues.setdefault("garment_identity", "wardrobe remains identical across scenes unless storyboard explicitly requests costume change")
        cues.setdefault("garment_category", "same garment category as reference unless task mode explicitly allows costume change")
        cues.setdefault("coverage_identity", "same outfit coverage identity as reference")
        cues.setdefault("construction_identity", "same outfit construction identity as reference")
        cues.setdefault("silhouette_identity", "same outfit silhouette identity as reference")
        cues.setdefault("material_identity", "same material family identity as reference")
        cues.setdefault("signature_details_identity", "same signature detail identity as reference")
        cues.setdefault("color_identity", "same base garment and accent color identity as reference")
        cues.setdefault("footwear_identity", "same footwear identity and category as reference")
        cues.setdefault("body_identity", "same body shape, proportions, silhouette, height/build read across all scenes; no fuller/thinner drift")
        cues.setdefault("makeup_identity", "makeup style remains stable across scenes; no spontaneous redesign")
        cues.setdefault("accessory_identity", "accessories stay stable across scenes when established; no random invention/disappearance unless explicitly requested")
        cues.setdefault("age_consistency", "same apparent age across all scenes; no face-age drift and no maturity drift")
        cues.setdefault("color_identity", "keep stable base colors across scenes for skin tone, hair color, garment/fabric color, and accessory color; lighting may vary without redesigning base colors")
    fields_used = [key for key, value in cues.items() if str(value).strip()]
    if not cues:
        return "", []
    is_first_scene = _safe_float(scene.time_start, 0.0) <= 0.05
    locks: list[str] = [
        str(cues.get("face_identity") or ""),
        str(cues.get("hair_identity") or ""),
        str(cues.get("garment_category") or ""),
        str(cues.get("coverage_identity") or ""),
        str(cues.get("construction_identity") or ""),
        str(cues.get("silhouette_identity") or ""),
        str(cues.get("material_identity") or ""),
        str(cues.get("signature_details_identity") or ""),
        str(cues.get("color_identity") or ""),
        str(cues.get("garment_identity") or ""),
        str(cues.get("signature_details") or ""),
        str(cues.get("footwear_identity") or ""),
        str(cues.get("body_identity") or ""),
        str(cues.get("makeup_identity") or ""),
        str(cues.get("accessory_identity") or ""),
        str(cues.get("age_consistency") or ""),
        str(cues.get("color_identity") or ""),
        "no ethnicity drift, no silent wardrobe redesign, no garment-category reinterpretation, no silhouette change",
        "no jewelry/accessory invention unless the scene explicitly requests it",
        "same apparent age and same body silhouette/proportions across all scenes",
        "hairstyle, accessories, and visible styling remain stable unless explicitly changed by storyboard",
        "skin tone, hair color, and garment colors remain consistent across scenes",
        "face/hair identity lock and garment lock are separate and both must hold",
        "do not redesign outfit construction, coverage, silhouette, material family, signature details, or footwear identity",
    ]
    if is_first_scene:
        locks.append("first scene lock: hold face, hair, outfit category/coverage/construction/silhouette/signature details, and footwear exactly as reference")
    return "; ".join([part for part in dict.fromkeys(locks) if part]), fields_used


CLUB_VENUE_RELEVANCE_HINTS = (
    "club",
    "nightclub",
    "dance floor",
    "bar",
    "booth",
    "corridor",
    "lounge",
    "neon",
    "vip",
    "dj",
    "stage",
)
NON_CLUB_WORLD_HINTS = (
    "apartment",
    "flat",
    "living room",
    "bedroom",
    "kitchen",
    "home",
    "house",
    "loft",
    "office",
)


def _is_club_world_relevant_scene(scene: ScenarioDirectorScene, payload: dict[str, Any] | None = None) -> bool:
    payload = payload if isinstance(payload, dict) else {}
    scene_local_text = " ".join(
        [
            str(scene.location or ""),
            str(scene.frame_description or ""),
            str(scene.action_in_frame or ""),
            str(scene.scene_goal or ""),
        ]
    ).lower()
    if any(token in scene_local_text for token in NON_CLUB_WORLD_HINTS):
        return False
    if any(token in scene_local_text for token in CLUB_VENUE_RELEVANCE_HINTS):
        return True

    payload_background_text = " ".join(
        [
            str(payload.get("environment") or ""),
            str(payload.get("story_summary") or ""),
            str(payload.get("director_summary") or ""),
            str(payload.get("full_scenario") or ""),
        ]
    ).lower()
    return (
        any(token in payload_background_text for token in CLUB_VENUE_RELEVANCE_HINTS)
        and not any(token in payload_background_text for token in NON_CLUB_WORLD_HINTS)
    )


def _is_apartment_world_relevant_scene(scene: ScenarioDirectorScene, payload: dict[str, Any] | None = None) -> bool:
    payload = payload if isinstance(payload, dict) else {}
    scene_local_text = " ".join(
        [
            str(scene.location or ""),
            str(scene.frame_description or ""),
            str(scene.action_in_frame or ""),
            str(scene.scene_goal or ""),
        ]
    ).lower()
    if any(token in scene_local_text for token in NON_CLUB_WORLD_HINTS):
        return True
    payload_background_text = " ".join(
        [
            str(payload.get("environment") or ""),
            str(payload.get("story_summary") or ""),
            str(payload.get("director_summary") or ""),
            str(payload.get("full_scenario") or ""),
        ]
    ).lower()
    return any(token in payload_background_text for token in NON_CLUB_WORLD_HINTS)


def build_ltx_visible_image_prompt(scene: ScenarioDirectorScene, payload: dict[str, Any] | None = None) -> str:
    lead = str(scene.frame_description or "").strip()
    action = str(scene.action_in_frame or "").strip()
    location = str(scene.location or "").strip()
    emotion = str(scene.emotion or "").strip()
    shot = str(scene.shot_type or "").replace("_", " ").strip()
    subject = ", ".join([actor for actor in (scene.actors or []) if str(actor).strip()][:2]).strip()
    parts: list[str] = []
    opener = f"Cinematic 9:16 vertical {shot} frame" if shot else "Cinematic 9:16 vertical frame"
    if subject:
        opener = f"{opener} with {subject}"
    parts.append(opener)
    if lead:
        parts.append(lead)
    if action and action.lower() not in lead.lower() and not _is_lip_sync_music_scene(scene):
        parts.append(action)
    duet_hint = _build_duet_visible_hint(scene)
    if duet_hint:
        parts.append(duet_hint)
    props_hint = _build_props_visible_hint(scene)
    if props_hint:
        parts.append(props_hint)
    if location:
        parts.append(f"The background clearly reads as {location}")
    if emotion:
        parts.append(f"The visible emotion is {emotion}")
    identity_lock, _ = _build_character_identity_visible_lock(scene, payload=payload, role="character_1")
    if identity_lock:
        parts.append(identity_lock)
    parts.append(
        "World continuity lock: keep one coherent real venue across scenes; only zone/angle/distance/mood may vary while architecture/material palette/lighting system stay consistent."
    )
    apartment_world = _is_apartment_world_relevant_scene(scene, payload=payload)
    apply_club_venue_bible = (not apartment_world) and (_is_lip_sync_music_scene(scene) or _is_club_world_relevant_scene(scene, payload=payload))
    if _is_lip_sync_music_scene(scene):
        parts.append(
            "Still-photo canon: prioritize a single frozen photographic moment with scene-specific composition, gaze, pose, and body orientation."
        )
    if apply_club_venue_bible:
        parts.append(
            "Club venue bible (photo continuity): this is one real club, not different clubs; dance floor, bar, corridor, booth, and window zone must read as connected zones of the same interior."
        )
        parts.append(
            "Keep one interior language across scenes: same wall/ceiling logic, bar/furniture design, floor material, neon palette, haze-light rig, and crowd styling; no venue hopping or interior redesign."
        )
    if apartment_world:
        parts.append(
            "Apartment/home world bible (photo continuity): keep one coherent apartment interior identity with stable room geometry, window/ledge identity, furniture family, and material palette."
        )
        parts.append(
            "Do not introduce club/nightlife/dance-floor/bar venue semantics for apartment/home/loft/living-room scenes."
        )
    return _quality_filter_visible_prompt(_join_visible_prompt_parts(parts))


def _derive_visual_delta_axes(scene: ScenarioDirectorScene) -> list[str]:
    raw = " ".join([scene.action_in_frame, scene.frame_description, scene.camera, scene.transition_type]).lower()
    axes: list[str] = []
    if any(token in raw for token in ("turn", "spin", "twirl", "jump", "drop", "kneel", "rise")):
        axes.append("pose_phase")
    if any(token in raw for token in ("look", "gaze", "eye", "stare", "profile", "face")):
        axes.append("gaze_direction")
    if any(token in raw for token in ("dress", "fabric", "coat", "hair", "cape", "prop", "mic", "jacket")):
        axes.append("fabric_or_object_state")
    if any(token in raw for token in ("close", "wide", "push", "dolly", "crane", "zoom")):
        axes.append("camera_distance")
    if any(token in raw for token in ("crowd", "audience", "people", "club", "stage")):
        axes.append("crowd_spacing")
    if any(token in raw for token in ("smoke", "haze", "fog", "particle", "strobe", "flash", "light")):
        axes.append("background_intensity")
    defaults = ["pose_phase", "gaze_direction", "camera_distance", "background_intensity"]
    merged = list(dict.fromkeys((scene.delta_axes or []) + axes + defaults))
    return merged[:6]


def _normalize_prompt_tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9а-яё\s]", " ", str(text or "").lower())
    tokens = [token for token in normalized.split() if len(token) > 2]
    return set(tokens)


def _has_visual_change_cues(text: str) -> bool:
    lowered = str(text or "").lower()
    cues = (
        "closer",
        "leans",
        "lean",
        "turns",
        "turned",
        "smile",
        "smiles",
        "opens up",
        "gaze shifts",
        "distance",
        "framing tightens",
        "by the end",
        "final state",
        "крупнее",
        "поворач",
        "улыб",
        "ближе",
        "в финале",
    )
    return any(cue in lowered for cue in cues)


def _ensure_first_last_visual_delta(start_prompt: str, end_prompt: str, scene: ScenarioDirectorScene) -> str:
    start_clean = _strip_ltx_meta_noise(start_prompt)
    end_clean = _strip_ltx_meta_noise(end_prompt)
    if not start_clean:
        return end_clean
    start_norm = re.sub(r"\s+", " ", start_clean).strip().lower()
    end_norm = re.sub(r"\s+", " ", end_clean).strip().lower()
    if not end_clean:
        return ""

    start_tokens = _normalize_prompt_tokens(start_norm)
    end_tokens = _normalize_prompt_tokens(end_norm)
    overlap = len(start_tokens & end_tokens) / max(1, min(len(start_tokens), len(end_tokens)))
    contains = start_norm in end_norm or end_norm in start_norm
    too_similar = start_norm == end_norm or contains or overlap >= 0.78
    lacks_delta = not _has_visual_change_cues(end_norm)
    generic_end = any(
        phrase in end_norm
        for phrase in (
            "same subject in same location",
            "earlier phase",
            "later phase",
            "body tension releases",
            "silhouette changed",
        )
    )
    if too_similar or lacks_delta or generic_end:
        return ""
    return end_clean


def _build_first_last_state_prompts(scene: ScenarioDirectorScene, base_start: str, base_end: str) -> tuple[str, str]:
    axes = _derive_visual_delta_axes(scene)
    scene.delta_axes = axes
    subject = ", ".join([actor for actor in (scene.actors or []) if str(actor).strip()][:2]).strip() or "lead performer"
    location = str(scene.location or "club stage").strip()
    family = _resolve_transition_family(scene)
    motifs = _extract_scene_visual_motifs(scene)
    fabric = motifs.get("fabric", "fabric")
    petals = motifs.get("petals", "")
    crowd = motifs.get("crowd", "crowd")
    light = motifs.get("light", "lights")
    atmosphere = motifs.get("atmosphere", "haze")
    prop = motifs.get("prop", "props")
    start_detail = f"The body is in an early motion phase, gaze still controlled, with {fabric} close to the body and {light} kept restrained."
    end_detail = f"The body reaches a later phase, gaze shifts with stronger silhouette spread, while {light} and {atmosphere} become more active."
    if family == "transform":
        start_detail = f"The silhouette still reads as the original look, {fabric} mostly intact, and {prop} still in normal form."
        end_detail = f"The silhouette visibly transforms as {fabric} opens into a new shape, {prop} shifts state, and {petals or atmosphere} fill the lower frame."
    elif family == "reveal":
        start_detail = f"The subject is partially hidden by pose and framing, with {crowd} and foreground elements still masking details."
        end_detail = f"The reveal is complete: the face and torso are unobstructed, the gaze is direct, and the subject dominates center while {crowd} opens around them."
    elif family == "escalate":
        start_detail = f"The movement starts contained, with compact posture, controlled gaze, and restrained {light} behind the subject."
        end_detail = f"The movement peaks with expanded limbs and silhouette, stronger forward dominance, and aggressive {light} and {atmosphere} motion."
    elif family == "resolve":
        start_detail = f"The scene still carries momentum: shoulders lifted, breath visible, and background {light} still pulsing."
        end_detail = f"The scene resolves into stable posture, softened gaze, quieter {light}, and clearer depth separation from background."
    elif family == "release":
        start_detail = f"Tension is still present in neck, shoulders, and hands, with compressed spacing between subject and {crowd}."
        end_detail = f"Tension releases into open chest and longer lines; {crowd} steps back visually, and {atmosphere} drifts into a softer trail."
    elif family == "afterimage":
        start_detail = f"The subject remains fully present in frame with a sharp silhouette and active highlights."
        end_detail = f"The final state leaves an afterimage feel: the silhouette thins or exits, lights fade, residual {atmosphere} hangs, and the stage feels nearly empty."
    start_prompt = _join_visible_prompt_parts(
        [
            f"Cinematic 9:16 vertical frame at {location} with {subject}",
            base_start,
            start_detail,
        ]
    )
    end_prompt = _join_visible_prompt_parts(
        [
            f"Cinematic 9:16 vertical frame in the same {location} with the same {subject}",
            base_end,
            end_detail,
        ]
    )
    return _quality_filter_visible_prompt(start_prompt), _quality_filter_visible_prompt(end_prompt)


def build_ltx_visible_first_last_prompts(
    scene: ScenarioDirectorScene,
    raw_scene: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    raw_scene = raw_scene if isinstance(raw_scene, dict) else {}
    explicit_start = str(raw_scene.get("startFramePrompt") or raw_scene.get("start_frame_prompt") or scene.start_frame_prompt or "").strip()
    explicit_end = str(raw_scene.get("endFramePrompt") or raw_scene.get("end_frame_prompt") or scene.end_frame_prompt or "").strip()

    base_start = explicit_start or str(scene.frame_description or scene.scene_goal or scene.image_prompt or "").strip()
    base_end = explicit_end or str(scene.scene_goal or scene.action_in_frame or scene.frame_description or "").strip()
    identity_lock, _ = _build_character_identity_visible_lock(scene, payload=payload, role="character_1")
    if identity_lock:
        base_start = _join_visible_prompt_parts([base_start, identity_lock])
        base_end = _join_visible_prompt_parts([base_end, identity_lock])
    start_prompt, end_prompt = _build_first_last_state_prompts(scene, base_start, base_end)
    end_prompt = _ensure_first_last_visual_delta(start_prompt, end_prompt, scene)
    if not end_prompt:
        scene.render_mode = "image_video"
        scene.resolved_workflow_key = "i2v"
        scene.resolved_workflow_file = CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY["i2v"]
        scene.ltx_mode = "i2v"
        scene.needs_two_frames = False
        scene.video_generation_route = "downgraded_to_i2v"
        scene.video_downgrade_reason_code = "first_last_visual_delta_too_weak"
        scene.video_downgrade_reason_message = "first_last downgraded to i2v because generated A/B prompts had weak visual delta."
        return "", ""
    return start_prompt, end_prompt


def build_ltx_visible_video_prompt(scene: ScenarioDirectorScene, payload: dict[str, Any] | None = None) -> str:
    action = str(scene.action_in_frame or scene.scene_goal or "").strip()
    camera = str(scene.camera or "").strip()
    location = str(scene.location or "").strip()
    emotion = str(scene.emotion or "").strip()
    subject = ", ".join([actor for actor in (scene.actors or []) if str(actor).strip()][:2]).strip()
    parts: list[str] = ["Cinematic 9:16 vertical shot"]
    if location:
        parts.append(f"in {location}")
    if subject:
        parts.append(f"with {subject}")
    sentence = " ".join(parts).strip()
    motion_line = action or "The subject keeps flowing motion with readable body mechanics"
    camera_line = f"The camera {camera}" if camera else "The camera tracks smoothly to preserve clear motion arcs"
    emotion_line = f"The face reads {emotion}" if emotion else ""
    lipsync_line = ""
    if _is_lip_sync_music_scene(scene):
        camera_line = _lip_sync_safe_camera_line()
        motion_line = (
            "Emotional singing performance with clear mouth readability, phrase-driven hand acting, lyric-marking finger/hand phrasing, "
            "one-hand-to-chest and open-outward gesture vocabulary, coordinated head/shoulder/hand accents, controlled upper-body pulse, "
            "small forward intention/weight shift, micro-expressions, breath detail, and minimal locomotion; no chase/run/stunt action"
        )
        lipsync_line = (
            "Face readability and mouth continuity stay clear for emotional singing; expressive hand/upper-body freedom is allowed but must never block lipsync readability "
            "or loosen wardrobe continuity."
        )
    identity_lock, _ = _build_character_identity_visible_lock(scene, payload=payload, role="character_1")
    ltx_canon_block = build_ltx_video_canon_block(lip_sync=_is_lip_sync_music_scene(scene))
    return _quality_filter_visible_prompt(
        _join_visible_prompt_parts([sentence, motion_line, camera_line, emotion_line, lipsync_line, identity_lock, ltx_canon_block])
    )


def _build_music_video_image_prompt(scene: ScenarioDirectorScene, payload: dict[str, Any] | None = None) -> str:
    if not _scene_has_character_subject(scene):
        return ""
    subject = ", ".join([actor for actor in (scene.actors or []) if str(actor).strip().startswith("character_")][:2]).strip()
    prompt = _join_visible_prompt_parts([f"The frame keeps {subject} readable" if subject else "", build_ltx_visible_image_prompt(scene, payload=payload)])
    return _quality_filter_visible_prompt(_strip_video_only_semantics_from_image_prompt(prompt))


def _build_music_video_video_prompt(scene: ScenarioDirectorScene, payload: dict[str, Any] | None = None) -> str:
    if not _scene_has_character_subject(scene):
        return ""
    subject = ", ".join([actor for actor in (scene.actors or []) if str(actor).strip().startswith("character_")][:2]).strip()
    return _join_visible_prompt_parts([f"The performance stays centered on {subject}" if subject else "", build_ltx_visible_video_prompt(scene, payload=payload)])


def _is_lip_sync_music_scene(scene: ScenarioDirectorScene) -> bool:
    return str(scene.resolved_workflow_key or "").strip().lower() == "lip_sync_music" or bool(scene.lip_sync)


def _lip_sync_safe_camera_line() -> str:
    return (
        "Steady close performance camera, static or very slow push-in with minimal drift; allow only gentle left/right side arc "
        "(about 90–180° partial orbit) around performer; no full 360° wrap, no inversion, no vertical roll, no axial twist; "
        "camera stays upright with stable horizon while performer may gently rotate and remain face-readable."
    )


def _strip_video_only_semantics_from_image_prompt(text: str) -> str:
    lines = [segment.strip() for segment in re.split(r"[.\n]+", str(text or "").strip()) if segment.strip()]
    banned_markers = (
        "lyric articulation",
        "mouth readability",
        "lip articulation",
        "lip-sync",
        "lip sync",
        "subtle head motion",
        "body sway",
        "partial arc",
        "orbit",
        "no 360",
        "360",
        "360°",
        "push-in",
        "camera",
        "locomotion",
        "ltx",
        "motion canon",
        "live-session",
        "live session",
        "animation",
    )
    cleaned = [line for line in lines if not any(marker in line.lower() for marker in banned_markers)]
    return ". ".join(cleaned).strip()


def _derive_audio_emotion_direction(scene: ScenarioDirectorScene) -> str:
    explicit = str(scene.audio_emotion_direction or "").strip().lower()
    if explicit:
        return explicit
    signal = " ".join([
        str(scene.emotion or ""),
        str(scene.performance_phase or ""),
        str(scene.audio_anchor_evidence or ""),
        str(scene.local_phrase or ""),
        str(scene.what_from_audio_this_scene_uses or ""),
        str(scene.clip_arc_stage or ""),
    ]).lower()
    if any(token in signal for token in ("sad", "ache", "pain", "fragile", "melanch", "restrain")):
        return "restrained_ache"
    if any(token in signal for token in ("intimate", "soft", "quiet", "tender", "fragile")):
        return "intimate_fragile"
    if any(token in signal for token in ("rising", "build", "longing", "yearn")):
        return "rising_longing"
    if any(token in signal for token in ("open", "belt", "peak", "climax", "hook")):
        return "open_throated_peak"
    if any(token in signal for token in ("energetic", "drive", "attack", "pulse", "beat")):
        return "energetic_hook"
    if any(token in signal for token in ("release", "afterimage", "afterglow", "resolve", "residue")):
        return "bittersweet_release"
    return "restrained_ache"


def _enforce_lip_sync_music_visual_canon(scene: ScenarioDirectorScene) -> None:
    scene.scene_purpose = "performance"
    scene.transition_type = "cut" if _safe_float(scene.time_start, 0.0) > 0.0 else "cold_open"
    if not str(scene.performance_framing or "").strip():
        scene.performance_framing = "tight_medium"
    scene.shot_type = _normalize_scene_shot_type_from_camera(scene)
    location = str(scene.location or "the same world location").strip()
    if not str(scene.action_in_frame or "").strip():
        scene.action_in_frame = (
            "Emotional singing performance with face and mouth clearly readable; phrase-driven expressive hand gestures, "
            "one-hand-to-chest / open-outward lyric phrasing, coordinated head-shoulder-hand accents, controlled upper-body pulse, "
            "small forward intention with minimal locomotion, and no running or chase action."
        )
    if not str(scene.camera or "").strip():
        scene.camera = _lip_sync_safe_camera_line()
    if not str(scene.viewer_hook or "").strip():
        scene.viewer_hook = (
            "Immediate face-readable emotional singing beat; keep expression and lyric articulation as the main focus."
        )
    if not str(scene.frame_description or "").strip():
        scene.frame_description = (
            f"Half-body or medium-close singer performance in {location}; environment supports mood in background."
        )
    scene.video_prompt = _join_visible_prompt_parts(
        [
            "Emotional singing performance in tight medium framing with persistent face/mouth readability.",
            "Allow phrase-driven hand acting and expressive upper-body performance: lyric-marking fingers/hands, one hand to chest, one hand open outward, controlled torso pulse, and coordinated head/shoulder/hand emphasis.",
            "Performance should feel alive, not frozen: permit small forward intention and subtle weight shift while preserving stable lipsync readability.",
            "LIP-SYNC PERFORMANCE RULES (STRICT): performer-first, face/mouth readability mandatory, eye line toward camera or near-camera preferred, emotional lyric delivery through face/shoulders/hands/subtle torso rhythm.",
            "Camera behavior is locked/smooth: static or very slow push-in with minimal drift; if orbit language appears, treat it as a gentle left/right side partial arc, not a flip/inversion/overhead orbit.",
            "CAMERA ORBIT SAFETY: if orbit is used, keep it as a slow horizontal arc around performer at eye/chest/waist level and keep subject readable.",
            "Keep horizon and vertical axis stable: camera remains upright and physically readable at all times.",
            "No upside-down framing, no full frame inversion, no vertical roll/barrel-roll, no top-over flip, no tumbling, no uncontrolled axial rotation.",
            "No full 360° orbit around performer, no aggressive circular chase camera, no fast rotational move around face/body, no top orbit, no overhead spin, no head-top camera circle, no drone-like loop over subject.",
            "No top-down rotation, no camera roll, no spinning around head, no aggressive zoom-out, no fast retreating camera, and no wide framing drift into generic dance floor shot.",
            "Subject may sway/turn for performance, but camera orientation stays controlled and upright.",
            "Garment continuity remains strict during performance: emotional gestures must not redesign sleeves, bodice, neckline, skirt silhouette, rose layout, lining visibility logic, or footwear identity.",
            "Optional safe variant: performer may gently rotate while camera stays upright and background shifts behind trajectory, preserving character integrity.",
            "Environment remains background support; no running/chasing/spinning as primary action, no chaotic background dance, and no crowd stealing focus.",
            build_ltx_video_canon_block(lip_sync=True),
        ]
    )
    scene.audio_emotion_direction = _derive_audio_emotion_direction(scene)
    emotional_tone = str(scene.audio_emotion_direction or scene.emotion or "").strip().lower()
    audio_evidence = str(scene.audio_anchor_evidence or scene.local_phrase or scene.what_from_audio_this_scene_uses or "").strip()
    emotion_direction = "pain/restraint/fragility with inward gaze and softer tension"
    if "open_throated_peak" in emotional_tone or "energetic_hook" in emotional_tone:
        emotion_direction = "drive/attack/forward intention with stronger jaw-open singing and stronger hand language"
    elif "bittersweet_release" in emotional_tone:
        emotion_direction = "softer breath, emotional residue, reduced force with still-readable singing"
    elif "intimate_fragile" in emotional_tone:
        emotion_direction = "intimate and softer but still mouth-ready singing-readable"
    elif "rising_longing" in emotional_tone:
        emotion_direction = "rising longing with increasing intention and readable emotional tension"
    image_lipsync_canon = _join_visible_prompt_parts(
        [
            "LIP-SYNC IMAGE CANON (STRICT): singer-performance-first still frame, not neutral mannequin portrait.",
            f"Audio emotion direction key: {scene.audio_emotion_direction}.",
            f"Audio-driven emotion direction: {emotion_direction}.",
            f"Audio anchor evidence: {audio_evidence or 'beat/phrase contour from scene timing'}.",
            "Capture mouth-ready singing moment with expressive eyes/brow tension and visible neck/shoulders/upper torso.",
            "Include hands when they improve performance readability; keep enough garment context for continuity.",
            "Prefer tight_medium / medium / three_quarter framing; avoid face-only neutral beauty crop and avoid distant full-body by default.",
            "Hard forbidden changes: do not change face identity; do not slim down/compress body; do not change hair color/structure; do not replace garment category;",
            "do not change neckline/silhouette/color/signature details; do not replace accessories/shoes; do not beautify into a different woman; do not convert into generic singer portrait.",
        ]
    )
    scene.image_prompt = _quality_filter_visible_prompt(
        _join_visible_prompt_parts(
            [
                _strip_video_only_semantics_from_image_prompt(build_ltx_visible_image_prompt(scene)),
                image_lipsync_canon,
            ]
        )
    )
    scene.video_prompt = _quality_filter_visible_prompt(scene.video_prompt)


def build_ltx_video_negative_prompt(scene: ScenarioDirectorScene | dict[str, Any] | None = None) -> str:
    shared_safety_floor = [
        "no extreme motion blur",
        "no broken anatomy",
        "no extra limbs",
        "no limb duplication",
        "no body deformation",
    ]
    lip_sync_route_negative = [
        "no overhead orbit",
        "no top-down rotation",
        "no camera roll",
        "no spinning around head",
        "no aggressive zoom out",
        "no fast retreating camera",
        "no chaotic background dance",
        "no crowd stealing focus",
        "no unreadable mouth",
        "no broken mouth articulation",
        "no face distortion",
        "no ghost hand on microphone",
    ]
    i2v_route_negative = [
        "no jerky dance",
        "no flailing arms",
        "no abrupt spins",
        "no violent head whipping",
        "no high-frequency shaking",
        "no overhead orbit",
        "no top orbit",
        "no head-top camera circle",
        "no drone-like loop",
        "no roll-tilt orbit",
        "no anatomy break",
        "no identity drift",
    ]
    ending_afterglow_negative = [
        "no abrupt cut feeling",
        "no chaotic outro movement",
        "no fast retreating camera",
        "no frantic background extras",
        "no random dance explosion",
        "no unreadable final pose",
    ]

    if not scene:
        return ", ".join(shared_safety_floor)

    if isinstance(scene, dict):
        resolved_route = str(
            scene.get("resolved_workflow_key")
            or scene.get("resolvedWorkflowKey")
            or scene.get("video_generation_route")
            or scene.get("videoGenerationRoute")
            or scene.get("planned_video_generation_route")
            or scene.get("plannedVideoGenerationRoute")
            or scene.get("ltx_mode")
            or scene.get("ltxMode")
            or ""
        ).strip().lower()
        transition_type = str(scene.get("transition_type") or scene.get("transitionType") or "").strip().lower()
        purpose = str(scene.get("scene_purpose") or scene.get("scenePurpose") or "").strip().lower()
        clip_arc_stage = str(scene.get("clip_arc_stage") or scene.get("clipArcStage") or "").strip().lower()
        lip_sync_flag = _coerce_bool(scene.get("lip_sync") if "lip_sync" in scene else scene.get("lipSync"), False)
    else:
        resolved_route = str(scene.resolved_workflow_key or scene.ltx_mode or "").strip().lower()
        transition_type = str(scene.transition_type or "").strip().lower()
        purpose = str(scene.scene_purpose or "").strip().lower()
        clip_arc_stage = str(scene.clip_arc_stage or "").strip().lower()
        lip_sync_flag = bool(scene.lip_sync)

    is_lip_sync_route = lip_sync_flag or resolved_route in {"lip_sync_music", "lip_sync"}
    is_ending_afterglow_scene = (
        purpose in {"outro", "ending", "afterglow", "payoff"}
        or clip_arc_stage in {"outro", "ending", "afterglow", "payoff", "afterimage_release"}
        or transition_type in {"ending_hold", "afterglow", "outro"}
    )

    route_block = lip_sync_route_negative if is_lip_sync_route else i2v_route_negative
    merged: list[str] = [*route_block, *shared_safety_floor]
    if is_ending_afterglow_scene:
        merged.extend(ending_afterglow_negative)

    deduped: list[str] = []
    seen: set[str] = set()
    for token in merged:
        normalized = str(token or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return ", ".join(deduped)


def _scene_requires_explicit_first_last_prompts(scene: ScenarioDirectorScene) -> bool:
    render_mode = str(scene.render_mode or "").strip().lower()
    ltx_mode = str(scene.ltx_mode or "").strip().lower()
    return bool(scene.needs_two_frames) or render_mode in {"first_last", "first_last_sound"} or ltx_mode in {"f_l", "first_last"}


def _derive_first_last_frame_prompts(
    scene: ScenarioDirectorScene,
    raw_scene: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    return build_ltx_visible_first_last_prompts(scene, raw_scene, payload=payload)


def _enhance_music_video_transition_language(scene: ScenarioDirectorScene) -> None:
    transition_kind = str(scene.transition_type or "").strip().lower()
    render_mode = str(scene.render_mode or "").strip().lower()
    purpose = str(scene.scene_purpose or "").strip().lower()
    is_transition_scene = transition_kind in {"state_shift", "continuation"} or render_mode in {"first_last", "first_last_sound"} or purpose == "transition"
    if not is_transition_scene:
        return
    if "visual bridge" not in scene.viewer_hook.lower():
        scene.viewer_hook = f"{scene.viewer_hook} Use this beat as a visual bridge into a new state.".strip()
    if "state change" not in scene.clip_decision_reason.lower():
        scene.clip_decision_reason = f"{scene.clip_decision_reason} state_change_bridge=true;"


def _build_music_video_clip_decision_reason(
    scene: ScenarioDirectorScene,
    *,
    shot_type: str,
    presence_type: str,
    vocal_presentation: str,
    performer_presentation: str,
    lip_sync_compatible: bool,
    lip_sync_compatibility_reason: str,
    forced_transition_scene: bool,
    auto_sound_workflow_enabled: bool,
) -> str:
    emphasis: list[str] = []
    if scene.scene_purpose == "hook":
        emphasis.append("hook")
    elif scene.scene_purpose == "payoff":
        emphasis.append("payoff")
    elif scene.scene_purpose == "ending_hold":
        emphasis.append("ending")
    if forced_transition_scene:
        emphasis.append("forced_first_last")
    emphasis_token = ",".join(emphasis) if emphasis else "none"
    transition_token = scene.transition_type
    if forced_transition_scene and transition_token != "state_shift":
        transition_token = f"{transition_token}(forced)"
    return (
        f"purpose={scene.scene_purpose}; render={scene.render_mode}; workflow={scene.resolved_workflow_key}; "
        f"shot={shot_type}; presence={presence_type}; framing={scene.performance_framing}; "
        f"transition={transition_token}; twoFrames={'true' if scene.needs_two_frames else 'false'}; "
        f"lipSync={'true' if scene.lip_sync else 'false'}; vocal={vocal_presentation}; performer={performer_presentation}; "
        f"compatibility={'true' if lip_sync_compatible else 'false'}({lip_sync_compatibility_reason}); "
        f"soundWorkflowAutoDisabled={'true' if not auto_sound_workflow_enabled else 'false'}; emphasis={emphasis_token}."
    )


def _infer_music_video_presence_type(
    scene: ScenarioDirectorScene,
    *,
    payload: dict[str, Any] | None = None,
    raw_scene: dict[str, Any] | None = None,
) -> str:
    active_roles = _collect_scene_active_character_roles(scene, payload=payload, raw_scene=raw_scene)
    if "character_1" in active_roles and "character_2" in active_roles:
        return "duet"
    if active_roles:
        return "solo"
    return "environment"


def _infer_music_video_style_tone(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> str:
    texts = [
        _scene_text_bundle(scene),
        str(payload.get("source", {}).get("text") or "") if isinstance(payload.get("source"), dict) else "",
        str(payload.get("director_note") or payload.get("directorNote") or ""),
    ]
    normalized = _normalize_lookup_text(" ".join(texts))
    if any(token in normalized for token in ("thriller", "detective", "chase", "pursuit", "stalker", "surveillance", "suspense", "noir", "триллер", "детектив", "преслед", "слежк", "напряж")):
        return "thriller_detective"
    if any(token in normalized for token in ("romance", "romantic", "tender", "love", "couple", "роман", "любов", "нежн")):
        return "romantic"
    return "neutral"


def _resolve_director_genre_intent(payload: dict[str, Any], scene: ScenarioDirectorScene | None = None) -> dict[str, str]:
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    director_note = str(controls.get("directorNote") or controls.get("director_note") or "").strip()
    scene_bundle = _scene_text_bundle(scene) if scene is not None else ""
    normalized = _normalize_lookup_text(f"{director_note} {scene_bundle}")
    if any(token in normalized for token in ("horror", "страш", "жут", "ужас", "dread", "terror")):
        return {
            "directorGenreIntent": "horror_dread",
            "directorGenreReason": "director_note_horror_dread_tokens_detected",
            "directorToneBias": "fear_pressure_unease",
        }
    if any(token in normalized for token in ("social drama", "социальн", "tragic", "траг", "injustice", "бедност", "потер", "утрат")):
        return {
            "directorGenreIntent": "tragic_social_drama",
            "directorGenreReason": "social_conflict_or_tragic_tokens_detected",
            "directorToneBias": "human_cost_social_weight",
        }
    return {
        "directorGenreIntent": "neutral_drama",
        "directorGenreReason": "no_explicit_horror_or_tragic_social_markers",
        "directorToneBias": "observational_emotional_realism",
    }


def _collect_scene_active_character_roles(
    scene: ScenarioDirectorScene,
    *,
    payload: dict[str, Any] | None = None,
    raw_scene: dict[str, Any] | None = None,
) -> list[str]:
    source_payload = payload if isinstance(payload, dict) else {}
    scene_payload = raw_scene if isinstance(raw_scene, dict) else _find_raw_scene_payload(scene, source_payload)

    def _normalized_character_role(role_value: Any) -> str:
        role = _normalize_scenario_role(role_value)
        return role if role in {"character_1", "character_2", "character_3"} else ""

    ordered: list[str] = []

    def _push_role(role_value: Any) -> None:
        normalized = _normalized_character_role(role_value)
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    def _push_list(values: Any) -> None:
        if not isinstance(values, list):
            return
        for role_value in values:
            _push_role(role_value)

    # priority: primary -> secondary/sceneActive -> explicit scene contracts -> actors/refs
    for key in ("primaryRole", "primary_role"):
        _push_role(scene_payload.get(key))

    for key in ("secondaryRoles", "secondary_roles", "sceneActiveRoles", "scene_active_roles"):
        _push_list(scene_payload.get(key))

    for key in ("mustAppear", "must_appear", "participants"):
        _push_list(scene_payload.get(key))

    for key in ("refsUsed", "refs_used"):
        value = scene_payload.get(key)
        if isinstance(value, dict):
            for role_key, role_value in value.items():
                if _coerce_bool(role_value, False) or role_value:
                    _push_role(role_key)
        else:
            _push_list(value)

    for map_key in ("refsUsedByRole", "refs_used_by_role", "refDirectives", "ref_directives"):
        map_value = scene_payload.get(map_key)
        if isinstance(map_value, dict):
            for role_key, role_value in map_value.items():
                if role_value:
                    _push_role(role_key)

    _push_list(scene.actors or [])

    return ordered


def _apply_music_video_role_influence(
    scene: ScenarioDirectorScene,
    *,
    payload: dict[str, Any],
    index: int,
    total: int,
    shot_type: str,
    scene_purpose: str,
    presence_type: str,
    performance_framing: str,
    transition_candidate: bool,
) -> dict[str, Any]:
    role_types, _, _ = _resolve_effective_role_type_by_role(payload)
    raw_scene = _find_raw_scene_payload(scene, payload)
    active_roles = _collect_scene_active_character_roles(scene, payload=payload, raw_scene=raw_scene)
    if not active_roles:
        return {"applied": False, "reason": "no_active_character_roles", "sceneRoleDynamics": "environment"}
    heroes = [role for role in active_roles if role_types.get(role) == "hero"]
    antagonists = [role for role in active_roles if role_types.get(role) == "antagonist"]
    supports = [role for role in active_roles if role_types.get(role) == "support"]
    tone = _infer_music_video_style_tone(scene, payload)
    reason: list[str] = []
    dynamics: list[str] = []
    final = {
        "shot_type": shot_type,
        "scene_purpose": scene_purpose,
        "presence_type": presence_type,
        "performance_framing": performance_framing,
        "transition_emphasis": "",
    }

    if len(heroes) >= 2:
        dynamics.append("shared_hero_duet")
        reason.append("two_heroes_shared_center")
        if not transition_candidate and scene_purpose in {"build", "performance"}:
            final["scene_purpose"] = "performance"
        if final["presence_type"] in {"duet", "solo"}:
            final["presence_type"] = "shared_duet"
        if final["shot_type"] not in {"duet_shared", "close_up"}:
            final["shot_type"] = "duet_shared"
        if final["performance_framing"] not in {"duet_frame", "face_close"}:
            final["performance_framing"] = "duet_frame"

    if heroes:
        dynamics.append("hero_anchor")
        if not transition_candidate and scene_purpose in {"build", "performance", "payoff", "ending_hold"}:
            if index in {1, max(0, total - 2), total - 1}:
                reason.append("hero_emotional_anchor_priority")
                if index == total - 1:
                    final["scene_purpose"] = "ending_hold"
                elif index == max(0, total - 2):
                    final["scene_purpose"] = "payoff"
                else:
                    final["scene_purpose"] = "performance"
                if final["shot_type"] in {"wide", "detail_insert"}:
                    final["shot_type"] = "close_up" if index != max(0, total - 2) else "medium"
                if final["performance_framing"] in {"non_performance", "wide_performance"}:
                    final["performance_framing"] = "face_close"

    if antagonists:
        dynamics.append("asymmetric_counter_presence")
        reason.append("antagonist_counter_presence")
        if final["presence_type"] == "duet":
            final["presence_type"] = "counter_presence"
        if tone == "thriller_detective":
            reason.append("thriller_detective_tension_weight")
            final["transition_emphasis"] = "watcher_pursuit_block"
            if final["shot_type"] in {"duet_shared", "close_up"}:
                final["shot_type"] = "medium" if not transition_candidate else final["shot_type"]
            if final["performance_framing"] == "duet_frame":
                final["performance_framing"] = "asymmetric_duet"
            if final["scene_purpose"] == "performance" and heroes and len(heroes) == 1:
                final["scene_purpose"] = "build"
        elif tone == "romantic":
            reason.append("romantic_soft_antagonist_not_neutral")
            if final["presence_type"] in {"duet", "shared_duet"}:
                final["presence_type"] = "counter_presence_soft"
        else:
            if final["performance_framing"] == "duet_frame":
                final["performance_framing"] = "asymmetric_duet"

    if supports and heroes:
        dynamics.append("support_secondary_presence")
        reason.append("support_kept_secondary_to_hero")
        if final["presence_type"] == "duet" and not antagonists:
            final["presence_type"] = "hero_with_support"
        if final["scene_purpose"] in {"payoff", "ending_hold"} and len(heroes) == 1:
            final["performance_framing"] = "face_close"

    if "character_2" in active_roles and "character_1" in active_roles:
        dynamics.append("duet_pair_protected")
    applied = bool(reason)
    return {
        "applied": applied,
        "reason": ";".join(reason) if reason else "role_defaults",
        "sceneRoleDynamics": ",".join(dynamics) if dynamics else "neutral",
        **final,
    }


def _extract_role_identity_markers(
    role: str,
    payload: dict[str, Any],
    *,
    raw_scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scene_payload = raw_scene if isinstance(raw_scene, dict) else {}
    texts = _collect_role_hint_texts(role, payload, scene_payload)
    texts.extend(_collect_text_fragments(scene_payload.get("identityHintsByRole")))
    texts.extend(_collect_text_fragments(scene_payload.get("identity_hints_by_role")))
    texts.extend(_collect_text_fragments(scene_payload.get("sceneIdentityHints")))
    texts.extend(_collect_text_fragments(scene_payload.get("scene_identity_hints")))
    normalized = _normalize_lookup_text(" ".join(texts))

    face_terms = [term for term in ("oval face", "angular", "jawline", "high cheekbone", "круглое лицо", "скулы", "челюст") if term in normalized][:3]
    hair_face_terms = [term for term in ("blonde", "brunette", "redhead", "short hair", "long hair", "curly", "beard", "mustache", "усы", "борода", "кудр", "коротк", "длинн", "светл", "темн") if term in normalized][:4]
    body_terms = [term for term in ("slim", "athletic", "curvy", "fuller", "heavy", "lean", "broad", "tall", "petite", "стройн", "полн", "крупн", "худощ", "атлет", "высок", "миниат") if term in normalized][:4]
    outfit_terms = [term for term in ("dress", "jacket", "coat", "hoodie", "armor", "uniform", "suit", "плать", "куртк", "пальто", "форма", "костюм") if term in normalized][:4]
    accessory_terms = [term for term in ("glasses", "earring", "necklace", "hat", "ring", "очк", "серьг", "кулон", "шляп", "кольц") if term in normalized][:3]
    age_gender_terms = [term for term in ("male", "female", "man", "woman", "young", "older", "муж", "жен", "девуш", "парень", "возраст") if term in normalized][:3]

    display_labels = payload.get("displayLabelByRole") if isinstance(payload.get("displayLabelByRole"), dict) else {}
    label = str(display_labels.get(role) or role).strip()
    return {
        "label": label,
        "face": face_terms,
        "hair_face": hair_face_terms,
        "body": body_terms,
        "outfit": outfit_terms,
        "accessories": accessory_terms,
        "age_gender": age_gender_terms,
    }


def _build_multi_character_identity_lock(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> dict[str, Any]:
    raw_scene = _find_raw_scene_payload(scene, payload)
    active_roles = _collect_scene_active_character_roles(scene, payload=payload, raw_scene=raw_scene)
    if len(active_roles) < 2:
        return {
            "enabled": False,
            "distinctCharacterSeparation": False,
            "identityLockByRole": {},
            "appearanceDriftRisk": "low_single_character",
            "duetLockEnabled": False,
            "duetCompositionMode": "single_focus",
            "secondaryRoleVisibilityRequirement": "none",
            "character2DriftGuard": "not_required",
            "duetIdentityContract": "",
            "contract": "",
        }
    lock_by_role = {role: _extract_role_identity_markers(role, payload, raw_scene=raw_scene) for role in active_roles}
    is_strict_duet = "character_1" in active_roles and "character_2" in active_roles
    identity_marker_count = 0
    for role in active_roles:
        markers = lock_by_role.get(role) or {}
        identity_marker_count += sum(len(markers.get(bucket) or []) for bucket in ("face", "hair_face", "body", "outfit", "accessories", "age_gender"))
    identity_marker_strength = min(1.0, identity_marker_count / max(4.0, len(active_roles) * 6.0))
    composition_strength = 0.9 if str(scene.shot_type or "") in {"duet_shared", "medium"} else 0.6
    if str(scene.performance_framing or "") in {"duet_frame", "asymmetric_duet"}:
        composition_strength = max(composition_strength, 0.95)
    must_appear = raw_scene.get("mustAppear") or raw_scene.get("must_appear") or raw_scene.get("participants") or []
    presence_contract_strength = 1.0 if len([r for r in must_appear if _normalize_scenario_role(r) in active_roles]) >= 2 else 0.55
    duet_lock_strength = 1.0 if is_strict_duet else 0.7
    active_role_count = len(active_roles)
    drift_score = (
        0.25 * min(1.0, active_role_count / 3.0)
        + 0.75 * (1.0 - (0.35 * duet_lock_strength + 0.25 * identity_marker_strength + 0.2 * composition_strength + 0.2 * presence_contract_strength))
    )
    if drift_score >= 0.7:
        risk = "high_character2_drift" if "character_2" in active_roles and duet_lock_strength < 0.75 else "high_multi_character_drift"
    elif drift_score >= 0.52:
        risk = "elevated_character2_drift" if "character_2" in active_roles else "elevated_multi_character_drift"
    elif drift_score >= 0.35:
        risk = "medium_multi_character_drift"
    else:
        risk = "low_locked_duet" if is_strict_duet else "low_multi_character_drift"
    clauses: list[str] = [
        "Distinct character separation is mandatory: keep every character visually unique in face, body silhouette, and outfit identity.",
        "Do not merge faces, do not average body types, do not transfer hairstyle/facial structure, and do not swap outfits between characters.",
        "Both active characters must stay on-screen when scene contract indicates duet/shared-presence/two-character framing.",
    ]
    for idx, role in enumerate(active_roles, start=1):
        markers = lock_by_role.get(role) or {}
        identity_bits = [f"slot {idx}={role} ({markers.get('label') or role})"]
        if markers.get("face"):
            identity_bits.append(f"face:{'/'.join(markers.get('face') or [])}")
        if markers.get("body"):
            identity_bits.append(f"body:{'/'.join(markers.get('body') or [])}")
        if markers.get("hair_face"):
            identity_bits.append(f"face-hair:{'/'.join(markers.get('hair_face') or [])}")
        if markers.get("outfit"):
            identity_bits.append(f"outfit:{'/'.join(markers.get('outfit') or [])}")
        if markers.get("accessories"):
            identity_bits.append(f"accessories:{'/'.join(markers.get('accessories') or [])}")
        if markers.get("age_gender"):
            identity_bits.append(f"age-gender:{'/'.join(markers.get('age_gender') or [])}")
        clauses.append(f"Preserve {'; '.join(identity_bits)}.")
    if "character_1" in active_roles and "character_2" in active_roles:
        clauses.append("character_2 must remain visibly distinct from character_1 and must never become a softened copy.")
    duet_contract = ""
    if is_strict_duet:
        duet_contract = (
            "DUET IDENTITY CONTRACT: keep character_1 and character_2 as two different human identities; "
            "never merge or average faces/bodies/clothing; preserve distinct face architecture, hair identity, silhouette, body build, and outfit silhouette; "
            "keep both active characters legible in one coherent frame; avoid twinization, face averaging, and clothing convergence."
        )
        clauses.append(duet_contract)
    return {
        "enabled": True,
        "distinctCharacterSeparation": True,
        "identityLockByRole": lock_by_role,
        "appearanceDriftRisk": risk,
        "duetLockEnabled": is_strict_duet,
        "duetCompositionMode": "hard_duet_split" if is_strict_duet else "multi_character_separation",
        "secondaryRoleVisibilityRequirement": "both_roles_legible_same_frame" if is_strict_duet else "secondary_roles_must_be_readable",
        "character2DriftGuard": (
            "strict_character2_not_softened_copy_of_character1"
            if is_strict_duet
            else ("soft_guard_for_character2" if "character_2" in active_roles else "not_required")
        ),
        "duetIdentityContract": duet_contract,
        "driftRiskInputs": {
            "activeRoleCount": active_role_count,
            "duetLockStrength": round(duet_lock_strength, 3),
            "identityMarkerStrength": round(identity_marker_strength, 3),
            "compositionStrength": round(composition_strength, 3),
            "presenceContractStrength": round(presence_contract_strength, 3),
            "driftScore": round(drift_score, 3),
        },
        "contract": " ".join(clauses),
    }


def _select_forced_music_video_transition_index(
    scenes: list[ScenarioDirectorScene],
    *,
    payload: dict[str, Any] | None = None,
) -> int | None:
    if len(scenes) < 5:
        return None
    candidate_indices = [idx for idx in range(1, len(scenes) - 1)]
    if not candidate_indices:
        return None
    scored: list[tuple[int, int]] = []
    for idx in candidate_indices:
        scene = scenes[idx]
        shot_type = _infer_music_video_shot_type(scene)
        raw_scene = _find_raw_scene_payload(scene, payload if isinstance(payload, dict) else {})
        presence = _infer_music_video_presence_type(scene, payload=payload, raw_scene=raw_scene)
        text_bundle = _scene_text_bundle(scene).lower()
        score = 0
        if shot_type == "duet_shared" or presence == "duet":
            score += 5
        if any(token in text_bundle for token in ("meet", "meeting", "touch", "take hand", "turn to each other", "walk together", "shared horizon")):
            score += 4
        if any(token in text_bundle for token in ("road", "highway", "path", "leave", "depart", "horizon", "into distance")):
            score += 3
        if "close" in shot_type or shot_type == "detail_insert":
            score += 1
        if idx >= len(scenes) // 2:
            score += 1
        scored.append((score, idx))
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_idx = scored[0]
    return best_idx if best_score > 0 else candidate_indices[0]


def _is_lipsync_voice_compatible(vocal_presentation: str, performer_presentation: str) -> tuple[bool, str]:
    vocal = str(vocal_presentation or "unknown").strip().lower() or "unknown"
    performer = str(performer_presentation or "unknown").strip().lower() or "unknown"
    if vocal == "unknown":
        return False, "unknown_vocal_presentation"
    if performer == "unknown":
        return False, "unknown_performer_presentation"
    if vocal == "male" and performer == "female":
        return False, "male_vocal_female_performer_conflict"
    if vocal == "female" and performer == "male":
        return False, "female_vocal_male_performer_conflict"
    if vocal == "mixed" or performer == "mixed":
        return False, "mixed_presentation_not_supported"
    if vocal == performer:
        return True, "compatible"
    return False, "incompatible_presentation"


def _extract_lip_sync_text(scene: ScenarioDirectorScene) -> str:
    if str(scene.local_phrase or "").strip():
        return str(scene.local_phrase or "").strip()
    fallback = str(scene.what_from_audio_this_scene_uses or scene.scene_goal or "").strip()
    if not fallback:
        return ""
    words = fallback.split()
    return " ".join(words[:8]).strip()


def _scene_has_character_subject(scene: ScenarioDirectorScene) -> bool:
    actors = {_normalize_scenario_role(actor) for actor in (scene.actors or [])}
    return any(role in {"character_1", "character_2", "character_3"} for role in actors)


def _scene_is_renderable_ltx_mode(scene: ScenarioDirectorScene) -> bool:
    return str(scene.ltx_mode or "").strip().lower() in {"i2v", "i2v_as", "f_l", "f_l_as", "continuation", "lip_sync", "lip_sync_music"}


def _is_environment_only_scene_contract(scene: ScenarioDirectorScene) -> bool:
    dynamics = str(scene.scene_role_dynamics or "").strip().lower()
    reason = str(scene.role_influence_reason or "").strip().lower()
    has_actors = any(str(actor).strip() for actor in (scene.actors or []))
    return (not has_actors) and (dynamics == "environment" or reason == "no_active_character_roles")


def _detect_music_video_scene_actor_candidates(
    scene: ScenarioDirectorScene,
    *,
    payload: dict[str, Any],
    raw_scene: dict[str, Any] | None = None,
) -> list[str]:
    candidates: list[str] = []
    for role in _collect_scene_active_character_roles(scene, payload=payload, raw_scene=raw_scene):
        if role in {"character_1", "character_2", "character_3"} and role not in candidates:
            candidates.append(role)
    for role in _detect_expected_character_roles(payload):
        if role in {"character_1", "character_2", "character_3"} and role not in candidates:
            candidates.append(role)
    if "character_1" not in candidates and _has_connected_ref_for_role(payload, "character_1"):
        candidates.insert(0, "character_1")
    if "character_1" not in candidates:
        candidates.insert(0, "character_1")
    return [role for role in candidates if role]


def _enforce_music_video_render_subject_contract(
    scene: ScenarioDirectorScene,
    *,
    payload: dict[str, Any],
    raw_scene: dict[str, Any] | None,
) -> bool:
    if not _scene_is_renderable_ltx_mode(scene):
        return True
    if _scene_has_character_subject(scene):
        return True
    actor_candidates = _detect_music_video_scene_actor_candidates(scene, payload=payload, raw_scene=raw_scene)
    if actor_candidates:
        scene.actors = [actor_candidates[0]]
    return _scene_has_character_subject(scene)


def _downgrade_to_environment_establishing_note(scene: ScenarioDirectorScene) -> None:
    scene.needs_two_frames = False
    scene.lip_sync = False
    scene.send_audio_to_generator = False
    scene.render_mode = "image_video"
    scene.resolved_workflow_key = "i2v"
    scene.resolved_workflow_file = CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY["i2v"]
    scene.ltx_mode = "i2v"
    scene.continuation_from_previous = False
    scene.start_frame_source = "new"
    scene.start_frame_prompt = ""
    scene.end_frame_prompt = ""


def _rollback_lipsync_to_i2v(scene: ScenarioDirectorScene, *, reason: str, downgrade_code: str) -> None:
    scene.lip_sync = False
    scene.render_mode = "image_video"
    scene.ltx_mode = "i2v"
    scene.send_audio_to_generator = False
    scene.lip_sync_text = ""
    scene.music_vocal_lipsync_allowed = False
    if str(scene.audio_slice_kind or "").strip().lower() == "music_vocal":
        scene.audio_slice_kind = "voice_only" if str(scene.local_phrase or "").strip() else "none"
    scene.workflow_decision_reason = reason
    scene.lip_sync_decision_reason = reason
    scene.resolved_workflow_key, scene.resolved_workflow_file = _resolve_workflow_key_and_file("i2v", fallback_key="i2v")
    _assign_video_route(
        scene,
        route="downgraded_to_i2v",
        planned_route="lip_sync_music",
        downgrade_code=str(downgrade_code or "music_vocal_lipsync_not_allowed"),
        downgrade_message=reason,
    )


def _assign_video_route(
    scene: ScenarioDirectorScene,
    *,
    route: str,
    planned_route: str | None = None,
    block_code: str = "",
    block_message: str = "",
    downgrade_code: str = "",
    downgrade_message: str = "",
) -> None:
    scene.video_generation_route = route
    scene.planned_video_generation_route = str(planned_route or route).strip().lower()
    scene.video_block_reason_code = block_code
    scene.video_block_reason_message = block_message
    scene.video_downgrade_reason_code = downgrade_code
    scene.video_downgrade_reason_message = downgrade_message
    scene.video_ready = route != "blocked"


def _scene_renderability_guard(scene: ScenarioDirectorScene, *, prior_scene_exists: bool) -> None:
    route = str(scene.video_generation_route or "").strip().lower() or "i2v"
    scene.video_ready = route != "blocked"
    if scene.video_block_reason_code:
        scene.video_generation_route = "blocked"
        scene.video_ready = False
        return
    if str(scene.resolved_workflow_key or "").strip().lower() not in {"i2v", "f_l", "lip_sync_music"}:
        _assign_video_route(
            scene,
            route="blocked",
            planned_route=scene.planned_video_generation_route or route,
            block_code="workflow_not_renderable",
            block_message="Resolved workflow key is not renderable for canonical clip generation.",
            downgrade_code=scene.video_downgrade_reason_code,
            downgrade_message=scene.video_downgrade_reason_message,
        )
        return
    if str(scene.ltx_mode or "").strip().lower() == "continuation" and not prior_scene_exists:
        _assign_video_route(
            scene,
            route="blocked",
            planned_route=scene.planned_video_generation_route or route,
            block_code="continuation_not_supported",
            block_message="Continuation mode requires a previous scene frame source.",
            downgrade_code=scene.video_downgrade_reason_code,
            downgrade_message=scene.video_downgrade_reason_message,
        )
        return
    if str(scene.render_mode or "").strip().lower() == "lip_sync_music":
        if str(scene.performer_presentation or "unknown").strip().lower() == "unknown":
            _assign_video_route(
                scene,
                route="blocked",
                planned_route=scene.planned_video_generation_route or "lip_sync_music",
                block_code="lip_sync_performer_presentation_unknown",
                block_message="Lip-sync route requires known performer presentation.",
                downgrade_code=scene.video_downgrade_reason_code or "lip_sync_performer_presentation_unknown",
                downgrade_message=scene.video_downgrade_reason_message or "Lip-sync candidate rejected because performer presentation is unknown.",
            )
            return
        if str(scene.vocal_presentation or "unknown").strip().lower() == "unknown":
            _assign_video_route(
                scene,
                route="blocked",
                planned_route=scene.planned_video_generation_route or "lip_sync_music",
                block_code="lip_sync_voice_presentation_unknown",
                block_message="Lip-sync route requires known vocal presentation.",
                downgrade_code=scene.video_downgrade_reason_code or "lip_sync_voice_presentation_unknown",
                downgrade_message=scene.video_downgrade_reason_message or "Lip-sync candidate rejected because vocal presentation is unknown.",
            )
            return
        if str(scene.lip_sync_voice_compatibility or "").strip().lower() != "compatible":
            _assign_video_route(
                scene,
                route="blocked",
                planned_route=scene.planned_video_generation_route or "lip_sync_music",
                block_code="lip_sync_voice_gender_mismatch",
                block_message="Lip-sync route blocked by voice/performer presentation mismatch.",
                downgrade_code=scene.video_downgrade_reason_code or "lip_sync_voice_gender_mismatch",
                downgrade_message=scene.video_downgrade_reason_message or "Lip-sync candidate rejected by voice/performer presentation mismatch.",
            )
            return
        if _safe_float(scene.audio_slice_end_sec, 0.0) <= _safe_float(scene.audio_slice_start_sec, 0.0):
            _assign_video_route(
                scene,
                route="blocked",
                planned_route=scene.planned_video_generation_route or "lip_sync_music",
                block_code="missing_audio_slice",
                block_message="Lip-sync scene requires a non-empty audio slice.",
                downgrade_code=scene.video_downgrade_reason_code,
                downgrade_message=scene.video_downgrade_reason_message,
            )
            return
        if str(scene.audio_slice_kind or "").strip().lower() != "music_vocal":
            _assign_video_route(
                scene,
                route="blocked",
                planned_route=scene.planned_video_generation_route or "lip_sync_music",
                block_code="audio_slice_not_music_vocal",
                block_message="Lip-sync route requires music_vocal audio slice kind.",
                downgrade_code=scene.video_downgrade_reason_code or "lip_sync_music_not_supported_for_slice",
                downgrade_message=scene.video_downgrade_reason_message or "Scene audio slice is not eligible for lip_sync_music.",
            )
            return
        if not scene.music_vocal_lipsync_allowed:
            _assign_video_route(
                scene,
                route="blocked",
                planned_route=scene.planned_video_generation_route or "lip_sync_music",
                block_code="music_vocal_lipsync_not_allowed",
                block_message="music_vocal_lipsync_allowed is false for this scene.",
                downgrade_code=scene.video_downgrade_reason_code or "lip_sync_music_not_supported_for_slice",
                downgrade_message=scene.video_downgrade_reason_message or "Lip-sync route blocked by compatibility policy.",
            )
            return
    if str(scene.render_mode or "").strip().lower() in {"first_last", "first_last_sound"}:
        if not str(scene.start_visual_state or "").strip() or not str(scene.end_visual_state or "").strip():
            _assign_video_route(
                scene,
                route="blocked",
                planned_route=scene.planned_video_generation_route or "f_l",
                block_code="missing_source_frames",
                block_message="first_last scene requires both start and end visual states.",
                downgrade_code=scene.video_downgrade_reason_code,
                downgrade_message=scene.video_downgrade_reason_message,
            )
            return


def _prevent_phrase_loop_in_music_video(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    if len(scenes) < 2:
        storyboard_out.diagnostics.phrase_loop_prevented = False
        storyboard_out.diagnostics.phrase_loop_prevented_reason = "insufficient_scenes_for_phrase_loop_guard"
        storyboard_out.diagnostics.scene_merge_or_reuse_reason = "insufficient_scenes_for_phrase_loop_guard"
        return storyboard_out
    merged_scenes: list[ScenarioDirectorScene] = [scenes[0]]
    merged_scenes[0].phrase_loop_similarity_with_prev = 0.0
    merged_scenes[0].phrase_loop_action = "keep"
    prevented = False
    merge_notes: list[str] = []
    storyboard_out.diagnostics.chorus_detected = _is_repeat_heavy_music_clip(scenes)

    def _is_near_repeat_phrase(left: str, right: str) -> bool:
        left_clean = re.sub(r"[^\w\s]", " ", str(left or "").lower())
        right_clean = re.sub(r"[^\w\s]", " ", str(right or "").lower())
        left_tokens = [token for token in left_clean.split() if token]
        right_tokens = [token for token in right_clean.split() if token]
        if not left_tokens or not right_tokens:
            return False
        left_set = set(left_tokens)
        right_set = set(right_tokens)
        overlap = len(left_set.intersection(right_set))
        union = max(1, len(left_set.union(right_set)))
        prefix_overlap = len([token for token in zip(left_tokens, right_tokens) if token[0] == token[1]])
        return (overlap / union) >= 0.72 or prefix_overlap >= max(2, min(len(left_tokens), len(right_tokens)) - 1)

    stop_tokens = {"a", "the", "and", "or", "to", "ti", "di", "e", "la", "il", "non", "mi", "si"}

    def _token_set(value: str) -> set[str]:
        clean = re.sub(r"[^\w\s]", " ", str(value or "").lower())
        return {token for token in clean.split() if token and token not in stop_tokens}

    def _jaccard(left: str, right: str) -> float:
        left_set = _token_set(left)
        right_set = _token_set(right)
        if not left_set and not right_set:
            return 1.0
        if not left_set or not right_set:
            return 0.0
        return len(left_set.intersection(right_set)) / max(1, len(left_set.union(right_set)))

    def _phrase_loop_similarity(left: ScenarioDirectorScene, right: ScenarioDirectorScene) -> float:
        weighted_pairs = [
            (str(left.scene_purpose or ""), str(right.scene_purpose or ""), 0.16),
            (str(left.beat_function or ""), str(right.beat_function or ""), 0.14),
            (str(left.clip_arc_stage or ""), str(right.clip_arc_stage or ""), 0.13),
            (str(left.local_phrase or ""), str(right.local_phrase or ""), 0.2),
            (str(left.visual_intensity_level or "") + " " + str(left.scene_goal or ""), str(right.visual_intensity_level or "") + " " + str(right.scene_goal or ""), 0.15),
            (str(left.camera or "") + " " + str(left.shot_type or ""), str(right.camera or "") + " " + str(right.shot_type or ""), 0.12),
            (str(left.location or "") + " " + str(left.frame_description or ""), str(right.location or "") + " " + str(right.frame_description or ""), 0.1),
        ]
        total = 0.0
        for left_value, right_value, weight in weighted_pairs:
            total += _jaccard(left_value, right_value) * weight
        return round(max(0.0, min(1.0, total)), 3)

    for scene in scenes[1:]:
        prev = merged_scenes[-1]
        phrase_prev = re.sub(r"\s+", " ", str(prev.local_phrase or "").strip().lower())
        phrase_cur = re.sub(r"\s+", " ", str(scene.local_phrase or "").strip().lower())
        repeated_phrase = bool(phrase_prev and phrase_cur and (phrase_prev == phrase_cur or _is_near_repeat_phrase(phrase_prev, phrase_cur)))
        similarity = _phrase_loop_similarity(prev, scene)
        scene.phrase_loop_similarity_with_prev = similarity
        if not repeated_phrase and similarity < 0.78:
            scene.phrase_loop_action = "keep"
            merged_scenes.append(scene)
            continue
        compared_fields = [
            "clip_arc_stage",
            "beat_function",
            "visual_intensity_level",
            "crowd_relation_state",
            "transition_family",
            "shot_type",
            "camera",
            "location",
            "performance_phase",
        ]
        same_profile = all(str(getattr(prev, key, "") or "").strip().lower() == str(getattr(scene, key, "") or "").strip().lower() for key in compared_fields)
        if not same_profile:
            scene.phrase_loop_action = "reframe"
            scene.progression_reason = "phrase_loop_reframed_progression_changed_camera_or_phase"
            if str(scene.performance_phase or "").strip().lower() == str(prev.performance_phase or "").strip().lower():
                scene.performance_phase = "growth" if str(scene.performance_phase or "").strip().lower() != "growth" else "climax"
            if str(scene.transition_family or "").strip().lower() in {"", "cut"}:
                scene.transition_family = "contrast_reframe"
            if str(scene.visual_intensity_level or "").strip().lower() == str(prev.visual_intensity_level or "").strip().lower():
                scene.visual_intensity_level = "high" if str(prev.visual_intensity_level or "").strip().lower() != "high" else "medium"
            merge_notes.append(f"repeated_phrase_reframed:{scene.scene_id}:functional_layer_shift")
            prevented = True
            merged_scenes.append(scene)
            continue
        scene.phrase_loop_action = "merge"
        prev.time_end = round(max(_safe_float(prev.time_end, 0.0), _safe_float(scene.time_end, 0.0)), 3)
        prev.duration = round(max(0.0, prev.time_end - _safe_float(prev.time_start, 0.0)), 3)
        prev.requested_duration_sec = prev.duration
        prev.audio_slice_end_sec = round(max(_safe_float(prev.audio_slice_end_sec, 0.0), _safe_float(scene.audio_slice_end_sec, 0.0)), 3)
        prev.audio_slice_expected_duration_sec = round(max(0.0, prev.audio_slice_end_sec - _safe_float(prev.audio_slice_start_sec, 0.0)), 3)
        prev.actors = list(dict.fromkeys([*(prev.actors or []), *(scene.actors or [])]))
        prev.video_downgrade_reason_code = prev.video_downgrade_reason_code or "phrase_loop_merged"
        prev.video_downgrade_reason_message = prev.video_downgrade_reason_message or "Repeated lyric phrase merged into previous beat to preserve visual arc progression."
        prevented = True
        merge_notes.append(f"merged_repeated_phrase:{scene.scene_id}->{prev.scene_id}:near_repeat_chorus")
    storyboard_out.scenes = merged_scenes
    storyboard_out.diagnostics.phrase_loop_prevented = prevented
    storyboard_out.diagnostics.phrase_loop_prevented_reason = (
        "duplicate_lyrical_beats_merged_or_reframed" if prevented else "no_high_overlap_neighbor_duplicates"
    )
    storyboard_out.diagnostics.scene_merge_or_reuse_reason = "; ".join(merge_notes[:8]) if merge_notes else "visual_arc_policy_applied_no_merge_needed"
    return storyboard_out


def _strengthen_first_last_candidate(scene: ScenarioDirectorScene) -> ScenarioDirectorScene:
    text_blob = " ".join(
        [
            str(scene.scene_goal or ""),
            str(scene.frame_description or ""),
            str(scene.action_in_frame or ""),
            str(scene.workflow_decision_reason or ""),
            str(scene.clip_decision_reason or ""),
            str(scene.transition_family or ""),
        ]
    ).lower()
    trigger_tokens = ("world shift", "reveal", "release", "afterimage", "scale", "escalat", "camera distance", "pose shift", "gaze shift", "approach", "exit")
    if not any(token in text_blob for token in trigger_tokens):
        return scene
    if not str(scene.start_visual_state or "").strip():
        scene.start_visual_state = str(scene.frame_description or scene.scene_goal or "phase A: initial visual state").strip()
    if not str(scene.end_visual_state or "").strip():
        scene.end_visual_state = f"phase B: {str(scene.action_in_frame or scene.scene_goal or 'resolved visual state').strip()}"
    if not scene.delta_axes:
        axes: list[str] = []
        if any(token in text_blob for token in ("world shift", "reveal", "afterimage")):
            axes.append("world_context_shift")
        if any(token in text_blob for token in ("scale", "wide", "close", "camera")):
            axes.append("camera_language_shift")
        if any(token in text_blob for token in ("emotion", "release", "escalat")):
            axes.append("emotion_intensity_shift")
        scene.delta_axes = axes or ["visual_state_shift"]
    return scene


def _get_clip_formula_target(total_duration_sec: float) -> dict[str, int]:
    duration = _safe_float(total_duration_sec, 0.0)
    if duration <= 15.0:
        return {"lip_sync_music": 1, "f_l": 1, "i2v_min": 2, "i2v_max": 2}
    if duration <= 22.0:
        return {"lip_sync_music": 1, "f_l": 1, "i2v_min": 3, "i2v_max": 4}
    return {"lip_sync_music": 2, "f_l": 2, "i2v_min": 3, "i2v_max": 4}


def evaluateFirstLastEligibility(scene: ScenarioDirectorScene, context: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = context or {}
    scene = _strengthen_first_last_candidate(scene)
    weak_terms = ("emotion grows", "camera moves", "scene continues", "slight change", "same", "continues")
    reasons: list[str] = []
    weak_reasons: list[str] = []
    delta_axes_used: list[str] = []
    duration = _safe_float(scene.duration, _safe_float(scene.time_end, 0.0) - _safe_float(scene.time_start, 0.0))
    start_state = str(scene.start_visual_state or "").strip()
    end_state = str(scene.end_visual_state or "").strip()
    strong_start_state = bool(start_state and len(start_state.split()) >= 4)
    strong_end_state = bool(end_state and len(end_state.split()) >= 4)
    if _is_lip_sync_music_scene(scene):
        weak_reasons.append("scene_is_lip_sync_music")
    if str(scene.scene_purpose or "").strip().lower() in {"performance", "chorus_hit"} and str(scene.transition_family or "").strip().lower() in {"", "hold", "none", "static"}:
        weak_reasons.append("pure_static_performance_beat")
    if duration < 2.2:
        weak_reasons.append("duration_too_short_for_visible_transition")
    if not strong_start_state:
        weak_reasons.append("missing_strong_start_visual_state")
    if not strong_end_state:
        weak_reasons.append("missing_strong_end_visual_state")

    start_lower = start_state.lower()
    end_lower = end_state.lower()
    if start_lower and end_lower and (start_lower == end_lower or _safe_phrase_overlap(start_lower, end_lower) >= 0.86):
        weak_reasons.append("start_end_semantically_too_similar")
    if any(token in start_lower or token in end_lower for token in weak_terms):
        weak_reasons.append("weak_generic_state_wording")

    axis_hints = {
        "camera_distance_shift": ("close", "wide", "distance", "push in", "pull back", "camera distance"),
        "scale_shift": ("scale", "tiny", "vast", "small", "large"),
        "pose_phase_shift": ("pose", "turns", "kneels", "stands", "falls", "rises"),
        "gaze_direction_shift": ("gaze", "looks", "stares", "away", "toward"),
        "movement_state_shift": ("still", "motion", "running", "frozen", "release"),
        "environment_state_shift": ("room", "street", "rain", "fire", "light", "world", "door"),
        "silhouette_reveal_shift": ("silhouette", "reveal", "approach", "exit", "afterimage", "open", "close"),
        "compression_expansion_shift": ("compression", "expansion", "squeeze", "expand"),
        "solitude_openness_shift": ("alone", "crowd", "open", "empty"),
        "static_motion_release_shift": ("static", "locked", "burst", "release"),
    }
    combined_blob = " ".join(
        [
            start_lower,
            end_lower,
            str(scene.transition_family or "").strip().lower(),
            " ".join(str(item).strip().lower() for item in (scene.delta_axes or [])),
            str(scene.camera or "").strip().lower(),
            str(scene.frame_description or "").strip().lower(),
            str(scene.action_in_frame or "").strip().lower(),
        ]
    )
    for axis_name, hints in axis_hints.items():
        if any(hint in combined_blob for hint in hints):
            delta_axes_used.append(axis_name)
    if _scene_has_transition_evidence(scene):
        reasons.append("transition_evidence_detected")
    if delta_axes_used:
        reasons.append("multi_axis_visual_delta")
    strong_visual_delta = len(set(delta_axes_used)) >= 2 and "start_end_semantically_too_similar" not in weak_reasons
    if not strong_visual_delta:
        weak_reasons.append("explicit_visual_delta_not_strong_enough")
    else:
        reasons.append("strong_visual_delta_confirmed")

    score = max(0.0, min(1.0, (len(set(delta_axes_used)) * 0.2) + (0.2 if _scene_has_transition_evidence(scene) else 0.0) + (0.2 if strong_start_state and strong_end_state else 0.0) - (0.15 * len(set(weak_reasons)))))
    eligible = not weak_reasons and strong_visual_delta
    return {
        "eligible": eligible,
        "score": round(score, 3),
        "reasons": reasons,
        "weakReasons": weak_reasons,
        "deltaAxesUsed": sorted(set(delta_axes_used)),
        "strongStartState": strong_start_state,
        "strongEndState": strong_end_state,
        "strongVisualDelta": strong_visual_delta,
    }


def _safe_phrase_overlap(left: str, right: str) -> float:
    left_tokens = {token for token in re.findall(r"\w+", str(left or "").lower()) if len(token) > 2}
    right_tokens = {token for token in re.findall(r"\w+", str(right or "").lower()) if len(token) > 2}
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / max(1, len(left_tokens.union(right_tokens)))


def _scene_is_lip_sync_eligible(scene: ScenarioDirectorScene, *, is_clip_single_character: bool) -> tuple[bool, str]:
    if not _scene_has_human_performer(scene) or not _scene_has_character_subject(scene):
        return False, "lip_sync_skip_no_human_performer"
    if str(scene.scene_purpose or "").strip().lower() in {"transition", "reveal"}:
        return False, "lip_sync_skip_transition_scene"
    if not str(scene.local_phrase or "").strip() and not _scene_has_lip_sync_signal(scene):
        return False, "lip_sync_skip_no_phrase"
    duration = _safe_float(scene.duration, _safe_float(scene.time_end, 0.0) - _safe_float(scene.time_start, 0.0))
    if duration < 3.0 or duration > 7.0:
        return False, "lip_sync_skip_duration_out_of_range"
    if str(scene.shot_type or "").strip().lower() not in {"close_up", "medium", "portrait"} and str(scene.performance_framing or "").strip().lower() not in {"face_close", "close_performance", "tight_medium", "medium_close"}:
        return False, "lip_sync_skip_no_face_framing"
    if str(scene.scene_purpose or "").strip().lower() not in {"performance", "emotional_claim", "chorus_hit", "vocal_emphasis", "hook", "build", "payoff"}:
        return False, "lip_sync_skip_scene_purpose_mismatch"
    compatible, compatibility_reason = _is_lipsync_voice_compatible(scene.vocal_presentation, scene.performer_presentation)
    audio_sendable = str(scene.audio_slice_kind or "").strip().lower() == "music_vocal" and scene.music_vocal_lipsync_allowed
    single_performer_scene = (
        len([actor for actor in (scene.actors or []) if str(actor).strip().startswith("character_")]) == 1
        and str(scene.performer_presentation or "").strip().lower() in {"male", "female"}
    )
    if compatible:
        scene.lip_sync_voice_compatibility = "compatible"
        scene.lip_sync_voice_compatibility_reason = compatibility_reason
        if audio_sendable:
            return True, "lip_sync_eligible"
        if is_clip_single_character and single_performer_scene:
            return True, "lip_sync_eligible_single_performer_clip_fallback"
        return False, "lip_sync_skip_audio_not_sendable"
    if compatibility_reason == "unknown_vocal_presentation" and is_clip_single_character and single_performer_scene:
        scene.lip_sync_voice_compatibility = "compatible"
        scene.lip_sync_voice_compatibility_reason = "single_performer_clip_fallback"
        return True, "lip_sync_eligible_single_performer_clip_fallback"
    scene.lip_sync_voice_compatibility = "incompatible"
    scene.lip_sync_voice_compatibility_reason = compatibility_reason
    if compatibility_reason == "unknown_vocal_presentation":
        return False, "lip_sync_skip_unknown_and_no_single_performer_fallback"
    return False, "lip_sync_skip_voice_performer_conflict"


def _lip_sync_candidate_score(scene: ScenarioDirectorScene) -> int:
    score = 0
    if str(scene.local_phrase or "").strip():
        score += 2
    if _scene_has_lip_sync_signal(scene):
        score += 1
    if str(scene.shot_type or "").strip().lower() in {"close_up", "portrait"}:
        score += 1
    if str(scene.performance_framing or "").strip().lower() in {"face_close", "close_performance", "tight_medium", "medium_close"}:
        score += 1
    return score


def _first_last_candidate_score(scene: ScenarioDirectorScene) -> int:
    return int(round(max(0.0, min(1.0, _safe_float(scene.first_last_candidate_score, 0.0))) * 100))


def _clip_formula_actual_counts(scenes: list[ScenarioDirectorScene], target: dict[str, int]) -> dict[str, Any]:
    lip = sum(1 for s in scenes if s.resolved_workflow_key == "lip_sync_music")
    fl = sum(1 for s in scenes if s.resolved_workflow_key == "f_l")
    i2v = sum(1 for s in scenes if s.resolved_workflow_key == "i2v")
    i2v_min = int(target.get("i2v_min", 0))
    i2v_max = int(target.get("i2v_max", 0))
    return {
        "lip_sync_music": lip,
        "f_l": fl,
        "i2v": i2v,
        "i2v_in_target_range": i2v_min <= i2v <= i2v_max if i2v_max >= i2v_min else True,
    }


def _apply_scene_route(scene: ScenarioDirectorScene, *, route: str, reason: str) -> None:
    fallback_audio_send_reason = "lip_sync_audio_send_enabled_by_clip_fallback"
    lip_sync_selected_reason = "lip_sync_scene_audio_slice_selected"
    fallback_lip_sync_eligibility = "lip_sync_eligible_single_performer_clip_fallback"

    if route == "lip_sync_music":
        scene.render_mode = "lip_sync_music"
        scene.needs_two_frames = False
        scene.ltx_mode = "lip_sync_music"
        scene.lip_sync = True
        scene.send_audio_to_generator = True
        scene.video_downgrade_reason_code = ""
        scene.video_downgrade_reason_message = ""
        if str(scene.lip_sync_decision_reason or "").strip().lower() == fallback_lip_sync_eligibility:
            scene.audio_slice_kind = "music_vocal"
            scene.music_vocal_lipsync_allowed = True
            scene.audio_slice_decision_reason = fallback_audio_send_reason
        else:
            scene.audio_slice_decision_reason = lip_sync_selected_reason
        scene.resolved_workflow_key, scene.resolved_workflow_file = _resolve_workflow_key_and_file("lip_sync_music", fallback_key="lip_sync_music")
        scene.lip_sync_text = _extract_lip_sync_text(scene)
        _assign_video_route(scene, route="lip_sync_music", planned_route="lip_sync_music")
    elif route == "f_l":
        scene.render_mode = "first_last"
        scene.needs_two_frames = True
        scene.ltx_mode = "f_l"
        scene.lip_sync = False
        scene.lip_sync_text = ""
        scene.send_audio_to_generator = False
        if str(scene.lip_sync_decision_reason or "").strip().lower() == fallback_lip_sync_eligibility:
            scene.audio_slice_kind = ""
            scene.music_vocal_lipsync_allowed = False
        if str(scene.audio_slice_decision_reason or "").strip().lower() in {fallback_audio_send_reason, lip_sync_selected_reason}:
            scene.audio_slice_decision_reason = ""
        scene.resolved_workflow_key, scene.resolved_workflow_file = _resolve_workflow_key_and_file("f_l", fallback_key="f_l")
        _assign_video_route(scene, route="f_l", planned_route="f_l")
    else:
        scene.render_mode = "image_video"
        scene.needs_two_frames = False
        if str(scene.ltx_mode or "").strip().lower() not in {"continuation"}:
            scene.ltx_mode = "i2v"
        scene.lip_sync = False
        scene.lip_sync_text = ""
        scene.send_audio_to_generator = False
        if str(scene.lip_sync_decision_reason or "").strip().lower() == fallback_lip_sync_eligibility:
            scene.audio_slice_kind = ""
            scene.music_vocal_lipsync_allowed = False
        if str(scene.audio_slice_decision_reason or "").strip().lower() in {fallback_audio_send_reason, lip_sync_selected_reason}:
            scene.audio_slice_decision_reason = ""
        scene.resolved_workflow_key, scene.resolved_workflow_file = _resolve_workflow_key_and_file("i2v", fallback_key="i2v")
        _assign_video_route(scene, route="i2v", planned_route="i2v")
    scene.workflow_decision_reason = reason


def _rebalance_music_video_formula(
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    audio_duration_sec: float,
    lip_sync_candidates: list[ScenarioDirectorScene],
    first_last_candidates: list[ScenarioDirectorScene],
) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    diagnostics = storyboard_out.diagnostics
    target = _get_clip_formula_target(audio_duration_sec)
    diagnostics.clip_formula_target = target
    if not scenes:
        diagnostics.clip_formula_actual = {"lip_sync_music": 0, "f_l": 0, "i2v": 0, "i2v_in_target_range": False}
        diagnostics.clip_formula_rebalance_applied = False
        diagnostics.clip_formula_rebalance_detected_need = False
        diagnostics.clip_formula_rebalance_notes = ["no_scenes_for_formula_rebalance"]
        return storyboard_out
    notes: list[str] = []
    lip_target = int(target.get("lip_sync_music", 0))
    fl_target = int(target.get("f_l", 0))
    sorted_lip = sorted(lip_sync_candidates, key=_lip_sync_candidate_score, reverse=True)
    sorted_fl = sorted(first_last_candidates, key=_first_last_candidate_score, reverse=True)
    strong_fl_candidate_ids = {scene.scene_id for scene in sorted_fl}
    diagnostics.first_last_shortage_reason = ""
    selected_lip_ids = {scene.scene_id for scene in sorted_lip[:lip_target]}
    selected_fl_ids: set[str] = set()
    for scene in sorted_fl:
        if len(selected_fl_ids) >= fl_target:
            break
        if scene.scene_id in selected_lip_ids:
            continue
        selected_fl_ids.add(scene.scene_id)

    if lip_target > len(sorted_lip):
        notes.append("insufficient_lip_sync_music_candidates_after_eligibility_pass")
    if fl_target > len(selected_fl_ids):
        notes.append("insufficient_f_l_candidates_after_eligibility_pass")
        diagnostics.first_last_shortage_reason = f"strong_first_last_candidates={len(sorted_fl)} target={fl_target}"
        for scene in scenes:
            if len(selected_fl_ids) >= fl_target:
                break
            if scene.scene_id in selected_lip_ids or scene.scene_id in selected_fl_ids:
                continue
            strengthened = _strengthen_first_last_candidate(scene)
            eligibility = evaluateFirstLastEligibility(strengthened, context={"rebalance": "strengthen_attempt"})
            if not eligibility.get("eligible"):
                continue
            scene.first_last_candidate = True
            scene.first_last_candidate_score = _safe_float(eligibility.get("score"), 0.0)
            scene.first_last_candidate_reasons = [str(item).strip() for item in (eligibility.get("reasons") or []) if str(item).strip()]
            scene.first_last_reject_reasons = [str(item).strip() for item in (eligibility.get("weakReasons") or []) if str(item).strip()]
            scene.strong_visual_delta = _coerce_bool(eligibility.get("strongVisualDelta"), False)
            scene.route_before_rebalance = "f_l_candidate_after_strengthen"
            selected_fl_ids.add(scene.scene_id)
            strong_fl_candidate_ids.add(scene.scene_id)
            notes.append(f"rebalance_strengthened_f_l:{scene.scene_id}")

    for scene in scenes:
        scene.route_after_rebalance = ""
        if scene.scene_id in selected_lip_ids:
            _apply_scene_route(scene, route="lip_sync_music", reason="Deterministic clip router selected lip_sync_music after eligibility rebalance.")
            scene.route_after_rebalance = "lip_sync_music"
            notes.append(f"selected_lip_sync_music:{scene.scene_id}")
            continue
        if scene.scene_id in selected_fl_ids:
            _apply_scene_route(scene, route="f_l", reason="Deterministic clip router selected first_last after eligibility rebalance.")
            scene.route_after_rebalance = "f_l"
            notes.append(f"selected_f_l:{scene.scene_id}")
            continue
        _apply_scene_route(scene, route="i2v", reason="default_clip_i2v_route")
        scene.route_after_rebalance = "i2v"
        notes.append(f"selected_i2v:{scene.scene_id}")

    diagnostics.clip_formula_actual = _clip_formula_actual_counts(scenes, target)
    diagnostics.clip_formula_rebalance_detected_need = bool(
        diagnostics.clip_formula_actual.get("lip_sync_music", 0) != lip_target
        or diagnostics.clip_formula_actual.get("f_l", 0) != fl_target
    )
    diagnostics.strong_first_last_candidate_count = len(strong_fl_candidate_ids)
    if diagnostics.clip_formula_actual.get("f_l", 0) >= fl_target:
        diagnostics.first_last_shortage_reason = ""
    elif not diagnostics.first_last_shortage_reason:
        diagnostics.first_last_shortage_reason = f"strong_first_last_candidates={len(strong_fl_candidate_ids)} target={fl_target}"
    diagnostics.clip_formula_rebalance_applied = bool(selected_lip_ids or selected_fl_ids)
    diagnostics.clip_formula_rebalance_notes = notes or ["rebalance_not_needed"]
    return storyboard_out


def _route_clip_video_models(
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    payload: dict[str, Any],
    audio_duration_sec: float,
) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    diagnostics = storyboard_out.diagnostics
    diagnostics.clip_formula_target = _get_clip_formula_target(audio_duration_sec)
    if not scenes:
        diagnostics.clip_formula_actual = {"lip_sync_music": 0, "f_l": 0, "i2v": 0, "i2v_in_target_range": False}
        diagnostics.clip_formula_rebalance_applied = False
        diagnostics.clip_formula_rebalance_notes = ["no_scenes_for_clip_router"]
        return storyboard_out
    active_roles = _collect_active_connected_character_roles(payload)
    is_clip_single_character = len(active_roles) <= 1
    lip_sync_candidates: list[ScenarioDirectorScene] = []
    first_last_candidates: list[ScenarioDirectorScene] = []
    for scene in scenes:
        scene.route_before_rebalance = "i2v"
        scene.route_after_rebalance = ""
        scene.first_last_candidate = False
        scene.first_last_candidate_score = 0.0
        scene.first_last_candidate_reasons = []
        scene.first_last_reject_reasons = []
        scene.strong_visual_delta = False
        lip_ok, lip_reason = _scene_is_lip_sync_eligible(scene, is_clip_single_character=is_clip_single_character)
        scene.lip_sync_decision_reason = lip_reason
        if lip_ok:
            scene.route_before_rebalance = "lip_sync_music"
            lip_sync_candidates.append(scene)
            continue
        fl_eval = evaluateFirstLastEligibility(scene, context={"is_clip_single_character": is_clip_single_character})
        scene.first_last_candidate = bool(fl_eval.get("eligible"))
        scene.first_last_candidate_score = _safe_float(fl_eval.get("score"), 0.0)
        scene.first_last_candidate_reasons = [str(item).strip() for item in (fl_eval.get("reasons") or []) if str(item).strip()]
        scene.first_last_reject_reasons = [str(item).strip() for item in (fl_eval.get("weakReasons") or []) if str(item).strip()]
        scene.strong_visual_delta = _coerce_bool(fl_eval.get("strongVisualDelta"), False)
        if scene.first_last_candidate:
            scene.route_before_rebalance = "f_l"
            first_last_candidates.append(scene)
            scene.video_downgrade_reason_code = ""
            scene.video_downgrade_reason_message = ""
            scene.workflow_decision_reason = "strong_first_last_candidate"
            continue
        scene.route_before_rebalance = "i2v"
        scene.video_downgrade_reason_code = ""
        scene.video_downgrade_reason_message = ""
        scene.workflow_decision_reason = "f_l_skip_strong_eligibility_failed"
    storyboard_out.scenes = scenes
    return _rebalance_music_video_formula(
        storyboard_out,
        audio_duration_sec=audio_duration_sec,
        lip_sync_candidates=lip_sync_candidates,
        first_last_candidates=first_last_candidates,
    )


def _enforce_music_video_clip_formula(
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    payload: dict[str, Any],
    audio_duration_sec: float,
) -> ScenarioDirectorStoryboardOut:
    source_mode = str(payload.get("sourceMode") or payload.get("source_mode") or "").strip().lower()
    if source_mode and source_mode != "audio":
        storyboard_out.diagnostics.clip_formula_target = _get_clip_formula_target(audio_duration_sec)
        storyboard_out.diagnostics.clip_formula_actual = _clip_formula_actual_counts(
            storyboard_out.scenes or [],
            storyboard_out.diagnostics.clip_formula_target,
        )
        storyboard_out.diagnostics.clip_formula_rebalance_applied = False
        storyboard_out.diagnostics.clip_formula_rebalance_notes = ["clip_router_skipped_non_audio_source_mode"]
        return storyboard_out
    if not (0.0 < _safe_float(audio_duration_sec, 0.0) <= 35.0):
        storyboard_out.diagnostics.clip_formula_target = _get_clip_formula_target(audio_duration_sec)
        storyboard_out.diagnostics.clip_formula_actual = _clip_formula_actual_counts(
            storyboard_out.scenes or [],
            storyboard_out.diagnostics.clip_formula_target,
        )
        storyboard_out.diagnostics.clip_formula_rebalance_applied = False
        storyboard_out.diagnostics.clip_formula_rebalance_notes = ["clip_router_skipped_outside_0_35_sec_window"]
        return storyboard_out
    return _route_clip_video_models(storyboard_out, payload=payload, audio_duration_sec=audio_duration_sec)


def _apply_music_video_mode_policy(
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    content_type_policy: dict[str, Any],
    payload: dict[str, Any],
    audio_duration_sec: float | None = None,
) -> ScenarioDirectorStoryboardOut:
    clip_arc_stages = ["hook_entry", "expansion", "inner_turn", "power_return", "afterimage_release"]
    beat_function_by_stage = {
        "hook_entry": "entry_hook",
        "expansion": "performance_growth",
        "inner_turn": "emotional_dip_or_suspension",
        "power_return": "climax_return",
        "afterimage_release": "release_afterimage",
    }
    performance_phase_by_stage = {
        "hook_entry": "entry",
        "expansion": "growth",
        "inner_turn": "dip",
        "power_return": "climax",
        "afterimage_release": "release",
    }
    scenes = storyboard_out.scenes or []
    if not scenes:
        return storyboard_out
    storyboard_out = _maybe_split_final_hybrid_outro_scene(storyboard_out)
    scenes = storyboard_out.scenes or []
    has_existing_first_last = any(
        str(scene.render_mode or "").strip().lower() in {"first_last", "first_last_sound"} or _coerce_bool(scene.needs_two_frames, False)
        for scene in scenes
    )
    forced_first_last_index: int | None = None
    if len(scenes) >= 5 and not has_existing_first_last:
        forced_first_last_index = _select_forced_music_video_transition_index(scenes, payload=payload)
    repeat_heavy_clip = _is_repeat_heavy_music_clip(scenes)
    target_lip_sync = 2 if 25.0 <= _safe_float(audio_duration_sec, 0.0) <= 35.0 else max(1, min(3, len(scenes) // 3 or 1))
    max_lip_sync = max(1, min(target_lip_sync, 3))
    lip_sync_used = 0
    prev_lip_sync = False
    prev_two_frames = False
    prev_shot_type = ""
    kept_scenes: list[ScenarioDirectorScene] = []
    for index, scene in enumerate(scenes):
        shot_type = _infer_music_video_shot_type(scene)
        raw_scene = _find_raw_scene_payload(scene, payload)
        scene.audio_slice_kind = str(
            scene.audio_slice_kind
            or (raw_scene.get("audioSliceKind") if isinstance(raw_scene, dict) else "")
            or (raw_scene.get("audio_slice_kind") if isinstance(raw_scene, dict) else "")
            or ("music_vocal" if _is_music_vocal_mode(scene.narration_mode) else ("voice_only" if str(scene.local_phrase or "").strip() else "none"))
        ).strip().lower()
        scene.music_vocal_lipsync_allowed = _coerce_bool(
            scene.music_vocal_lipsync_allowed
            or (raw_scene.get("musicVocalLipSyncAllowed") if isinstance(raw_scene, dict) else False)
            or (raw_scene.get("music_vocal_lipsync_allowed") if isinstance(raw_scene, dict) else False),
            scene.audio_slice_kind == "music_vocal" and _is_music_vocal_mode(scene.narration_mode),
        )
        presence_type = _infer_music_video_presence_type(scene, payload=payload, raw_scene=raw_scene)
        if index == 0 and shot_type in {"extreme_wide"}:
            shot_type = "wide"
        elif index == 1 and shot_type in {"wide"}:
            shot_type = "close_up" if _scene_has_human_performer(scene) else "medium"
        elif index > 1 and shot_type == prev_shot_type:
            if shot_type == "wide":
                shot_type = "close_up" if _scene_has_human_performer(scene) else "medium"
            elif shot_type in {"close_up", "detail_insert"}:
                shot_type = "wide" if index % 2 == 0 else "medium"
            else:
                shot_type = "duet_shared" if presence_type == "duet" else ("close_up" if index % 2 == 0 else "wide")
        scene.shot_type = shot_type
        duration = max(0.0, _safe_float(scene.duration, scene.time_end - scene.time_start))
        if duration > 0:
            scene.requested_duration_sec = duration
        else:
            scene.requested_duration_sec = _safe_float(scene.requested_duration_sec, 0.0)
        is_short_establishing_beat = duration <= 1.0
        if not _scene_has_character_subject(scene):
            actor_candidates = _detect_music_video_scene_actor_candidates(scene, payload=payload, raw_scene=raw_scene)
            if actor_candidates:
                scene.actors = [actor_candidates[0]]
            elif not is_short_establishing_beat:
                logger.warning(
                    "[SCENARIO_DIRECTOR] dropped renderable music_video scene without subject scene_id=%s duration=%.3f",
                    scene.scene_id,
                    duration,
                )
                continue

        auto_sound_workflow_enabled = False
        has_sound_cue = bool(str(scene.sfx or "").strip() or str(scene.local_phrase or "").strip())
        transition_candidate = _scene_has_transition_evidence(scene) and index > 0 and not prev_two_frames
        forced_transition_scene = forced_first_last_index is not None and index == forced_first_last_index
        if forced_transition_scene:
            transition_candidate = True
        vocal_presentation = _infer_vocal_presentation(scene, payload)
        performer_presentation = _infer_scene_performer_presentation(scene, payload)
        inferred_framing = _infer_performance_framing(
            scene,
            shot_type=shot_type,
            performer_presentation=performer_presentation,
            transition_candidate=transition_candidate,
        )
        performance_framing = str(scene.performance_framing or "").strip() or inferred_framing
        scene.scene_purpose = _infer_music_video_scene_purpose(
            index,
            len(scenes),
            scene,
            transition_candidate=transition_candidate,
            performance_framing=performance_framing,
            performer_presentation=performer_presentation,
        )
        role_influence = _apply_music_video_role_influence(
            scene,
            payload=payload,
            index=index,
            total=len(scenes),
            shot_type=shot_type,
            scene_purpose=scene.scene_purpose,
            presence_type=presence_type,
            performance_framing=performance_framing,
            transition_candidate=transition_candidate,
        )
        shot_type = str(role_influence.get("shot_type") or shot_type)
        presence_type = str(role_influence.get("presence_type") or presence_type)
        performance_framing = str(role_influence.get("performance_framing") or performance_framing)
        scene.scene_purpose = str(role_influence.get("scene_purpose") or scene.scene_purpose)
        keep_first_last, first_last_guard_reason = _should_keep_first_last_for_scene(
            scene,
            transition_candidate=transition_candidate,
            forced_transition_scene=forced_transition_scene,
        )
        if transition_candidate and not keep_first_last:
            transition_candidate = False
            scene.scene_purpose = _infer_music_video_scene_purpose(
                index,
                len(scenes),
                scene,
                transition_candidate=False,
                performance_framing=performance_framing,
                performer_presentation=performer_presentation,
            )
        # Persist role-influenced composition back into final scene state.
        scene.shot_type = shot_type
        scene.shot_type = _normalize_scene_shot_type_from_camera(scene)
        shot_type = str(scene.shot_type or shot_type)
        scene.performance_framing = performance_framing
        lip_sync_signal = _scene_has_lip_sync_signal(scene)
        human_performer = _scene_has_human_performer(scene)
        close_capable = shot_type not in {"wide", "extreme_wide", "aerial"} and performance_framing not in {
            "non_performance",
            "wide_performance",
        }
        lip_sync_failure_reason = ""
        if not lip_sync_signal:
            lip_sync_failure_reason = "signal_missing"
        elif not human_performer:
            lip_sync_failure_reason = "performer_not_human"
        elif transition_candidate:
            lip_sync_failure_reason = "transition_candidate"
        elif prev_lip_sync:
            lip_sync_failure_reason = "previous_scene_already_lipsync"
        elif lip_sync_used >= max_lip_sync:
            lip_sync_failure_reason = "lipsync_quota_reached"
        elif not close_capable:
            lip_sync_failure_reason = "framing_too_wide"
        lip_sync_base_candidate = (
            human_performer
            and close_capable
            and lip_sync_signal
            and not transition_candidate
            and not prev_lip_sync
            and lip_sync_used < max_lip_sync
        )
        lip_sync_compatible, lip_sync_compatibility_reason = _is_lipsync_voice_compatible(vocal_presentation, performer_presentation)
        compatibility_reason_code = ""
        if lip_sync_compatibility_reason == "unknown_vocal_presentation":
            compatibility_reason_code = "vocal_presentation_unknown"
        elif lip_sync_compatibility_reason == "unknown_performer_presentation":
            compatibility_reason_code = "performer_presentation_unknown"
        elif lip_sync_compatibility_reason in {"male_vocal_female_performer_conflict", "female_vocal_male_performer_conflict", "incompatible_presentation"}:
            compatibility_reason_code = "gender/presentation_mismatch"
        lip_sync_candidate = lip_sync_base_candidate and lip_sync_compatible
        scene.performer_presentation = performer_presentation or "unknown"
        scene.vocal_presentation = vocal_presentation or "unknown"
        scene.lip_sync_voice_compatibility = "compatible" if lip_sync_compatible else "incompatible"
        scene.lip_sync_voice_compatibility_reason = compatibility_reason_code or lip_sync_compatibility_reason

        render_mode = "image_video"
        resolved_workflow = str(content_type_policy.get("clipWorkflowDefault") or "i2v")
        ltx_mode = "i2v"
        needs_two_frames = False
        continuation = _coerce_bool(scene.continuation_from_previous, False) or scene.ltx_mode == "continuation"
        send_audio_to_generator = False
        lip_sync = False
        transition_type = "cut" if index > 0 else "cold_open"
        workflow_reason = "Default clip workflow for standard image-to-video scene."
        lip_sync_reason = "Not a lip-sync scene."
        audio_slice_reason = "Audio slice is not required."

        lip_sync_mouth_visible, lip_sync_visibility_reason = _evaluate_lipsync_mouth_visibility(scene)
        preforce_lipsync_candidate = (
            human_performer
            and lip_sync_signal
            and not transition_candidate
            and not prev_lip_sync
            and lip_sync_used < max_lip_sync
            and not forced_transition_scene
        )
        forced_lipsync_composition_applied = False
        if preforce_lipsync_candidate and (not close_capable and not lip_sync_mouth_visible):
            _force_lipsync_friendly_composition(scene)
            forced_lipsync_composition_applied = True
            shot_type = str(scene.shot_type or shot_type)
            performance_framing = str(scene.performance_framing or performance_framing)
            lip_sync_mouth_visible, lip_sync_visibility_reason = _evaluate_lipsync_mouth_visibility(scene)
            close_capable = shot_type not in {"wide", "extreme_wide", "aerial"} and performance_framing not in {
                "non_performance",
                "wide_performance",
            }
            lip_sync_base_candidate = preforce_lipsync_candidate and close_capable
        lip_sync_candidate = lip_sync_base_candidate and lip_sync_compatible and lip_sync_mouth_visible
        if not lip_sync_signal:
            lip_sync_failure_reason = "signal_missing"
        elif not human_performer:
            lip_sync_failure_reason = "performer_not_human"
        elif transition_candidate:
            lip_sync_failure_reason = "transition_candidate"
        elif prev_lip_sync:
            lip_sync_failure_reason = "previous_scene_already_lipsync"
        elif lip_sync_used >= max_lip_sync:
            lip_sync_failure_reason = "lipsync_quota_reached"
        elif not close_capable:
            lip_sync_failure_reason = "framing_too_wide"
        else:
            lip_sync_failure_reason = ""

        if lip_sync_candidate and not forced_transition_scene:
            render_mode = "lip_sync_music"
            resolved_workflow = str(content_type_policy.get("clipWorkflowLipSync") or "image-lipsink-video-music")
            ltx_mode = "lip_sync_music"
            lip_sync = True
            send_audio_to_generator = True
            performance_framing = "face_close" if performance_framing == "" else performance_framing
            scene_start = _safe_float(scene.time_start, 0.0)
            scene_end = max(scene_start, _safe_float(scene.time_end, scene_start))
            base_slice_start = _safe_float(scene.audio_slice_start_sec, scene_start)
            base_slice_end = max(base_slice_start, _safe_float(scene.audio_slice_end_sec, scene_end))
            audio_cap = _safe_float(audio_duration_sec, 0.0) if audio_duration_sec is not None else 0.0
            slice_upper_bound = base_slice_end
            if audio_cap > 0:
                slice_upper_bound = max(base_slice_start, min(base_slice_end, audio_cap))
            start_sec = base_slice_start
            end_sec = min(start_sec + 5.0, slice_upper_bound)
            if end_sec < start_sec:
                end_sec = start_sec
            scene.audio_slice_start_sec = start_sec
            scene.audio_slice_end_sec = end_sec
            scene.audio_slice_expected_duration_sec = round(max(0.0, end_sec - start_sec), 3)
            scene.lip_sync_text = _extract_lip_sync_text(scene)
            workflow_reason = "Lip-sync workflow selected for close human vocal articulation."
            lip_sync_reason = (
                f"Local vocal phrase + human close framing detected; compatibility={lip_sync_compatibility_reason}; "
                f"visibility={lip_sync_visibility_reason}."
            )
            if forced_lipsync_composition_applied:
                lip_sync_reason = f"{lip_sync_reason} forced_composition_applied"
            audio_slice_reason = "Slice clamped to scene vocal window (max ~5s) and timeline bounds."
            lip_sync_used += 1
            _assign_video_route(scene, route="lip_sync_music")
        elif transition_candidate:
            needs_two_frames = True
            transition_type = "state_shift"
            render_mode = "first_last"
            resolved_workflow = str(content_type_policy.get("clipWorkflowFirstLast") or "imag-imag-video-bz")
            ltx_mode = "f_l"
            workflow_reason = (
                "First-last workflow for controlled visual state transition; sound workflow auto-disabled in music_video."
                if has_sound_cue and not auto_sound_workflow_enabled
                else "First-last workflow for controlled visual state transition."
            )
            _assign_video_route(scene, route="f_l")
        elif not keep_first_last and first_last_guard_reason != "not_transition_candidate":
            workflow_reason = "Single-frame workflow kept because first_last guard found reiterative duet beat without explicit visual transition."
            _assign_video_route(
                scene,
                route="downgraded_to_i2v",
                planned_route="f_l",
                downgrade_code="first_last_visual_delta_too_weak",
                downgrade_message="Planned first_last downgraded to i2v because strong A/B visual delta was not detected.",
            )
        elif has_sound_cue and auto_sound_workflow_enabled:
            render_mode = "image_video_sound"
            resolved_workflow = str(content_type_policy.get("clipWorkflowSound") or "i2v_sound")
            ltx_mode = "i2v_as"
            workflow_reason = "Sound-aware workflow selected for SFX/short phrase support."
            _assign_video_route(scene, route="i2v")
        elif has_sound_cue and not auto_sound_workflow_enabled:
            workflow_reason = "Sound cue detected but auto sound workflow is disabled for music_video; using base i2v."
            _assign_video_route(
                scene,
                route="i2v",
                planned_route="i2v_sound",
                downgrade_code="sound_workflow_disabled_for_music_video",
                downgrade_message="Sound workflow is disabled for music_video; canonical i2v route kept.",
            )
        else:
            _assign_video_route(scene, route="i2v")

        if lip_sync_base_candidate and not lip_sync_compatible:
            lip_sync_reason = f"Lip-sync candidate rejected by compatibility gate: {lip_sync_compatibility_reason}."
            if not scene.video_downgrade_reason_code and compatibility_reason_code:
                _assign_video_route(
                    scene,
                    route="downgraded_to_i2v",
                    planned_route="lip_sync_music",
                    downgrade_code=compatibility_reason_code,
                    downgrade_message=f"Lip-sync candidate rejected by compatibility gate: {lip_sync_compatibility_reason}.",
                )
        if not lip_sync_base_candidate and lip_sync_failure_reason:
            lip_sync_reason = f"Lip-sync candidate rejected: {lip_sync_failure_reason}."
        if lip_sync_base_candidate and lip_sync_compatible and not lip_sync_mouth_visible:
            lip_sync_reason = f"Lip-sync candidate rejected: {lip_sync_visibility_reason}."
            _assign_video_route(
                scene,
                route="downgraded_to_i2v",
                planned_route="lip_sync_music",
                downgrade_code=lip_sync_visibility_reason or "lip_sync_mouth_visibility_poor",
                downgrade_message=f"Lip-sync candidate rejected due to framing/visibility: {lip_sync_visibility_reason}.",
            )
            if forced_lipsync_composition_applied:
                lip_sync_reason = f"{lip_sync_reason} forced_composition_failed"
        if lip_sync_candidate and not scene.music_vocal_lipsync_allowed:
            lip_sync_candidate = False
            lip_sync_reason = "Lip-sync candidate rejected: music_vocal_lipsync_not_allowed."
            workflow_reason = "Downgraded to canonical i2v because scene audio slice is not music+vocal compatible for lip_sync_music."
            render_mode = "image_video"
            ltx_mode = "i2v"
            lip_sync = False
            send_audio_to_generator = False
            scene.lip_sync_text = ""
            scene.music_vocal_lipsync_allowed = False
            scene.audio_slice_kind = "voice_only" if str(scene.local_phrase or "").strip() else "none"
            resolved_workflow = str(content_type_policy.get("clipWorkflowDefault") or "i2v")
            _assign_video_route(
                scene,
                route="downgraded_to_i2v",
                planned_route="lip_sync_music",
                downgrade_code="music_vocal_lipsync_not_allowed",
                downgrade_message="Scene could not use lip_sync_music because music_vocal_lipsync_allowed is false.",
            )

        if continuation and not needs_two_frames and not lip_sync:
            ltx_mode = "continuation"
            scene.start_frame_source = "previous_frame"
            transition_type = "continuation"
        elif not continuation and index > 0 and not needs_two_frames and not lip_sync and (index % 4 == 0):
            continuation = True
            ltx_mode = "continuation"
            scene.start_frame_source = "previous_frame"
            transition_type = "continuation"
        elif needs_two_frames and scene.start_frame_source == "previous_frame":
            scene.start_frame_source = "first_frame"
        elif scene.start_frame_source == "previous_frame" and not continuation:
            scene.start_frame_source = "new"

        scene.needs_two_frames = needs_two_frames
        scene.continuation_from_previous = continuation
        scene.render_mode = render_mode
        resolved_workflow_key, resolved_workflow_file = _resolve_workflow_key_and_file(
            resolved_workflow,
            fallback_key="lip_sync_music" if lip_sync else ("f_l" if needs_two_frames else "i2v"),
        )
        scene.resolved_workflow_key = resolved_workflow_key
        scene.resolved_workflow_file = resolved_workflow_file
        scene.ltx_mode = ltx_mode
        previous_reason = str(scene.ltx_reason or "").strip()
        final_reason = workflow_reason
        if previous_reason and previous_reason != workflow_reason:
            final_reason = f"{workflow_reason} Context: {previous_reason}"
        scene.ltx_reason = _normalize_ltx_reason(final_reason, ltx_mode, narration_mode=scene.narration_mode)
        scene.lip_sync = lip_sync
        scene.send_audio_to_generator = send_audio_to_generator
        if lip_sync:
            scene.audio_slice_kind = "music_vocal"
            scene.music_vocal_lipsync_allowed = True
        elif not str(scene.audio_slice_kind or "").strip():
            scene.audio_slice_kind = "voice_only" if str(scene.local_phrase or "").strip() else "none"
        scene.performance_framing = performance_framing
        stage_index = int(round(((index + 1) / max(1, len(scenes))) * (len(clip_arc_stages) - 1)))
        scene.clip_arc_stage = clip_arc_stages[min(max(0, stage_index), len(clip_arc_stages) - 1)]
        scene.beat_function = beat_function_by_stage.get(scene.clip_arc_stage, str(scene.scene_purpose or "performance_step"))
        scene.progression_reason = f"Arc progression follows {scene.clip_arc_stage} stage with audio-first timing anchor."
        scene.transition_family = "state_shift" if needs_two_frames else (transition_type or "cut")
        scene.start_visual_state = str(scene.frame_description or scene.scene_goal or "").strip()
        scene.end_visual_state = str(scene.action_in_frame or scene.scene_goal or "").strip()
        scene.visual_intensity_level = (
            "high"
            if scene.clip_arc_stage == "power_return"
            else ("low" if scene.clip_arc_stage in {"hook_entry", "afterimage_release"} else "medium")
        )
        scene.crowd_relation_state = "crowd_dominant" if "crowd" in " ".join([scene.location, scene.frame_description]).lower() else "hero_dominant"
        scene.performance_phase = performance_phase_by_stage.get(scene.clip_arc_stage, scene.clip_arc_stage)
        scene.transition_type = transition_type if not str(scene.transition_type or "").strip() or scene.transition_type == "cut" else scene.transition_type
        if _is_environment_only_scene_contract(scene):
            _downgrade_to_environment_establishing_note(scene)
            if duration > 1.0:
                if not _enforce_music_video_render_subject_contract(scene, payload=payload, raw_scene=raw_scene):
                    logger.warning(
                        "[SCENARIO_DIRECTOR] dropped environment-only music_video scene without hero scene_id=%s duration=%.3f",
                        scene.scene_id,
                        duration,
                    )
                    continue
                scene.scene_role_dynamics = "hero_present"
                scene.role_influence_reason = "music_video_hero_required"
                scene.workflow_decision_reason = "Environment-only classification remediated by assigning hero subject."
            else:
                logger.info(
                    "[SCENARIO_DIRECTOR] dropped short environment helper beat from final render storyboard scene_id=%s duration=%.3f",
                    scene.scene_id,
                    duration,
                )
                continue
        if forced_transition_scene:
            scene.scene_purpose = "transition"
        renderable_mode = _scene_is_renderable_ltx_mode(scene)
        if renderable_mode and not _enforce_music_video_render_subject_contract(scene, payload=payload, raw_scene=raw_scene):
            logger.warning(
                "[SCENARIO_DIRECTOR] dropped music_video scene without character subject after remediation scene_id=%s",
                scene.scene_id,
            )
            continue
        multi_identity_lock = _build_multi_character_identity_lock(scene, payload)
        genre_intent = _resolve_director_genre_intent(payload, scene)
        visible_identity_lock, identity_fields_used = _build_character_identity_visible_lock(scene, payload=payload, role="character_1")
        hero_contract = _build_normalized_hero_appearance_contract(payload, role="character_1")
        scene.hero_appearance_contract = hero_contract
        scene_roles_lower = {str(actor).strip().lower() for actor in (scene.actors or []) if str(actor).strip()}
        raw_primary_role = str(raw_scene.get("primaryRole") or raw_scene.get("primary_role") or "").strip().lower()
        raw_must_appear = {
            str(role).strip().lower()
            for role in (raw_scene.get("mustAppear") or raw_scene.get("must_appear") or [])
            if str(role).strip()
        }
        character1_required = bool("character_1" in scene_roles_lower or raw_primary_role == "character_1" or "character_1" in raw_must_appear)
        scene.identity_lock_applied = bool(visible_identity_lock)
        scene.identity_lock_notes = (
            (
                "character_1 strong first-scene identity lock applied"
                if bool(visible_identity_lock) and _safe_float(scene.time_start, 0.0) <= 0.05
                else "character_1 visible identity lock applied"
            )
            if visible_identity_lock
            else ("identity_lock_insufficient_source" if "character_1" in {str(actor).strip().lower() for actor in (scene.actors or [])} else "")
        )
        if scene.identity_lock_applied:
            required_lock_fields = [
                "face_identity",
                "hair_identity",
                "body_identity",
                "age_consistency",
                "garment_category",
                "coverage_identity",
                "construction_identity",
                "silhouette_identity",
                "material_identity",
                "signature_details_identity",
                "color_identity",
                "garment_identity",
                "makeup_identity",
                "accessory_identity",
                "footwear_identity",
            ]
            normalized_fields = [str(field or "").strip() for field in (identity_fields_used or []) if str(field or "").strip()]
            for required in required_lock_fields:
                if required not in normalized_fields:
                    normalized_fields.append(required)
            scene.identity_lock_fields_used = normalized_fields
        else:
            scene.identity_lock_fields_used = identity_fields_used
        if character1_required and hero_contract:
            scene.identity_lock_applied = True
            stable_anchor_url = str(raw_scene.get("stableSceneAnchorImageUrl") or raw_scene.get("previousConfirmedStableImageUrl") or "").strip()
            scene.previous_stable_image_anchor_available = bool(index > 0 and kept_scenes)
            scene.previous_stable_image_anchor_url_resolved = stable_anchor_url
            scene.previous_stable_image_anchor_used = False
            scene.previous_stable_image_anchor_applied = False
            scene.previous_stable_image_anchor_reason = (
                "anchor_url_available_but_not_used_in_director_stage" if stable_anchor_url else "anchor_not_resolved"
            )
            guaranteed_fields = list(hero_contract.keys())
            scene.identity_lock_fields_used = list(dict.fromkeys([*(scene.identity_lock_fields_used or []), *guaranteed_fields]))
            if not str(scene.identity_lock_notes or "").strip():
                scene.identity_lock_notes = "character_1_required_hard_person_lock_applied"
        scene.image_prompt = _build_music_video_image_prompt(scene, payload=payload)
        scene.video_prompt = _build_music_video_video_prompt(scene, payload=payload)
        scene.video_negative_prompt = build_ltx_video_negative_prompt(scene)
        # Final derived debug layer (must reflect final scene state, not intermediate steps).
        final_shot_type = str(scene.shot_type or shot_type).strip() or shot_type
        final_presence_type = str(role_influence.get("presence_type") or _infer_music_video_presence_type(scene, payload=payload, raw_scene=raw_scene))
        scene.viewer_hook = _build_music_video_viewer_hook(scene, scene.scene_purpose, final_shot_type)
        if _coerce_bool(role_influence.get("applied"), False):
            role_reason = str(role_influence.get("reason") or "").strip()
            scene.viewer_hook = f"{scene.viewer_hook} Role dynamics: {str(role_influence.get('sceneRoleDynamics') or 'active')}."
            if role_reason:
                scene.viewer_hook = f"{scene.viewer_hook} Dramaturgic reason: {role_reason}."
        scene.clip_decision_reason = _build_music_video_clip_decision_reason(
            scene,
            shot_type=final_shot_type,
            presence_type=final_presence_type,
            vocal_presentation=vocal_presentation,
            performer_presentation=performer_presentation,
            lip_sync_compatible=lip_sync_compatible,
            lip_sync_compatibility_reason=lip_sync_compatibility_reason,
            forced_transition_scene=forced_transition_scene,
            auto_sound_workflow_enabled=auto_sound_workflow_enabled,
        )
        scene.clip_decision_reason = (
            f"{scene.clip_decision_reason} roleInfluenceApplied={'true' if _coerce_bool(role_influence.get('applied'), False) else 'false'}"
            f"; roleInfluenceReason={str(role_influence.get('reason') or 'none')}"
            f"; sceneRoleDynamics={str(role_influence.get('sceneRoleDynamics') or 'neutral')}"
            f"; multiCharacterIdentityLock={'true' if _coerce_bool(multi_identity_lock.get('enabled'), False) else 'false'}"
            f"; distinctCharacterSeparation={'true' if _coerce_bool(multi_identity_lock.get('distinctCharacterSeparation'), False) else 'false'}"
            f"; duetLockEnabled={'true' if _coerce_bool(multi_identity_lock.get('duetLockEnabled'), False) else 'false'}"
            f"; duetCompositionMode={str(multi_identity_lock.get('duetCompositionMode') or 'none')}"
            f"; secondaryRoleVisibilityRequirement={str(multi_identity_lock.get('secondaryRoleVisibilityRequirement') or 'none')}"
            f"; character2DriftGuard={str(multi_identity_lock.get('character2DriftGuard') or 'none')}"
            f"; directorGenreIntent={str(genre_intent.get('directorGenreIntent') or 'neutral_drama')}"
            f"; directorToneBias={str(genre_intent.get('directorToneBias') or 'observational_emotional_realism')}"
            f"; appearanceDriftRisk={str(multi_identity_lock.get('appearanceDriftRisk') or 'none')}."
        )
        scene.role_influence_applied = _coerce_bool(role_influence.get("applied"), False)
        scene.role_influence_reason = str(role_influence.get("reason") or "none")
        scene.scene_role_dynamics = str(role_influence.get("sceneRoleDynamics") or "neutral")
        scene.multi_character_identity_lock = _coerce_bool(multi_identity_lock.get("enabled"), False)
        scene.distinct_character_separation = _coerce_bool(multi_identity_lock.get("distinctCharacterSeparation"), False)
        scene.duet_lock_enabled = _coerce_bool(multi_identity_lock.get("duetLockEnabled"), False)
        scene.duet_composition_mode = str(multi_identity_lock.get("duetCompositionMode") or "")
        scene.secondary_role_visibility_requirement = str(multi_identity_lock.get("secondaryRoleVisibilityRequirement") or "")
        scene.character2_drift_guard = str(multi_identity_lock.get("character2DriftGuard") or "")
        scene.duet_identity_contract = str(multi_identity_lock.get("duetIdentityContract") or "")
        scene.appearance_drift_risk = str(multi_identity_lock.get("appearanceDriftRisk") or "none")
        if character1_required and (scene.render_mode == "lip_sync_music" or str(scene.shot_type or "").strip().lower() in {"portrait", "close_up", "beauty"}):
            scene.appearance_drift_risk = "high_identity_drift_risk_portrait_lipsync_hard_lock"
        if scene.identity_lock_applied and scene.appearance_drift_risk in {"", "none"}:
            scene.appearance_drift_risk = "low_locked_by_character_reference"
        scene.director_genre_intent = str(genre_intent.get("directorGenreIntent") or "neutral_drama")
        scene.director_genre_reason = str(genre_intent.get("directorGenreReason") or "fallback")
        scene.director_tone_bias = str(genre_intent.get("directorToneBias") or "observational_emotional_realism")
        _enhance_music_video_transition_language(scene)
        scene.workflow_decision_reason = workflow_reason
        scene.lip_sync_decision_reason = f"{lip_sync_reason} shot={scene.shot_type}; framing={scene.performance_framing}; route={scene.video_generation_route or scene.resolved_workflow_key}."
        scene.audio_slice_decision_reason = audio_slice_reason
        if scene.render_mode == "lip_sync_music":
            if str(scene.performer_presentation or "unknown").strip().lower() == "unknown":
                scene.performer_presentation = _infer_scene_performer_presentation(scene, payload) or "female"
            if str(scene.vocal_presentation or "unknown").strip().lower() == "unknown":
                scene.vocal_presentation = _infer_vocal_presentation(scene, payload) or scene.performer_presentation or "female"
            if str(scene.lip_sync_voice_compatibility or "unknown").strip().lower() == "unknown":
                scene.lip_sync_voice_compatibility = "compatible"
            if not str(scene.audio_anchor_evidence or "").strip():
                scene.audio_anchor_evidence = str(scene.local_phrase or scene.what_from_audio_this_scene_uses or "phrase/beat contour matched for lip_sync_music").strip()
            if not str(scene.performance_phase or "").strip():
                scene.performance_phase = str(scene.clip_arc_stage or "build").strip()
            if not str(scene.emotion or "").strip():
                scene.emotion = "performance intensity from audio contour"
            scene.audio_emotion_direction = _derive_audio_emotion_direction(scene)
            if not str(scene.lip_sync_decision_reason or "").strip():
                scene.lip_sync_decision_reason = "lip_sync_music_audio_shaped_emotion_applied"
        compacted_ending_hold, ending_hold_reason = _maybe_compact_repeat_heavy_ending_hold(
            scene,
            repeat_heavy_clip=repeat_heavy_clip,
        )
        if compacted_ending_hold:
            scene.workflow_decision_reason = f"{scene.workflow_decision_reason} Ending hold compacted for repeat-heavy clip context."
            scene.audio_slice_decision_reason = f"{scene.audio_slice_decision_reason} Ending hold compacted to keep final beat concise."
        elif ending_hold_reason == "complex_evolution_present":
            scene.workflow_decision_reason = f"{scene.workflow_decision_reason} Ending hold kept longer due to internal visual evolution."

        if not lip_sync:
            scene.lip_sync_text = ""
            scene.music_vocal_lipsync_allowed = scene.audio_slice_kind == "music_vocal" and _is_music_vocal_mode(scene.narration_mode)
            fallback_start = round(_safe_float(scene.audio_slice_start_sec, _safe_float(scene.time_start, 0.0)), 3)
            fallback_end = round(
                max(
                    fallback_start,
                    _safe_float(
                        scene.audio_slice_end_sec,
                        max(_safe_float(scene.time_end, fallback_start), fallback_start),
                    ),
                ),
                3,
            )
            scene.audio_slice_start_sec = fallback_start
            scene.audio_slice_end_sec = fallback_end
            scene.audio_slice_expected_duration_sec = round(max(0.0, fallback_end - fallback_start), 3)

        if scene.render_mode == "lip_sync_music":
            if not scene.music_vocal_lipsync_allowed or scene.audio_slice_kind != "music_vocal":
                reason = (
                    "music_vocal_lipsync_not_allowed"
                    if not scene.music_vocal_lipsync_allowed
                    else "audio_slice_not_music_vocal"
                )
                _rollback_lipsync_to_i2v(
                    scene,
                    reason=f"lip_sync_music blocked: {reason}. scene downgraded_to_i2v with full lip-sync state rollback.",
                    downgrade_code=reason,
                )
                continue
            _enforce_lip_sync_music_visual_canon(scene)
            scene.send_audio_to_generator = True
        if scene.render_mode in {"image_video_sound", "first_last_sound"}:
            scene.render_mode = "image_video" if scene.render_mode == "image_video_sound" else "first_last"
            scene.resolved_workflow_key, scene.resolved_workflow_file = _resolve_workflow_key_and_file(
                "i2v" if scene.render_mode == "image_video" else "f_l",
                fallback_key="i2v" if scene.render_mode == "image_video" else "f_l",
            )
        if scene.identity_lock_applied is False and "character_1" in {str(actor).strip().lower() for actor in (scene.actors or [])}:
            _assign_video_route(
                scene,
                route=scene.video_generation_route or "i2v",
                planned_route=scene.planned_video_generation_route or scene.video_generation_route or "i2v",
                block_code=scene.video_block_reason_code,
                block_message=scene.video_block_reason_message,
                downgrade_code=scene.video_downgrade_reason_code or "identity_lock_insufficient_source",
                downgrade_message=scene.video_downgrade_reason_message or "character_1 reference profile is insufficient for strong identity lock.",
            )

        _scene_renderability_guard(scene, prior_scene_exists=bool(kept_scenes))
        prev_lip_sync = bool(scene.lip_sync)
        prev_two_frames = bool(scene.needs_two_frames)
        prev_shot_type = str(scene.shot_type or shot_type)
        kept_scenes.append(scene)
    storyboard_out.scenes = kept_scenes
    if kept_scenes:
        if not str(storyboard_out.story.title or "").strip():
            storyboard_out.story.title = "Audio-first music video arc"
        if not str(storyboard_out.story.summary or "").strip():
            storyboard_out.story.summary = "Performance-driven arc inside one coherent real venue, anchored to audio timing."
        if not str(storyboard_out.story_summary or "").strip():
            storyboard_out.story_summary = storyboard_out.story.summary
        if not str(storyboard_out.director_summary or "").strip():
            storyboard_out.director_summary = "Audio drives timing/energy; visuals stay grounded in one coherent photoreal venue without literal line-by-line rewrite."
        if not str(storyboard_out.audio_understanding.main_topic or "").strip():
            storyboard_out.audio_understanding.main_topic = "music performance arc"
        if not str(storyboard_out.audio_understanding.world_context or "").strip():
            storyboard_out.audio_understanding.world_context = "single coherent real-world performance venue with stable geography across scenes"
        if not str(storyboard_out.audio_understanding.emotional_tone_from_audio or "").strip():
            storyboard_out.audio_understanding.emotional_tone_from_audio = "dynamic: hook confidence -> inner dip -> climax return -> release"
        if not str(storyboard_out.audio_understanding.what_from_audio_defines_world or "").strip():
            storyboard_out.audio_understanding.what_from_audio_defines_world = "rhythm pressure, vocal emphasis, and phrase energy shifts define visual world transitions"
    return storyboard_out


def _detect_expected_character_roles(payload: dict[str, Any]) -> list[str]:
    effective_role_types, source_by_role, _ = _resolve_effective_role_type_by_role(payload)
    explicit_roles = [
        role
        for role in ("character_1", "character_2", "character_3")
        if source_by_role.get(role) == "explicit" and effective_role_types.get(role) in {"hero", "support", "antagonist"}
    ]
    if explicit_roles:
        return explicit_roles[:2]
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    expected: list[str] = []
    for role in ("character_1", "character_2", "character_3"):
        item = refs.get(role)
        if not isinstance(item, dict):
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        connected = _coerce_bool(meta.get("connected"), False)
        ref_count = len(item.get("refs") or [])
        count = int(item.get("count") or 0)
        if connected or ref_count > 0 or count > 0:
            expected.append(role)
    if len(expected) >= 2:
        return expected[:2]
    implied = connected_summary.get("entities") if isinstance(connected_summary.get("entities"), list) else []
    for role in ("character_1", "character_2"):
        if role in expected:
            continue
        if any(role in str(entity or "") for entity in implied):
            expected.append(role)
    return expected[:2]


def _enforce_explicit_role_assignments(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut) -> tuple[ScenarioDirectorStoryboardOut, list[str]]:
    effective_role_types, source_by_role, _ = _resolve_effective_role_type_by_role(payload)
    explicit_roles = [
        role
        for role in ("character_1", "character_2", "character_3")
        if source_by_role.get(role) == "explicit" and effective_role_types.get(role) in {"hero", "support", "antagonist"}
    ]
    if not explicit_roles:
        return storyboard_out, []
    warnings: list[str] = []
    role_presence = {role: False for role in explicit_roles}
    for scene in storyboard_out.scenes:
        for role in explicit_roles:
            if role in scene.actors:
                role_presence[role] = True
    for role in explicit_roles:
        if role_presence.get(role):
            continue
        target_scene = storyboard_out.scenes[0] if storyboard_out.scenes else None
        if target_scene:
            target_scene.actors.append(role)
            role_presence[role] = True
            warnings.append(f"explicit_role_repaired:{role}")
    return storyboard_out, warnings


def _enforce_single_character_music_video_policy(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    active_roles = _collect_active_connected_character_roles(payload)
    storyboard_out.diagnostics.active_connected_character_roles = list(active_roles)
    is_single_character_mode = active_roles == ["character_1"]
    storyboard_out.diagnostics.single_character_mode_enforced = is_single_character_mode
    if not is_single_character_mode:
        return storyboard_out
    removed_roles: set[str] = set()
    for scene in storyboard_out.scenes or []:
        actor_roles = [role for role in (scene.actors or []) if str(role).strip()]
        scene.actors = [role for role in actor_roles if str(role).strip().lower() != "character_2"]
        if len(actor_roles) != len(scene.actors):
            removed_roles.add("character_2")
        actor_roles_after = {str(role).strip().lower() for role in (scene.actors or []) if str(role).strip()}
        if "character_2" not in actor_roles_after:
            scene.duet_lock_enabled = False
            scene.duet_composition_mode = "single_focus"
            scene.secondary_role_visibility_requirement = "none"
            scene.character2_drift_guard = "not_required"
            scene.duet_identity_contract = ""
            scene.multi_character_identity_lock = False
            scene.distinct_character_separation = False
            dynamics_parts = [
                part.strip()
                for part in str(scene.scene_role_dynamics or "").split(",")
                if part.strip() and part.strip() != "duet_pair_protected"
            ]
            scene.scene_role_dynamics = ",".join(dynamics_parts) if dynamics_parts else "hero_anchor"
        if str(scene.performer_presentation or "unknown").strip().lower() == "unknown":
            fallback_performer = _infer_scene_performer_presentation(scene, payload)
            if fallback_performer in {"male", "female", "mixed"}:
                scene.performer_presentation = fallback_performer
                scene.lip_sync_voice_compatibility_reason = (
                    f"{scene.lip_sync_voice_compatibility_reason}; performer_presentation_single_character_fallback"
                ).strip("; ")
        if str(scene.vocal_presentation or "unknown").strip().lower() == "unknown":
            fallback_vocal = _infer_vocal_presentation(scene, payload)
            if fallback_vocal in {"male", "female", "mixed"}:
                scene.vocal_presentation = fallback_vocal
        if "character_1" in {str(actor).strip().lower() for actor in (scene.actors or [])}:
            if not scene.hero_appearance_contract:
                scene.hero_appearance_contract = _build_normalized_hero_appearance_contract(payload, role="character_1")
            scene.identity_lock_applied = bool(scene.identity_lock_applied or scene.identity_lock_fields_used)
            if not str(scene.identity_lock_notes or "").strip():
                scene.identity_lock_notes = "single_character_mode_identity_lock_required_for_character_1"
            if not scene.identity_lock_fields_used:
                scene.identity_lock_fields_used = [
                    "face_identity",
                    "hair_identity",
                    "body_identity",
                    "silhouette_identity",
                    "age_consistency",
                    "garment_category",
                    "coverage_identity",
                    "construction_identity",
                    "material_identity",
                    "signature_details_identity",
                    "color_identity",
                    "garment_identity",
                    "makeup_identity",
                    "footwear_identity",
                    "accessory_identity",
                    "world_identity",
                ]
            if scene.hero_appearance_contract:
                scene.identity_lock_applied = True
                scene.identity_lock_fields_used = list(
                    dict.fromkeys([*(scene.identity_lock_fields_used or []), *scene.hero_appearance_contract.keys()])
                )
    storyboard_out.story_summary = _remove_single_character_summary_duet_phrases(storyboard_out.story_summary)
    storyboard_out.full_scenario = _remove_single_character_summary_duet_phrases(storyboard_out.full_scenario)
    storyboard_out.director_summary = _remove_single_character_summary_duet_phrases(storyboard_out.director_summary)
    storyboard_out.diagnostics.removed_inactive_roles = sorted(removed_roles)
    return storyboard_out


def _character_lock_candidate_score(scene: ScenarioDirectorScene, *, scene_index: int, total_scenes: int, companion_roles: set[str]) -> int:
    bundle = _scene_text_bundle(scene).lower()
    purpose = _infer_scene_purpose(scene)
    score = 0
    if purpose in {"reveal", "confrontation", "escalation", "destabilization"}:
        score += 3
    if any(token in bundle for token in ("reveal", "confront", "exchange", "reaction", "watches", "faces", "shared", "between", "answer", "responds", "alarm", "tension")):
        score += 2
    if any(actor in companion_roles for actor in scene.actors):
        score += 2
    if any(token in bundle for token in ("close-up", "isolated", "alone", "empty hallway", "environment only", "abstract", "held image", "lingers on")):
        score -= 3
    if scene_index == 0 and purpose == "hook":
        score -= 2
    if scene_index == total_scenes - 1 and purpose == "final image / ending hold":
        score -= 2
    if len(scene.actors) <= 1 and any(token in bundle for token in ("close-up", "macro", "portrait", "hand", "face")):
        score -= 2
    return score


def _enforce_character_lock(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    expected_roles = _detect_expected_character_roles(payload)
    if len(expected_roles) < 2 or not storyboard_out.scenes:
        return storyboard_out
    role_set = set(expected_roles)
    present_roles = {actor for scene in storyboard_out.scenes for actor in scene.actors if actor in role_set}
    missing_roles = [role for role in expected_roles if role not in present_roles]
    if not missing_roles:
        return storyboard_out
    total_scenes = len(storyboard_out.scenes)
    for role in missing_roles:
        companion_roles = role_set - {role}
        candidates: list[tuple[int, int]] = []
        for index, scene in enumerate(storyboard_out.scenes):
            score = _character_lock_candidate_score(scene, scene_index=index, total_scenes=total_scenes, companion_roles=companion_roles)
            if score >= 3:
                candidates.append((score, index))
        if not candidates:
            logger.debug("[SCENARIO_DIRECTOR] character lock skipped no_candidate role=%s", role)
            continue
        candidates.sort(key=lambda item: (-item[0], item[1]))
        repaired_indexes: list[str] = []
        max_repairs = 1 if total_scenes <= 4 else 2
        for _, index in candidates[:max_repairs]:
            scene = storyboard_out.scenes[index]
            if role not in scene.actors:
                scene.actors.append(role)
            if not scene.scene_goal or scene.scene_goal.lower() in GENERIC_SCENE_GOALS:
                purpose = _infer_scene_purpose(scene)
                if purpose in {"reveal", "confrontation", "escalation", "destabilization"}:
                    scene.scene_goal = purpose
                elif any(actor in companion_roles for actor in scene.actors):
                    scene.scene_goal = "interaction"
            repaired_indexes.append(scene.scene_id)
        if repaired_indexes:
            logger.debug("[SCENARIO_DIRECTOR] character lock repaired role=%s scenes=%s", role, ",".join(repaired_indexes))
        else:
            logger.debug("[SCENARIO_DIRECTOR] character lock skipped no_candidate role=%s", role)
    return storyboard_out


def _has_overly_uniform_timing(storyboard_out: ScenarioDirectorStoryboardOut) -> bool:
    if len(storyboard_out.scenes) < 3:
        return False
    durations = [max(0.1, _safe_float(scene.duration, scene.time_end - scene.time_start)) for scene in storyboard_out.scenes]
    spread = max(durations) - min(durations)
    average = sum(durations) / len(durations)
    max_deviation = max(abs(value - average) for value in durations)
    repeated_fives = sum(1 for value in durations if abs(value - 5.0) <= 0.15)
    half_second_buckets = {round(value * 2) / 2 for value in durations}
    return (spread <= 0.45 and max_deviation <= 0.3) or (len(half_second_buckets) <= 2 and spread <= 0.6) or (repeated_fives >= max(3, len(durations) - 1) and spread <= 0.8)


def _apply_timing_variation(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not _has_overly_uniform_timing(storyboard_out):
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    scene_count = len(storyboard_out.scenes)
    if scene_count < 2:
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    durations = [max(1.2, _safe_float(scene.duration, scene.time_end - scene.time_start)) for scene in storyboard_out.scenes]
    deltas = [0.0] * scene_count
    purposes = [_infer_scene_purpose(scene) for scene in storyboard_out.scenes]
    if durations[0] >= 2.2 and purposes[0] not in {"emotional climax", "final image / ending hold"}:
        deltas[0] -= min(0.35, durations[0] - 1.8)
    if purposes[-1] in {"reveal", "emotional climax", "peak image", "final image / ending hold"} or durations[-1] <= max(durations[:-1]):
        deltas[-1] += 0.35
    if scene_count >= 4 and purposes[-2] in {"reveal", "emotional climax", "confrontation", "escalation"}:
        deltas[-2] += 0.2
    positive_total = round(sum(value for value in deltas if value > 0), 3)
    negative_total = round(-sum(value for value in deltas if value < 0), 3)
    remaining = round(positive_total - negative_total, 3)
    compensation_indexes = [
        index
        for index in range(1, max(1, scene_count - 1))
        if durations[index] > 1.8 and purposes[index] not in {"emotional climax", "final image / ending hold"}
    ]
    if remaining > 0 and compensation_indexes:
        per_scene = min(0.2, round(remaining / len(compensation_indexes), 3))
        for index in compensation_indexes:
            available = max(0.0, durations[index] - 1.4)
            reduction = min(per_scene, available)
            deltas[index] -= reduction
            remaining = round(remaining - reduction, 3)
            if remaining <= 0.02:
                break
    elif remaining < 0 and compensation_indexes:
        deltas[compensation_indexes[len(compensation_indexes) // 2]] += abs(remaining)
    adjusted = [round(max(1.2, duration + delta), 3) for duration, delta in zip(durations, deltas)]
    total_diff = round(sum(durations) - sum(adjusted), 3)
    if abs(total_diff) > 0.01:
        rebalance_index = compensation_indexes[0] if compensation_indexes else min(max(1, scene_count // 2), scene_count - 1)
        adjusted[rebalance_index] = round(max(1.2, adjusted[rebalance_index] + total_diff), 3)
    if max(abs(adjusted[index] - durations[index]) for index in range(scene_count)) < 0.15:
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    cursor = 0.0
    for scene, duration in zip(storyboard_out.scenes, adjusted):
        scene.time_start = round(cursor, 3)
        scene.duration = duration
        scene.time_end = round(scene.time_start + scene.duration, 3)
        cursor = scene.time_end
    delta_total = round(sum(abs(adjusted[index] - durations[index]) for index in range(scene_count)), 3)
    logger.debug("[SCENARIO_DIRECTOR] timing variation applied delta_total=%s", delta_total)
    return storyboard_out


def _scene_has_transition_evidence(scene: ScenarioDirectorScene) -> bool:
    bundle = _scene_text_bundle(scene).lower()
    return any(
        token in bundle
        for token in (
            "door opens", "opens one inch", "entering a new", "new chamber", "new room", "crosses the threshold",
            "reveal", "reveals", "unveils", "transformation", "before", "after", "state shift", "activation",
            "activates", "ignites", "powers on", "hatch opens", "crosses into", "discovers",
        )
    )


def _scene_has_audio_reactive_evidence(scene: ScenarioDirectorScene) -> bool:
    bundle = _scene_text_bundle(scene).lower()
    return any(
        token in bundle
        for token in (
            "pulses", "pulse", "breathing machinery", "machinery", "hum", "audio-reactive", "lights flicker",
            "rhythmic", "throb", "alarm", "siren", "vibration", "subtle response",
        )
    )


def _recover_ltx_mode(scene: ScenarioDirectorScene) -> str:
    if _scene_has_transition_evidence(scene):
        return "f_l_as" if _scene_has_audio_reactive_evidence(scene) else "f_l"
    if _scene_has_audio_reactive_evidence(scene):
        return "i2v_as"
    return "i2v"


def _rebalance_ltx_modes(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not storyboard_out.scenes:
        return storyboard_out
    continuation_total = sum(
        1 for scene in storyboard_out.scenes if scene.ltx_mode == "continuation" or scene.continuation_from_previous or scene.start_frame_source == "previous_frame"
    )
    continuation_limit = max(2, len(storyboard_out.scenes) // 2)
    continuation_seen = 0
    for index, scene in enumerate(storyboard_out.scenes):
        original_mode = scene.ltx_mode
        target_mode = original_mode
        correction_reason = ""
        if original_mode in {"lip_sync", "lip_sync_music"} and not _is_music_vocal_mode(scene.narration_mode):
            target_mode = "i2v_as"
            correction_reason = "visible articulation is unsupported by narration mode"
        elif original_mode == "continuation":
            continuation_seen += 1
            if index == 0:
                target_mode = _recover_ltx_mode(scene)
                correction_reason = "continuation cannot be the first scene"
            elif continuation_total > continuation_limit and continuation_seen > continuation_limit:
                target_mode = _recover_ltx_mode(scene)
                correction_reason = "continuation was overused"
        elif index == 0 and scene.start_frame_source == "previous_frame":
            target_mode = _recover_ltx_mode(scene)
            correction_reason = "first scene cannot inherit a previous frame"
        elif original_mode in {"f_l", "f_l_as"} and not scene.needs_two_frames and not _scene_has_transition_evidence(scene):
            target_mode = "i2v_as" if original_mode == "f_l_as" and _scene_has_audio_reactive_evidence(scene) else "i2v"
            correction_reason = "two-frame transition evidence is missing"
        elif original_mode == "i2v_as" and not _scene_has_audio_reactive_evidence(scene) and _scene_has_transition_evidence(scene) and scene.needs_two_frames:
            target_mode = "f_l_as"
            correction_reason = "transition evidence is stronger than ambient pulse"
        if target_mode != original_mode:
            scene.ltx_mode = target_mode
            scene.ltx_reason = _normalize_ltx_reason(f"Normalized to {target_mode}: {correction_reason}.", target_mode, narration_mode=scene.narration_mode)
            logger.debug("[SCENARIO_DIRECTOR] ltx normalize corrected scene_id=%s from=%s to=%s", scene.scene_id, original_mode, target_mode)
        else:
            scene.ltx_reason = _normalize_ltx_reason(scene.ltx_reason, scene.ltx_mode, narration_mode=scene.narration_mode)
            logger.debug("[SCENARIO_DIRECTOR] ltx normalize kept original scene_id=%s mode=%s", scene.scene_id, scene.ltx_mode)
        scene.needs_two_frames = scene.ltx_mode in {"f_l", "f_l_as"}
        if scene.ltx_mode == "continuation" and index > 0:
            scene.continuation_from_previous = True
            scene.start_frame_source = "previous_frame"
        elif scene.ltx_mode != "continuation":
            scene.continuation_from_previous = False
            if scene.start_frame_source == "previous_frame":
                scene.start_frame_source = "new"
    return storyboard_out


def _assert_storyboard_quality(storyboard_out: ScenarioDirectorStoryboardOut) -> None:
    scenes = storyboard_out.scenes or []
    if not scenes:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_empty_after_filter", "Scenario Director returned no usable scenes.", status_code=502)
    frame_count = sum(1 for scene in scenes if str(scene.frame_description or "").strip())
    action_count = sum(1 for scene in scenes if str(scene.action_in_frame or "").strip())
    camera_count = sum(1 for scene in scenes if str(scene.camera or "").strip())
    if frame_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without frame_description.", status_code=502)
    if action_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without action_in_frame.", status_code=502)
    if camera_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without camera direction.", status_code=502)
    strong_scene_count = 0
    generic_scene_count = 0
    directing_scene_count = 0
    for scene in scenes:
        weak, reason = _scene_weak_assessment(scene)
        directing_fields = sum(1 for field in (scene.frame_description, scene.action_in_frame, scene.camera) if str(field or "").strip())
        if directing_fields >= 2:
            directing_scene_count += 1
        if not weak or _scene_specificity_score(scene) >= 4:
            strong_scene_count += 1
        if reason == "generic":
            generic_scene_count += 1
    if directing_scene_count == 0 or strong_scene_count == 0 or generic_scene_count == len(scenes):
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned only weak filler scenes.", status_code=502)


def _resolve_audio_duration_sec(payload: dict[str, Any]) -> float:
    duration, _ = _resolve_audio_duration_info(payload)
    return duration


def _validate_audio_timeline_coverage(scenes: list[ScenarioDirectorScene], audio_duration_sec: float, *, coverage_source: str = "fallback") -> dict[str, Any]:
    if audio_duration_sec <= 0:
        return {
            "audioDurationSec": 0.0,
            "expectedAudioDurationSec": 0.0,
            "actualCoveredDurationSec": 0.0,
            "coverageSource": "missing",
            "timelineStartSec": 0.0,
            "timelineEndSec": 0.0,
            "timelineCoverageSec": 0.0,
            "timelineCoverageRatio": None,
            "coverageRatio": None,
            "uncoveredTailSec": 0.0,
            "internalGapCount": 0,
            "timelineCoverageStatus": "ok",
            "warnings": [],
        }
    if not scenes:
        return {
            "audioDurationSec": audio_duration_sec,
            "expectedAudioDurationSec": audio_duration_sec,
            "actualCoveredDurationSec": 0.0,
            "coverageSource": coverage_source,
            "timelineStartSec": 0.0,
            "timelineEndSec": 0.0,
            "timelineCoverageSec": 0.0,
            "timelineCoverageRatio": 0.0,
            "coverageRatio": 0.0,
            "uncoveredTailSec": audio_duration_sec,
            "internalGapCount": 0,
            "timelineCoverageStatus": "invalid",
            "warnings": ["timeline_coverage_too_short", "timeline_does_not_reach_audio_end", "timeline_has_uncovered_audio_tail"],
        }
    sorted_scenes = sorted(scenes, key=lambda scene: (scene.time_start, scene.time_end))
    first_start = _safe_float(sorted_scenes[0].time_start, 0.0)
    last_end = _safe_float(sorted_scenes[-1].time_end, first_start)
    total_coverage = 0.0
    gap_count = 0
    warnings: list[str] = []
    cursor = 0.0
    for scene in sorted_scenes:
        start = _safe_float(scene.time_start, cursor)
        end = max(start, _safe_float(scene.time_end, start))
        gap = round(max(0.0, start - cursor), 3)
        if gap >= TIMELINE_INTERNAL_GAP_WARN_SEC:
            gap_count += 1
            warnings.append("timeline_has_large_internal_gap")
        total_coverage += max(0.0, end - start)
        cursor = max(cursor, end)
    uncovered_tail = round(max(0.0, audio_duration_sec - last_end), 3)
    coverage_ratio = round((total_coverage / audio_duration_sec) if audio_duration_sec > 0 else 0.0, 4)
    if first_start > TIMELINE_START_TOLERANCE_SEC:
        warnings.append("timeline_does_not_start_at_zero")
    if audio_duration_sec - last_end > TIMELINE_END_TOLERANCE_SEC:
        warnings.append("timeline_does_not_reach_audio_end")
    if uncovered_tail > TIMELINE_TAIL_WARN_SEC:
        warnings.append("timeline_has_uncovered_audio_tail")
    if coverage_ratio < TIMELINE_COVERAGE_RATIO_WARN:
        warnings.append("timeline_coverage_too_short")
    status = "ok"
    unique_warnings = list(dict.fromkeys(warnings))
    if any(code in unique_warnings for code in ("timeline_does_not_reach_audio_end", "timeline_has_uncovered_audio_tail", "timeline_coverage_too_short")):
        status = "invalid"
    elif unique_warnings:
        status = "warning"
    return {
        "audioDurationSec": round(audio_duration_sec, 3),
        "expectedAudioDurationSec": round(audio_duration_sec, 3),
        "actualCoveredDurationSec": round(total_coverage, 3),
        "coverageSource": coverage_source,
        "timelineStartSec": round(first_start, 3),
        "timelineEndSec": round(last_end, 3),
        "timelineCoverageSec": round(total_coverage, 3),
        "timelineCoverageRatio": coverage_ratio,
        "coverageRatio": coverage_ratio,
        "uncoveredTailSec": uncovered_tail,
        "internalGapCount": gap_count,
        "timelineCoverageStatus": status,
        "warnings": unique_warnings,
    }


def _pick_clip_split_boundary(
    *,
    scene: ScenarioDirectorScene,
    duration: float,
    audio_analysis: dict[str, Any],
    merged_phrase_risk: bool,
) -> tuple[float | None, str, str]:
    start = _safe_float(scene.time_start, 0.0)
    end = max(start, _safe_float(scene.time_end, start))
    midpoint = round((start + end) / 2.0, 3)
    min_chunk = 1.5
    lower = start + min_chunk
    upper = end - min_chunk
    if upper <= lower:
        return None, "duration_too_short_for_safe_split", "fallback"

    def _valid_boundary(value: Any) -> float | None:
        point = _safe_float(value, -1.0)
        if point <= lower or point >= upper:
            return None
        return round(point, 3)

    candidates: list[tuple[float, str, int]] = []
    for phrase in (audio_analysis.get("phrases") or []):
        if not isinstance(phrase, dict):
            continue
        for key in ("end", "boundary", "timeSec", "time"):
            boundary = _valid_boundary(phrase.get(key))
            if boundary is not None:
                candidates.append((boundary, "phrase_boundary", 100))
                break
    for pause in (audio_analysis.get("pauseWindows") or []):
        if not isinstance(pause, dict):
            continue
        pause_start = _safe_float(pause.get("start"), -1.0)
        pause_end = _safe_float(pause.get("end"), -1.0)
        if pause_start > 0 and pause_end > 0:
            boundary = _valid_boundary((pause_start + pause_end) / 2.0)
        else:
            boundary = _valid_boundary(pause_start if pause_start > 0 else pause_end)
        if boundary is not None:
            candidates.append((boundary, "pause_boundary", 80))
    for transition in (audio_analysis.get("energyTransitions") or []):
        if not isinstance(transition, dict):
            continue
        boundary = _valid_boundary(transition.get("timeSec"))
        if boundary is not None:
            candidates.append((boundary, "energy_boundary", 60))

    local_phrase = str(scene.local_phrase or "").strip()
    semantic_markers = [marker for marker in re.split(r"(?:[|/]+|\n+|[;!?]+)", local_phrase) if str(marker or "").strip()]
    if len(semantic_markers) >= 2:
        step = duration / max(1, len(semantic_markers))
        for idx in range(1, len(semantic_markers)):
            boundary = _valid_boundary(start + step * idx)
            if boundary is not None:
                candidates.append((boundary, "semantic_action_shift", 45))

    text_signal = " ".join(
        [
            str(scene.scene_goal or "").lower(),
            str(scene.action_in_frame or "").lower(),
            str(scene.frame_description or "").lower(),
        ]
    )
    if any(token in text_signal for token in DUO_SCENE_HINTS | {"looks at", "turns to", "switches focus", "focus on"}):
        boundary = _valid_boundary(midpoint)
        if boundary is not None:
            candidates.append((boundary, "performer_focus_change", 30))

    if candidates:
        candidates.sort(key=lambda item: (-item[2], abs(item[0] - midpoint)))
        point, reason, _ = candidates[0]
        boundary_reason = "phrase" if reason == "phrase_boundary" else ("pause" if reason == "pause_boundary" else ("energy" if reason == "energy_boundary" else "semantic"))
        return point, reason, boundary_reason
    return round(max(lower, min(upper, midpoint)), 3), "midpoint_fallback", "fallback"


def _collect_generation_chunk_boundaries(
    *,
    payload: dict[str, Any] | None,
    audio_analysis: dict[str, Any],
) -> dict[str, list[float]]:
    boundary_signals: dict[str, list[float]] = {
        "pause": [],
        "phrase": [],
        "phrase_start": [],
        "transition": [],
        "semantic": [],
    }
    seen: set[tuple[str, float]] = set()

    def _push(kind: str, value: Any) -> None:
        if kind not in boundary_signals:
            return
        point = round(_safe_float(value, -1.0), 3)
        if point <= 0:
            return
        key = (kind, point)
        if key in seen:
            return
        seen.add(key)
        boundary_signals[kind].append(point)

    root = payload if isinstance(payload, dict) else {}
    single_call = root.get("_single_call_payload") if isinstance(root.get("_single_call_payload"), dict) else {}
    candidate_payloads = [single_call, root]
    for candidate in candidate_payloads:
        if not isinstance(candidate, dict):
            continue
        audio_structure = candidate.get("audioStructure") if isinstance(candidate.get("audioStructure"), dict) else {}
        for pause in (audio_structure.get("pauses") or []):
            if not isinstance(pause, dict):
                continue
            t0 = _safe_float(pause.get("t0") if pause.get("t0") is not None else pause.get("start"), -1.0)
            t1 = _safe_float(pause.get("t1") if pause.get("t1") is not None else pause.get("end"), -1.0)
            if t0 > 0 and t1 > 0:
                _push("pause", (t0 + t1) / 2.0)
            else:
                _push("pause", t0 if t0 > 0 else t1)
        for transition in (audio_structure.get("transitions") or []):
            if isinstance(transition, dict):
                _push("transition", transition.get("timeSec") or transition.get("t") or transition.get("time") or transition.get("t0"))
            else:
                _push("transition", transition)
        for row in (candidate.get("transcript") or []):
            if not isinstance(row, dict):
                continue
            _push("phrase_start", row.get("t0") if row.get("t0") is not None else row.get("start"))
            _push("phrase", row.get("t1") if row.get("t1") is not None else row.get("end"))
        for row in (candidate.get("semanticTimeline") or []):
            if not isinstance(row, dict):
                continue
            _push("phrase_start", row.get("t0") if row.get("t0") is not None else row.get("startSec"))
            _push("semantic", row.get("t1") if row.get("t1") is not None else row.get("endSec"))

    for phrase in (audio_analysis.get("phrases") or []):
        if not isinstance(phrase, dict):
            continue
        _push("phrase_start", phrase.get("start"))
        _push("phrase", phrase.get("end") or phrase.get("boundary") or phrase.get("timeSec") or phrase.get("time"))
    for pause in (audio_analysis.get("pauseWindows") or []):
        if not isinstance(pause, dict):
            continue
        start = _safe_float(pause.get("start"), -1.0)
        end = _safe_float(pause.get("end"), -1.0)
        _push("pause", (start + end) / 2.0 if start > 0 and end > 0 else (start if start > 0 else end))
    for transition in (audio_analysis.get("energyTransitions") or []):
        if not isinstance(transition, dict):
            continue
        _push("transition", transition.get("timeSec") or transition.get("time") or transition.get("t"))

    for values in boundary_signals.values():
        values.sort()
    return boundary_signals


def _pick_generation_split_point(
    *,
    scene_id: str,
    start: float,
    end: float,
    preferred_min: float,
    preferred_max: float,
    boundaries: dict[str, list[float]],
) -> tuple[float | None, str]:
    min_chunk = 2.0
    lower = start + min_chunk
    upper = end - min_chunk
    if upper <= lower:
        return None, "too_short"
    midpoint = (start + end) / 2.0
    ideal_low = start + preferred_min
    ideal_high = min(start + preferred_max, upper)

    def _best_candidate(points: list[float]) -> float | None:
        candidates = [p for p in points if lower < p < upper]
        if not candidates:
            return None
        in_band = [p for p in candidates if ideal_low <= p <= ideal_high]
        target = in_band if in_band else candidates
        return min(target, key=lambda value: abs(value - midpoint))

    pause_candidate = _best_candidate(boundaries.get("pause") or [])
    if pause_candidate is not None:
        logger.info(
            "[SCENARIO CHUNK PAUSE PICK] sceneId=%s pauseCandidate=%.3f window=[%.3f,%.3f] midpoint=%.3f",
            scene_id,
            pause_candidate,
            lower,
            upper,
            midpoint,
        )
        return round(pause_candidate, 3), "pause"

    phrase_candidate = _best_candidate(boundaries.get("phrase") or [])
    if phrase_candidate is not None:
        return round(phrase_candidate, 3), "phrase"

    safe_gap = 0.15
    phrase_start_candidates = [p for p in (boundaries.get("phrase_start") or []) if lower < p < upper]
    next_phrase_start = min([p for p in phrase_start_candidates if p > midpoint], default=None)
    if next_phrase_start is None:
        next_phrase_start = min([p for p in phrase_start_candidates if p > lower], default=None)
    if next_phrase_start is not None:
        safe_split = round(next_phrase_start - safe_gap, 3)
        if lower < safe_split < upper:
            return safe_split, "next_phrase_safe_gap"

    for kind in ("transition", "semantic"):
        candidate = _best_candidate(boundaries.get(kind) or [])
        if candidate is not None:
            return round(candidate, 3), kind
    uniform = round(midpoint, 3)
    if uniform <= lower:
        uniform = round(lower, 3)
    if uniform >= upper:
        uniform = round(upper, 3)
    return uniform, "uniform_fallback"


def _enforce_clip_phrase_and_duration_splits(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any] | None = None) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    if not scenes:
        return storyboard_out
    direct_gemini_storyboard_mode = _is_direct_gemini_storyboard_mode(payload if isinstance(payload, dict) else None)
    if direct_gemini_storyboard_mode:
        payload_map = payload if isinstance(payload, dict) else {}
        transcript_rows = []
        if isinstance(payload_map.get("_single_call_payload"), dict):
            transcript_rows = payload_map.get("_single_call_payload", {}).get("transcript") or []
        elif isinstance(payload_map.get("transcript"), list):
            transcript_rows = payload_map.get("transcript") or []
        for scene in scenes:
            scene.time_start = round(_safe_float(scene.time_start, 0.0), 3)
            scene.time_end = round(max(scene.time_start, _safe_float(scene.time_end, scene.time_start)), 3)
            scene.duration = round(max(0.0, scene.time_end - scene.time_start), 3)
            scene.requested_duration_sec = scene.duration
            route_value = str(scene.video_generation_route or scene.planned_video_generation_route or scene.resolved_workflow_key or "").strip().lower()
            is_lip_sync_route = route_value == "lip_sync_music"
            if is_lip_sync_route:
                scene.video_generation_route = "lip_sync_music"
                scene.planned_video_generation_route = "lip_sync_music"
                scene.resolved_workflow_key = "lip_sync_music"
                scene.render_mode = "lip_sync_music"
                scene.ltx_mode = "lip_sync_music"
                scene.lip_sync = True
                scene.send_audio_to_generator = True
                scene.audio_slice_kind = "music_vocal"
                scene.music_vocal_lipsync_allowed = True
                scene.audio_slice_start_sec = scene.time_start
                scene.audio_slice_end_sec = scene.time_end
                scene.audio_slice_expected_duration_sec = scene.duration
                scene.audio_slice_bounds_filled_from_scene = True
                scene.lip_sync_route_state_consistent = True
            else:
                normalized_route = route_value if route_value in {"i2v", "f_l", "blocked", "downgraded_to_i2v"} else "i2v"
                scene.video_generation_route = normalized_route
                scene.planned_video_generation_route = normalized_route
                scene.resolved_workflow_key = normalized_route if normalized_route in {"i2v", "f_l"} else (scene.resolved_workflow_key or "i2v")
                if scene.ltx_mode == "lip_sync_music":
                    scene.ltx_mode = normalized_route
                if scene.render_mode == "lip_sync_music":
                    scene.render_mode = "first_last" if normalized_route == "f_l" else "image_video"
                scene.lip_sync = False
                scene.send_audio_to_generator = False
                if str(scene.audio_slice_kind or "").strip().lower() == "music_vocal":
                    scene.audio_slice_kind = "voice_only" if str(scene.local_phrase or "").strip() else "none"
                scene.music_vocal_lipsync_allowed = False
                scene.audio_slice_start_sec = scene.time_start
                scene.audio_slice_end_sec = scene.time_end
                scene.audio_slice_expected_duration_sec = scene.duration
                scene.audio_slice_bounds_filled_from_scene = False
                scene.lip_sync_route_state_consistent = bool(scene.lip_sync) == is_lip_sync_route
        storyboard_out.scenes = scenes
        logger.info(
            "[SCENARIO DIRECT MODE] direct_gemini_storyboard_mode=true scene_merge_applied=false backend_route_override_applied=false transcript_segment_count=%s final_scene_count=%s scene_count_matches_transcript_beats=%s",
            len([row for row in transcript_rows if isinstance(row, dict)]),
            len(scenes),
            len([row for row in transcript_rows if isinstance(row, dict)]) == len(scenes) if transcript_rows else False,
        )
        return storyboard_out
    payload_map: dict[str, dict[str, Any]] = {}
    if isinstance(payload, dict):
        for item in (payload.get("scenes") or []):
            if isinstance(item, dict):
                scene_id = str(item.get("sceneId") or item.get("scene_id") or "").strip()
                if scene_id:
                    payload_map[scene_id] = item
    audio_context = _normalize_audio_context(payload if isinstance(payload, dict) else {})
    runtime_analysis = payload.get("_runtime_audio_analysis") if isinstance(payload, dict) and isinstance(payload.get("_runtime_audio_analysis"), dict) else {}
    audio_analysis = runtime_analysis if runtime_analysis else {
        "phrases": audio_context.get("phrases") or [],
        "pauseWindows": audio_context.get("pauseWindows") or [],
        "energyTransitions": audio_context.get("energyTransitions") or [],
    }
    boundaries = _collect_generation_chunk_boundaries(payload=payload if isinstance(payload, dict) else {}, audio_analysis=audio_analysis)
    logger.info(
        "[SCENARIO CHUNKING] semantic_blocks=%s raw_scenes=%s boundary_signals={pause:%s,phrase:%s,transition:%s,semantic:%s}",
        len(((payload or {}).get("_single_call_payload") or {}).get("semanticTimeline") or []),
        len(scenes),
        len(boundaries.get("pause") or []),
        len(boundaries.get("phrase") or []),
        len(boundaries.get("transition") or []),
        len(boundaries.get("semantic") or []),
    )
    next_scenes: list[ScenarioDirectorScene] = []
    split_events: list[str] = []
    split_id_counters: dict[str, int] = {}
    preferred_min = 3.0
    preferred_max = 6.0
    hard_max = 8.0
    repeat_clip_preferred_max = 4.5
    repeat_clip_merge_guard_max = 6.0

    def _sync_chunk_timing_fields(chunk_data: dict[str, Any]) -> dict[str, Any]:
        chunk_start = _safe_float(chunk_data.get("time_start"), 0.0)
        chunk_end = max(chunk_start, _safe_float(chunk_data.get("time_end"), chunk_start))
        chunk_duration = round(max(0.0, chunk_end - chunk_start), 3)
        chunk_data["time_start"] = round(chunk_start, 3)
        chunk_data["time_end"] = round(chunk_end, 3)
        chunk_data["duration"] = chunk_duration
        chunk_data["requested_duration_sec"] = chunk_duration
        chunk_data["audio_slice_start_sec"] = round(chunk_start, 3)
        chunk_data["audio_slice_end_sec"] = round(chunk_end, 3)
        chunk_data["audio_slice_expected_duration_sec"] = chunk_duration
        return chunk_data

    def _next_split_scene_id(base_scene_id: str) -> str:
        base = str(base_scene_id or "S").strip() or "S"
        index = split_id_counters.get(base, 0) + 1
        split_id_counters[base] = index
        if index <= 26:
            return f"{base}_{chr(64 + index)}"
        return f"{base}_{index}"

    for scene in scenes:
        start = _safe_float(scene.time_start, 0.0)
        end = max(start, _safe_float(scene.time_end, start))
        duration = max(0.0, _safe_float(scene.duration, end - start))
        raw_scene = payload_map.get(str(scene.scene_id or "").strip(), {})
        phrase_blob = str(scene.local_phrase or raw_scene.get("localPhrase") or raw_scene.get("local_phrase") or "").strip()
        lyric_text = str(raw_scene.get("lyricText") or raw_scene.get("lyric_text") or "").strip()
        audio_anchor = str(scene.audio_anchor_evidence or raw_scene.get("audioAnchorEvidence") or raw_scene.get("audio_anchor_evidence") or "").strip().lower()
        phrase_chunks = [chunk.strip() for chunk in re.split(r"(?:\s*[/|]\s*|\n+|(?<=[\.\!\?;])\s+)", " ".join([phrase_blob, lyric_text])) if chunk.strip()]
        phrases_inside = 0
        for phrase in (audio_analysis.get("phrases") or []):
            if not isinstance(phrase, dict):
                continue
            p_start = _safe_float(phrase.get("start"), -1.0)
            p_end = _safe_float(phrase.get("end"), -1.0)
            if p_end <= start or p_start >= end:
                continue
            phrases_inside += 1
        has_pause_boundary_inside = any(
            isinstance(pause, dict) and _safe_float(pause.get("start"), -1.0) > start + 0.2 and _safe_float(pause.get("start"), -1.0) < end - 0.2
            for pause in (audio_analysis.get("pauseWindows") or [])
        )
        merged_phrase_risk = bool(
            phrases_inside >= 2
            or len(phrase_chunks) >= 2
            or ("phrase" in audio_anchor and ("+" in audio_anchor or "next" in audio_anchor or "adjacent" in audio_anchor))
            or has_pause_boundary_inside
        )
        scene_preferred_max = repeat_clip_preferred_max if merged_phrase_risk else preferred_max
        should_split = merged_phrase_risk or duration > scene_preferred_max
        if not should_split:
            chunk_data = scene.model_dump(mode="python")
            chunk_data = _sync_chunk_timing_fields(chunk_data)
            next_scenes.append(ScenarioDirectorScene.model_validate(chunk_data))
            continue
        pending: list[tuple[dict[str, Any], int]] = [(scene.model_dump(mode="python"), 0)]
        scene_chunks: list[ScenarioDirectorScene] = []
        while pending:
            chunk_data, split_idx = pending.pop(0)
            chunk_start = _safe_float(chunk_data.get("time_start"), start)
            chunk_end = max(chunk_start, _safe_float(chunk_data.get("time_end"), chunk_start))
            chunk_duration = max(0.0, chunk_end - chunk_start)
            must_split = chunk_duration > scene_preferred_max or (duration > hard_max and split_idx == 0)
            if not must_split:
                chunk_data = _sync_chunk_timing_fields(chunk_data)
                scene_chunks.append(ScenarioDirectorScene.model_validate(chunk_data))
                continue
            split_at, split_kind = _pick_generation_split_point(
                scene_id=str(chunk_data.get("scene_id") or scene.scene_id or ""),
                start=chunk_start,
                end=chunk_end,
                preferred_min=preferred_min,
                preferred_max=preferred_max,
                boundaries=boundaries,
            )
            if split_at is None or split_at <= chunk_start or split_at >= chunk_end:
                chunk_data = _sync_chunk_timing_fields(chunk_data)
                scene_chunks.append(ScenarioDirectorScene.model_validate(chunk_data))
                continue
            left_id = _next_split_scene_id(scene.scene_id)
            right_id = _next_split_scene_id(scene.scene_id)
            left_data = {**chunk_data, "scene_id": left_id, "time_start": chunk_start, "time_end": split_at, "duration": round(max(0.0, split_at - chunk_start), 3)}
            right_data = {**chunk_data, "scene_id": right_id, "time_start": split_at, "time_end": chunk_end, "duration": round(max(0.0, chunk_end - split_at), 3)}
            left_data = _sync_chunk_timing_fields(left_data)
            right_data = _sync_chunk_timing_fields(right_data)
            if phrase_chunks:
                pivot = max(1, len(phrase_chunks) // 2)
                left_data["local_phrase"] = " ".join(phrase_chunks[:pivot]).strip() or left_data.get("local_phrase")
                right_data["local_phrase"] = " ".join(phrase_chunks[pivot:]).strip() or right_data.get("local_phrase")
            boundary_reason = split_kind if split_kind != "uniform_fallback" else "fallback"
            reason_value = "merged_phrase_risk" if merged_phrase_risk else split_kind
            left_data["boundary_reason"] = boundary_reason
            right_data["boundary_reason"] = boundary_reason
            left_data["audio_anchor_evidence"] = _append_decision_flag(left_data.get("audio_anchor_evidence"), "autoSplitReason", reason_value)
            right_data["audio_anchor_evidence"] = _append_decision_flag(right_data.get("audio_anchor_evidence"), "autoSplitReason", reason_value)
            split_events.append(f"{scene.scene_id}:{chunk_start:.3f}-{chunk_end:.3f}->{split_at:.3f}:{split_kind}")
            next_phrase_start_candidates = [
                point for point in (boundaries.get("phrase_start") or [])
                if point > split_at and point < chunk_end
            ]
            next_phrase_start = min(next_phrase_start_candidates) if next_phrase_start_candidates else None
            applied_safe_gap = round(max(0.0, (next_phrase_start - split_at)), 3) if next_phrase_start is not None and split_kind == "next_phrase_safe_gap" else 0.0
            logger.info(
                "[SCENARIO CHUNK BOUNDARY] sceneId=%s candidateBoundaries=%s pickedBoundary=%.3f pickedReason=%s nextPhraseStart=%s appliedSafeGap=%.3f finalChunk=[%.3f,%.3f]",
                str(chunk_data.get("scene_id") or scene.scene_id or ""),
                {
                    "pause": [round(v, 3) for v in (boundaries.get("pause") or []) if chunk_start < v < chunk_end][:6],
                    "phrase": [round(v, 3) for v in (boundaries.get("phrase") or []) if chunk_start < v < chunk_end][:6],
                    "phrase_start": [round(v, 3) for v in (boundaries.get("phrase_start") or []) if chunk_start < v < chunk_end][:6],
                    "transition": [round(v, 3) for v in (boundaries.get("transition") or []) if chunk_start < v < chunk_end][:6],
                    "semantic": [round(v, 3) for v in (boundaries.get("semantic") or []) if chunk_start < v < chunk_end][:6],
                },
                split_at,
                split_kind,
                f"{next_phrase_start:.3f}" if next_phrase_start is not None else "none",
                applied_safe_gap,
                chunk_start,
                chunk_end,
            )
            pending.insert(0, (right_data, split_idx + 1))
            pending.insert(0, (left_data, split_idx + 1))
        next_scenes.extend(scene_chunks)
    precise_phrase_rows: list[dict[str, Any]] = []
    fallback_phrase_rows: list[dict[str, Any]] = []
    pause_windows: list[dict[str, float]] = []
    pause_source_stats: dict[str, dict[str, int | bool]] = {}
    precise_phrase_seen: set[tuple[float, float, str]] = set()
    fallback_phrase_seen: set[tuple[float, float, str]] = set()
    pause_seen: set[tuple[float, float]] = set()

    def _push_phrase_row(
        start_value: Any,
        end_value: Any,
        text_value: Any,
        source: str,
        target_rows: list[dict[str, Any]],
        target_seen: set[tuple[float, float, str]],
    ) -> None:
        start_sec = round(_safe_float(start_value, -1.0), 3)
        end_sec = round(_safe_float(end_value, -1.0), 3)
        if start_sec < 0 or end_sec <= start_sec:
            return
        text = str(text_value or "").strip()
        key = (start_sec, end_sec, text)
        if key in target_seen:
            return
        target_seen.add(key)
        target_rows.append({"start": start_sec, "end": end_sec, "text": text, "source": source})

    def _push_pause_window(start_value: Any, end_value: Any, source: str) -> None:
        p_start = round(_safe_float(start_value, -1.0), 3)
        p_end = round(_safe_float(end_value, p_start), 3)
        if p_start < 0 or p_end <= p_start:
            return
        key = (p_start, p_end)
        if key in pause_seen:
            return
        pause_seen.add(key)
        pause_windows.append({"start": p_start, "end": p_end, "source": source})

    single_call_payload = (payload or {}).get("_single_call_payload") if isinstance((payload or {}).get("_single_call_payload"), dict) else {}
    for container, source_name in ((single_call_payload, "single_call"), (payload if isinstance(payload, dict) else {}, "payload")):
        pause_rows = (((container.get("audioStructure") or {}).get("pauses")) or [])
        pause_source_stats[source_name] = {
            "count": len(pause_rows) if isinstance(pause_rows, list) else 0,
            "t0_t1_count": 0,
            "start_end_count": 0,
            "supports_t0_t1": False,
            "supports_start_end": False,
        }
        for row in (container.get("transcript") or []):
            if isinstance(row, dict):
                _push_phrase_row(
                    row.get("t0") if row.get("t0") is not None else row.get("start"),
                    row.get("t1") if row.get("t1") is not None else row.get("end"),
                    row.get("text"),
                    source_name,
                    precise_phrase_rows,
                    precise_phrase_seen,
                )
        for row in (container.get("semanticTimeline") or []):
            if isinstance(row, dict):
                _push_phrase_row(
                    row.get("t0") if row.get("t0") is not None else row.get("startSec"),
                    row.get("t1") if row.get("t1") is not None else row.get("endSec"),
                    row.get("text"),
                    f"{source_name}_semantic",
                    fallback_phrase_rows,
                    fallback_phrase_seen,
                )
        for pause in pause_rows:
            if isinstance(pause, dict):
                has_t0_t1 = pause.get("t0") is not None or pause.get("t1") is not None
                has_start_end = pause.get("start") is not None or pause.get("end") is not None
                if has_t0_t1:
                    pause_source_stats[source_name]["t0_t1_count"] = int(pause_source_stats[source_name]["t0_t1_count"]) + 1
                    pause_source_stats[source_name]["supports_t0_t1"] = True
                if has_start_end:
                    pause_source_stats[source_name]["start_end_count"] = int(pause_source_stats[source_name]["start_end_count"]) + 1
                    pause_source_stats[source_name]["supports_start_end"] = True
                _push_pause_window(
                    pause.get("t0") if pause.get("t0") is not None else pause.get("start"),
                    pause.get("t1") if pause.get("t1") is not None else pause.get("end"),
                    f"{source_name}_audio_structure",
                )

    for source_name, stats in pause_source_stats.items():
        logger.info(
            "[SCENARIO AUDIO PAUSE SOURCE] source=%s count=%s t0_t1_count=%s start_end_count=%s supports_t0_t1=%s supports_start_end=%s",
            source_name,
            stats.get("count", 0),
            stats.get("t0_t1_count", 0),
            stats.get("start_end_count", 0),
            stats.get("supports_t0_t1", False),
            stats.get("supports_start_end", False),
        )

    for phrase in (audio_analysis.get("phrases") or []):
        if isinstance(phrase, dict):
            _push_phrase_row(
                phrase.get("start"),
                phrase.get("end"),
                phrase.get("text"),
                "audio_analysis",
                precise_phrase_rows,
                precise_phrase_seen,
            )
    for pause in (audio_analysis.get("pauseWindows") or []):
        if isinstance(pause, dict):
            _push_pause_window(pause.get("start"), pause.get("end"), "audio_analysis")

    precise_phrase_rows.sort(key=lambda row: (float(row.get("start") or 0.0), float(row.get("end") or 0.0)))
    fallback_phrase_rows.sort(key=lambda row: (float(row.get("start") or 0.0), float(row.get("end") or 0.0)))
    pause_windows.sort(key=lambda row: (float(row.get("start") or 0.0), float(row.get("end") or 0.0)))

    def _adaptive_safe_gap(gap_to_next: float) -> float:
        if gap_to_next <= 0.12:
            return 0.06
        if gap_to_next <= 0.3:
            return min(0.14, max(0.08, gap_to_next * 0.45))
        return min(0.18, max(0.1, gap_to_next * 0.35))

    def _pick_scene_anchor_phrase(scene_start: float, scene_end: float) -> dict[str, Any] | None:
        candidate_rows = precise_phrase_rows or fallback_phrase_rows
        if not candidate_rows:
            return None
        overlaps: list[tuple[float, dict[str, Any]]] = []
        for row in candidate_rows:
            p_start = _safe_float(row.get("start"), 0.0)
            p_end = _safe_float(row.get("end"), p_start)
            overlap = max(0.0, min(scene_end, p_end) - max(scene_start, p_start))
            if overlap > 0:
                overlaps.append((overlap, row))
        if overlaps:
            overlaps.sort(key=lambda item: item[0], reverse=True)
            return overlaps[0][1]
        scene_mid = (scene_start + scene_end) / 2.0
        return min(candidate_rows, key=lambda row: abs(_safe_float(row.get("start"), 0.0) - scene_mid))

    def _phrase_text_for_window(window_start: float, window_end: float) -> str | None:
        source_rows = precise_phrase_rows or fallback_phrase_rows
        matched: list[str] = []
        for row in source_rows:
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            p_start = _safe_float(row.get("start"), 0.0)
            p_end = _safe_float(row.get("end"), p_start)
            overlap = max(0.0, min(window_end, p_end) - max(window_start, p_start))
            phrase_duration = max(0.001, p_end - p_start)
            if overlap >= min(0.12, phrase_duration * 0.25):
                matched.append(text)
        unique = list(dict.fromkeys(matched))
        return " · ".join(unique).strip() or None

    for idx, chunk in enumerate(next_scenes):
        scene_start = round(_safe_float(chunk.time_start, 0.0), 3)
        scene_end = round(max(scene_start, _safe_float(chunk.time_end, scene_start)), 3)
        anchor_phrase = _pick_scene_anchor_phrase(scene_start, scene_end)
        chosen_phrase_start = _safe_float((anchor_phrase or {}).get("start"), scene_start)
        chosen_phrase_end = max(chosen_phrase_start, _safe_float((anchor_phrase or {}).get("end"), scene_end))
        previous_phrase_end = None
        next_phrase_start = None
        anchor_source_used = "precise" if precise_phrase_rows else ("semantic_fallback" if fallback_phrase_rows else "none")
        for row in (precise_phrase_rows or fallback_phrase_rows):
            p_start = _safe_float(row.get("start"), -1.0)
            p_end = _safe_float(row.get("end"), -1.0)
            if p_end <= chosen_phrase_start:
                previous_phrase_end = p_end
            if p_start > chosen_phrase_start:
                next_phrase_start = p_start
                break
        gap_before = chosen_phrase_start - previous_phrase_end if previous_phrase_end is not None else 0.2
        preroll = min(0.12, max(0.03, gap_before * 0.35))
        aligned_start = round(max(0.0, min(scene_start, chosen_phrase_start - preroll, chosen_phrase_start)), 3)
        if aligned_start > chosen_phrase_start:
            aligned_start = round(max(0.0, chosen_phrase_start - preroll), 3)

        pause_boundary = None
        pause_boundary_source = "none"
        safe_gap_used = 0.0
        aligned_end = scene_end
        if next_phrase_start is not None:
            gap_to_next = max(0.0, next_phrase_start - chosen_phrase_end)
            safe_gap_used = _adaptive_safe_gap(gap_to_next)
            for pause in pause_windows:
                p_start = _safe_float(pause.get("start"), -1.0)
                p_end = _safe_float(pause.get("end"), p_start)
                if p_start >= chosen_phrase_end and p_end <= next_phrase_start and p_end > p_start:
                    pause_boundary = round((p_start + p_end) / 2.0, 3)
                    pause_boundary_source = str(pause.get("source") or "unknown")
                    break
            phrase_end_boundary = round(chosen_phrase_end, 3)
            next_phrase_guard = round(max(aligned_start, next_phrase_start - safe_gap_used), 3)
            if pause_boundary is not None and pause_boundary > aligned_start:
                aligned_end = min(scene_end, pause_boundary)
            elif phrase_end_boundary > aligned_start:
                aligned_end = min(scene_end, phrase_end_boundary)
            elif next_phrase_guard > aligned_start:
                aligned_end = min(scene_end, next_phrase_guard)
            else:
                aligned_end = min(scene_end, max(aligned_start, next_phrase_guard))
            aligned_end = min(aligned_end, round(max(aligned_start, next_phrase_start - max(0.05, safe_gap_used)), 3))
        else:
            aligned_end = min(scene_end, round(max(aligned_start, chosen_phrase_end), 3))

        if aligned_end <= aligned_start:
            aligned_end = round(max(aligned_start, scene_end), 3)
        if aligned_end <= aligned_start:
            aligned_end = round(aligned_start + 0.05, 3)

        local_phrase = _phrase_text_for_window(aligned_start, aligned_end)
        if local_phrase:
            chunk.local_phrase = local_phrase
        chunk.audio_slice_start_sec = aligned_start
        chunk.audio_slice_end_sec = aligned_end
        chunk.audio_slice_expected_duration_sec = round(max(0.0, aligned_end - aligned_start), 3)
        logger.info(
            "[SCENARIO AUDIO ALIGN] sceneId=%s sourceUsedForAnchor=%s precisePhraseCount=%s fallbackPhraseCount=%s original=[%.3f,%.3f] chosenPhraseStart=%.3f chosenPhraseEnd=%.3f nextPrecisePhraseStart=%s alignedStart=%.3f alignedEnd=%.3f pauseBoundary=%s pauseBoundarySource=%s safeGap=%.3f",
            chunk.scene_id,
            anchor_source_used,
            len(precise_phrase_rows),
            len(fallback_phrase_rows),
            scene_start,
            scene_end,
            chosen_phrase_start,
            chosen_phrase_end,
            f"{next_phrase_start:.3f}" if next_phrase_start is not None else "none",
            aligned_start,
            aligned_end,
            f"{pause_boundary:.3f}" if pause_boundary is not None else "none",
            pause_boundary_source,
            safe_gap_used,
        )
        logger.info(
            "[SCENARIO PHRASE WINDOW] sceneId=%s sourceUsedForAnchor=%s precisePhraseCount=%s fallbackPhraseCount=%s chosenPhraseStart=%.3f chosenPhraseEnd=%.3f nextPrecisePhraseStart=%s finalWindow=[%.3f,%.3f] finalLocalPhrase=%s sourcePhrase=%s",
            chunk.scene_id,
            anchor_source_used,
            len(precise_phrase_rows),
            len(fallback_phrase_rows),
            chosen_phrase_start,
            chosen_phrase_end,
            f"{next_phrase_start:.3f}" if next_phrase_start is not None else "none",
            aligned_start,
            aligned_end,
            str(chunk.local_phrase or ""),
            str((anchor_phrase or {}).get("source") or "none"),
        )
        logger.info(
            "[SCENARIO AUDIO SLICE FINAL] sceneId=%s sourceUsedForAnchor=%s precisePhraseCount=%s fallbackPhraseCount=%s chosenPhraseStart=%.3f chosenPhraseEnd=%.3f nextPrecisePhraseStart=%s pauseBoundarySource=%s alignedStart=%.3f alignedEnd=%.3f audioSlice=[%.3f,%.3f] expected=%.3f finalLocalPhrase=%s sceneIndex=%s",
            chunk.scene_id,
            anchor_source_used,
            len(precise_phrase_rows),
            len(fallback_phrase_rows),
            chosen_phrase_start,
            chosen_phrase_end,
            f"{next_phrase_start:.3f}" if next_phrase_start is not None else "none",
            pause_boundary_source,
            aligned_start,
            aligned_end,
            chunk.audio_slice_start_sec,
            chunk.audio_slice_end_sec,
            chunk.audio_slice_expected_duration_sec,
            str(chunk.local_phrase or ""),
            idx,
        )

    def _scene_merge_payload(scene: ScenarioDirectorScene) -> dict[str, Any]:
        return {
            "sceneId": str(scene.scene_id or "").strip(),
            "t0": _safe_float(scene.time_start, 0.0),
            "t1": _safe_float(scene.time_end, _safe_float(scene.time_start, 0.0)),
            "summary": str(scene.scene_goal or scene.frame_description or "").strip(),
            "motion": str(scene.action_in_frame or "").strip(),
            "camera": str(scene.camera or "").strip(),
            "transitionHint": str(scene.boundary_reason or "").strip(),
            "environment": str(scene.location or "").strip(),
            "characters": [str(actor).strip() for actor in (scene.actors or []) if str(actor).strip()],
        }

    def _is_first_last_scene(scene: ScenarioDirectorScene) -> bool:
        render_mode = str(scene.render_mode or "").strip().lower()
        ltx_mode = str(scene.ltx_mode or "").strip().lower()
        return bool(scene.needs_two_frames) or render_mode in {"first_last", "first_last_sound"} or ltx_mode in {"f_l", "f_l_as", "first_last"}

    def _can_merge_final_scene_pair(left: ScenarioDirectorScene, right: ScenarioDirectorScene) -> tuple[bool, str]:
        if _is_first_last_scene(left) or _is_first_last_scene(right):
            return False, "first_last_scene_preserved"
        transition_tokens = " ".join([
            str(left.transition_type or "").lower(),
            str(right.transition_type or "").lower(),
            str(left.boundary_reason or "").lower(),
            str(right.boundary_reason or "").lower(),
        ])
        if any(token in transition_tokens for token in ("state_shift", "state shift", "transition", "hard_cut")):
            return False, "explicit_transition_boundary"
        if str(left.narration_mode or "").strip().lower() == "pause" or str(right.narration_mode or "").strip().lower() == "pause":
            return False, "micro_beat_pause_preserved"
        can_merge, reason = _can_merge_short_scene_pair(_scene_merge_payload(left), _scene_merge_payload(right))
        return can_merge, reason

    def _has_strong_first_last_reason(scene: ScenarioDirectorScene) -> tuple[bool, str]:
        evidence = " ".join(
            [
                str(scene.transition_type or "").strip().lower(),
                str(scene.boundary_reason or "").strip().lower(),
                str(scene.scene_purpose or "").strip().lower(),
                str(scene.ltx_reason or "").strip().lower(),
                str(scene.workflow_decision_reason or "").strip().lower(),
                str(scene.clip_decision_reason or "").strip().lower(),
                str(scene.audio_slice_decision_reason or "").strip().lower(),
            ]
        )
        strong_tokens = (
            "state_shift",
            "state shift",
            "before-after",
            "before after",
            "a→b",
            "a->b",
            "transition beat",
            "bridge",
            "cannot merge",
            "hard_cut",
            "hard cut",
            "visual transition",
            "forced_first_last",
        )
        if any(token in evidence for token in strong_tokens):
            return True, "strong_transition_evidence"
        return False, "no_strong_transition_evidence"

    def _degrade_short_first_last_scene(scene: ScenarioDirectorScene) -> str:
        if direct_gemini_storyboard_mode:
            logger.info("[SCENARIO DIRECT MODE] short first_last preserved sceneId=%s route=%s", scene.scene_id, str(scene.video_generation_route or ""))
            return "preserved_direct_mode"
        scene.needs_two_frames = False
        scene.continuation_from_previous = False
        scene.start_frame_source = "new"
        scene.render_mode = "image_video"
        scene.ltx_mode = "i2v"
        scene.resolved_workflow_key = "i2v"
        scene.resolved_workflow_file = CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY["i2v"]
        scene.transition_type = "cut"
        scene.start_frame_prompt = ""
        scene.end_frame_prompt = ""
        degrade_reason = (
            f"Short first_last scene degraded because duration below {SCENARIO_SHORT_FIRST_LAST_MIN_SEC:.1f}s without strong transition reason."
        )
        scene.ltx_reason = _normalize_ltx_reason(degrade_reason, scene.ltx_mode, narration_mode=scene.narration_mode)
        scene.video_generation_route = "downgraded_to_i2v"
        scene.planned_video_generation_route = "f_l"
        scene.video_downgrade_reason_code = "first_last_visual_delta_too_weak"
        scene.video_downgrade_reason_message = degrade_reason
        return "degraded_to_single"

    def _merge_final_generation_short_scenes(chunks: list[ScenarioDirectorScene]) -> list[ScenarioDirectorScene]:
        def _split_text_parts(value: str) -> list[str]:
            return [part.strip() for part in re.split(r"(?:\s*[|/·]\s*|\n+|(?<=[\.\!\?;:])\s+)", str(value or "")) if part.strip()]

        def _normalize_text_part(value: str) -> str:
            text = str(value or "").lower().strip()
            text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
            text = re.sub(r"\s+", " ", text).strip()
            return text

        def _dedupe_repeated_scene_text_parts(scene: ScenarioDirectorScene, field_name: str) -> None:
            raw_value = str(getattr(scene, field_name, "") or "").strip()
            if not raw_value:
                return
            parts = _split_text_parts(raw_value)
            if len(parts) <= 1:
                return
            deduped: list[str] = []
            deduped_norm: list[str] = []
            for part in parts:
                norm = _normalize_text_part(part)
                if not norm:
                    continue
                near_duplicate = False
                norm_tokens = set(norm.split())
                for prev_norm in deduped_norm:
                    prev_tokens = set(prev_norm.split())
                    shared = len(norm_tokens.intersection(prev_tokens))
                    union = max(1, len(norm_tokens.union(prev_tokens)))
                    jaccard = shared / union
                    if norm == prev_norm or norm in prev_norm or prev_norm in norm or jaccard >= 0.82:
                        near_duplicate = True
                        break
                if near_duplicate:
                    continue
                deduped.append(part)
                deduped_norm.append(norm)
            if not deduped:
                deduped = [parts[0]]
            if len(deduped) != len(parts):
                logger.info(
                    "[SCENARIO TEXT DEDUPE] %s",
                    {
                        "sceneId": str(scene.scene_id or "").strip(),
                        "field": field_name,
                        "beforeCount": len(parts),
                        "afterCount": len(deduped),
                    },
                )
            setattr(scene, field_name, " / ".join(deduped))

        def _role_pattern(scene: ScenarioDirectorScene) -> str:
            dynamics = str(scene.scene_role_dynamics or "").lower()
            if "solo" in dynamics:
                return "solo"
            if "duet" in dynamics:
                return "duet"
            actor_count = len([str(actor).strip() for actor in (scene.actors or []) if str(actor).strip()])
            if actor_count <= 1:
                return "solo"
            if actor_count == 2:
                return "duet"
            return "group"

        def _contains_repeated_local_phrase(scene: ScenarioDirectorScene) -> bool:
            parts = [part for part in _split_text_parts(str(scene.local_phrase or "")) if _normalize_text_part(part)]
            if len(parts) <= 1:
                return False
            normalized_parts = [_normalize_text_part(part) for part in parts if _normalize_text_part(part)]
            return len(set(normalized_parts)) < len(normalized_parts)

        def _has_repeated_text_fields(scene: ScenarioDirectorScene) -> bool:
            for field_name in ("scene_goal", "frame_description", "action_in_frame"):
                parts = _split_text_parts(str(getattr(scene, field_name, "") or ""))
                if len(parts) <= 1:
                    continue
                normalized_parts = [_normalize_text_part(part) for part in parts if _normalize_text_part(part)]
                if len(set(normalized_parts)) < len(normalized_parts):
                    return True
            return False

        def _should_block_repeat_merge(left: ScenarioDirectorScene, right: ScenarioDirectorScene) -> tuple[bool, str, dict[str, Any]]:
            left_duration = max(0.0, _safe_float(left.time_end, 0.0) - _safe_float(left.time_start, 0.0))
            right_duration = max(0.0, _safe_float(right.time_end, 0.0) - _safe_float(right.time_start, 0.0))
            merged_duration = left_duration + right_duration
            merged_phrase_count = int(_safe_float(getattr(left, "scene_phrase_count", 0), 0)) + int(
                _safe_float(getattr(right, "scene_phrase_count", 0), 0)
            )
            local_phrase_repeated = _contains_repeated_local_phrase(left) or _contains_repeated_local_phrase(right)
            repeated_text = _has_repeated_text_fields(left) or _has_repeated_text_fields(right)
            role_pattern_changed = _role_pattern(left) != _role_pattern(right)
            boundary_glue = " ".join([str(left.boundary_reason or "").lower(), str(right.boundary_reason or "").lower()])
            repeated_boundary = any(token in boundary_glue for token in ("fallback", "pause", "phrase", "glue", "repeat"))
            reason = ""
            if role_pattern_changed:
                reason = "role_pattern_changed"
            elif merged_phrase_count > 3:
                reason = "scene_phrase_count_guard"
            elif merged_duration >= repeat_clip_merge_guard_max and (local_phrase_repeated or repeated_text or repeated_boundary):
                reason = "repeat_heavy_duration_cap"
            elif merged_duration >= hard_max and (local_phrase_repeated or repeated_text):
                reason = "repeat_heavy_hard_cap"
            debug_payload = {
                "sceneId": str(left.scene_id or "").strip(),
                "neighborSceneId": str(right.scene_id or "").strip(),
                "reason": reason or "none",
                "currentDurationSec": round(left_duration, 3),
                "nextDurationSec": round(right_duration, 3),
                "mergedDurationSec": round(merged_duration, 3),
                "scenePhraseCount": merged_phrase_count,
                "repeatedTextDetected": repeated_text,
                "repeatedLocalPhraseDetected": local_phrase_repeated,
                "rolePatternChanged": role_pattern_changed,
            }
            return bool(reason), reason, debug_payload

        if len(chunks) < 2:
            return chunks
        merged_chunks = list(chunks)
        idx = 0
        while idx < len(merged_chunks):
            scene = merged_chunks[idx]
            duration = max(0.0, _safe_float(scene.time_end, 0.0) - _safe_float(scene.time_start, 0.0))
            is_first_last = _is_first_last_scene(scene)
            strong_reason_detected = False
            short_first_last_reason = "not_first_last_or_long_enough"
            if is_first_last and duration < SCENARIO_SHORT_FIRST_LAST_MIN_SEC:
                strong_reason_detected, short_first_last_reason = _has_strong_first_last_reason(scene)
                logger.info(
                    "[SCENARIO SHORT FIRST_LAST] sceneId=%s durationSec=%.3f renderMode=%s ltxMode=%s needsTwoFrames=%s strongReasonDetected=%s reason=%s",
                    scene.scene_id,
                    duration,
                    str(scene.render_mode or ""),
                    str(scene.ltx_mode or ""),
                    bool(scene.needs_two_frames),
                    "yes" if strong_reason_detected else "no",
                    short_first_last_reason,
                )
                if strong_reason_detected:
                    logger.info(
                        "[SCENARIO SHORT FIRST_LAST DECISION] sceneId=%s durationSec=%.3f renderMode=%s ltxMode=%s needsTwoFrames=%s strongReasonDetected=yes action=kept_first_last reason=%s",
                        scene.scene_id,
                        duration,
                        str(scene.render_mode or ""),
                        str(scene.ltx_mode or ""),
                        bool(scene.needs_two_frames),
                        short_first_last_reason,
                    )
                else:
                    action = _degrade_short_first_last_scene(scene)
                    logger.info(
                        "[SCENARIO SHORT FIRST_LAST DECISION] sceneId=%s durationSec=%.3f renderMode=%s ltxMode=%s needsTwoFrames=%s strongReasonDetected=no action=%s reason=%s",
                        scene.scene_id,
                        duration,
                        str(scene.render_mode or ""),
                        str(scene.ltx_mode or ""),
                        bool(scene.needs_two_frames),
                        action,
                        short_first_last_reason,
                    )
            if duration >= SCENARIO_CHUNK_PREFERRED_MIN_SEC:
                idx += 1
                continue
            logger.info("[SCENARIO FINAL MIN DURATION] sceneId=%s durationSec=%.3f preferredMinimumSec=%.1f hardMinimumSec=%.1f",
                        scene.scene_id, duration, SCENARIO_CHUNK_PREFERRED_MIN_SEC, SCENARIO_CHUNK_HARD_MIN_SEC)
            merged = False
            for neighbor_idx in (idx + 1, idx - 1):
                if neighbor_idx < 0 or neighbor_idx >= len(merged_chunks):
                    continue
                neighbor = merged_chunks[neighbor_idx]
                can_merge, reason = _can_merge_final_scene_pair(scene, neighbor)
                if not can_merge:
                    logger.info("[SCENARIO FINAL SCENE MERGE] action=kept_separate sceneId=%s neighborSceneId=%s reason=%s durationSec=%.3f",
                                scene.scene_id, neighbor.scene_id, reason, duration)
                    continue
                left_idx, right_idx = sorted([idx, neighbor_idx])
                left = merged_chunks[left_idx]
                right = merged_chunks[right_idx]
                block_merge, block_reason, block_payload = _should_block_repeat_merge(left, right)
                if block_merge:
                    logger.info("[SCENARIO REPEAT MERGE BLOCK] %s", block_payload)
                    logger.info("[SCENARIO FINAL SCENE MERGE] action=kept_separate sceneId=%s neighborSceneId=%s reason=%s durationSec=%.3f",
                                scene.scene_id, neighbor.scene_id, block_reason, duration)
                    continue
                left.time_start = round(_safe_float(left.time_start, 0.0), 3)
                left.time_end = round(max(_safe_float(left.time_end, 0.0), _safe_float(right.time_end, 0.0)), 3)
                left.duration = round(max(0.0, left.time_end - left.time_start), 3)
                left.requested_duration_sec = left.duration
                left.audio_slice_start_sec = round(min(_safe_float(left.audio_slice_start_sec, left.time_start), _safe_float(right.audio_slice_start_sec, right.time_start)), 3)
                left.audio_slice_end_sec = round(max(_safe_float(left.audio_slice_end_sec, left.time_end), _safe_float(right.audio_slice_end_sec, right.time_end)), 3)
                left.audio_slice_expected_duration_sec = round(max(0.0, left.audio_slice_end_sec - left.audio_slice_start_sec), 3)
                left.actors = list(dict.fromkeys([*(left.actors or []), *(right.actors or [])]))
                left.local_phrase = " · ".join(
                    [
                        text
                        for text in [
                            str(getattr(left, "local_phrase", "") or "").strip(),
                            str(getattr(right, "local_phrase", "") or "").strip(),
                        ]
                        if text
                    ]
                ).strip()
                left.scene_phrase_count = int(_safe_float(getattr(left, "scene_phrase_count", 0), 0)) + int(
                    _safe_float(getattr(right, "scene_phrase_count", 0), 0)
                )
                if str(right.scene_goal or "").strip():
                    left.scene_goal = " / ".join([part for part in [str(left.scene_goal or "").strip(), str(right.scene_goal or "").strip()] if part])
                if str(right.frame_description or "").strip():
                    left.frame_description = " / ".join([part for part in [str(left.frame_description or "").strip(), str(right.frame_description or "").strip()] if part])
                if str(right.action_in_frame or "").strip():
                    left.action_in_frame = " / ".join([part for part in [str(left.action_in_frame or "").strip(), str(right.action_in_frame or "").strip()] if part])
                for field_name in ("scene_goal", "frame_description", "action_in_frame", "local_phrase"):
                    _dedupe_repeated_scene_text_parts(left, field_name)
                merged_chunks.pop(right_idx)
                logger.info("[SCENARIO FINAL SCENE MERGE] action=merged sceneId=%s mergedWithSceneId=%s reason=%s resultDurationSec=%.3f",
                            left.scene_id, right.scene_id, reason, _safe_float(left.duration, 0.0))
                if not strong_reason_detected and duration < SCENARIO_SHORT_FIRST_LAST_MIN_SEC:
                    logger.info(
                        "[SCENARIO SHORT FIRST_LAST DECISION] sceneId=%s durationSec=%.3f renderMode=%s ltxMode=%s needsTwoFrames=%s strongReasonDetected=no action=merged_with_neighbor reason=%s",
                        left.scene_id,
                        duration,
                        str(left.render_mode or ""),
                        str(left.ltx_mode or ""),
                        bool(left.needs_two_frames),
                        reason,
                    )
                merged = True
                idx = max(0, left_idx - 1)
                break
            if not merged:
                if is_first_last and duration < SCENARIO_SHORT_FIRST_LAST_MIN_SEC and not strong_reason_detected:
                    logger.info(
                        "[SCENARIO SHORT FIRST_LAST DECISION] sceneId=%s durationSec=%.3f renderMode=%s ltxMode=%s needsTwoFrames=%s strongReasonDetected=no action=degraded_to_single reason=no_safe_neighbor_merge",
                        scene.scene_id,
                        duration,
                        str(scene.render_mode or ""),
                        str(scene.ltx_mode or ""),
                        bool(scene.needs_two_frames),
                    )
                logger.info("[SCENARIO FINAL SCENE MERGE] action=kept_short_scene sceneId=%s durationSec=%.3f reason=no_safe_neighbor_merge",
                            scene.scene_id, duration)
                idx += 1
        for merged_scene in merged_chunks:
            for field_name in ("scene_goal", "frame_description", "action_in_frame", "local_phrase"):
                _dedupe_repeated_scene_text_parts(merged_scene, field_name)
        return merged_chunks

    if direct_gemini_storyboard_mode:
        logger.info("[SCENARIO DIRECT MODE] scene merge disabled direct_gemini_storyboard_mode=true scene_merge_applied=false transcript_segment_count=%s final_scene_count=%s", len([row for row in (payload or {}).get("transcript", []) if isinstance(row, dict)]), len(next_scenes))
    else:
        next_scenes = _merge_final_generation_short_scenes(next_scenes)
    no_text_mode = str(storyboard_out.diagnostics.no_text_clip_policy or "").strip().lower() == "visual_arc_over_phrase_loop"
    if no_text_mode:
        phrase_loop_before = _is_repeat_heavy_music_clip(storyboard_out.scenes or [])
        phrase_loop_after = _is_repeat_heavy_music_clip(next_scenes)
        storyboard_out.diagnostics.chorus_detected = bool(phrase_loop_before)
        storyboard_out.diagnostics.phrase_loop_detected = bool(phrase_loop_before)
        storyboard_out.diagnostics.phrase_loop_prevention_action = "chunk_merge" if (phrase_loop_before and not phrase_loop_after) else ""
        storyboard_out.diagnostics.phrase_loop_prevented = bool(phrase_loop_before and not phrase_loop_after)
        storyboard_out.diagnostics.phrase_loop_prevented_reason = (
            "repeat_heavy_before_and_resolved_after_merge"
            if storyboard_out.diagnostics.phrase_loop_prevented
            else "repeat_pattern_not_resolved_by_chunk_merge"
        )
        storyboard_out.diagnostics.phrase_loop_prevention_reason = storyboard_out.diagnostics.phrase_loop_prevented_reason
        storyboard_out.diagnostics.scene_merge_or_reuse_reason = (
            "visual_arc_progression_merge_guard"
            if storyboard_out.diagnostics.phrase_loop_prevented
            else "visual_arc_policy_applied_no_merge_needed"
        )
    logger.info("[SCENARIO CHUNKING] generation_scenes=%s split_events=%s", len(next_scenes), split_events or ["none"])
    for chunk in next_scenes:
        logger.info(
            "[SCENARIO CHUNK SPLIT] sceneId=%s start=%.3f end=%.3f duration=%.3f requested=%.3f audioSlice=[%.3f,%.3f] expected=%.3f boundary=%s",
            chunk.scene_id,
            _safe_float(chunk.time_start, 0.0),
            _safe_float(chunk.time_end, 0.0),
            _safe_float(chunk.duration, max(0.0, _safe_float(chunk.time_end, 0.0) - _safe_float(chunk.time_start, 0.0))),
            _safe_float(chunk.requested_duration_sec, 0.0),
            _safe_float(chunk.audio_slice_start_sec, 0.0),
            _safe_float(chunk.audio_slice_end_sec, 0.0),
            _safe_float(chunk.audio_slice_expected_duration_sec, 0.0),
            str(chunk.boundary_reason or ""),
        )
    storyboard_out.scenes = next_scenes
    return storyboard_out


def _harden_storyboard_out(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> ScenarioDirectorStoryboardOut:
    # Gemini output is the source of truth: keep only production-safe normalization,
    # avoid semantic rewriting of scene count/timing/story arc.
    storyboard_out = _normalize_scene_timeline(storyboard_out)
    for scene in (storyboard_out.scenes or []):
        scene.video_negative_prompt = build_ltx_video_negative_prompt(scene)
    _assert_storyboard_quality(storyboard_out)
    return storyboard_out


def _validate_scene_audio_grounding(scene: ScenarioDirectorScene, audio_context: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    usage_text = str(scene.what_from_audio_this_scene_uses or "").strip().lower()
    usage_words = {word for word in re.findall(r"[a-z]+", usage_text)}
    if usage_words and usage_words.issubset(ABSTRACT_AUDIO_ONLY_WORDS):
        risks.append("abstract_audio_usage")
    elif usage_words and usage_words.intersection(ABSTRACT_AUDIO_ONLY_WORDS):
        context = audio_context if isinstance(audio_context, dict) else {}
        dynamic_sources: list[str] = []
        audio_understanding = context.get("audioUnderstanding")
        if isinstance(audio_understanding, dict):
            dynamic_sources.append(str(audio_understanding.get("mainTopic") or ""))
            dynamic_sources.append(str(audio_understanding.get("worldContext") or ""))
            implied_events = audio_understanding.get("impliedEvents")
            if isinstance(implied_events, list):
                dynamic_sources.extend(str(item or "") for item in implied_events)
        dynamic_sources.extend(
            [
                str(context.get("mainTopic") or ""),
                str(context.get("worldContext") or ""),
            ]
        )
        implied_events = context.get("impliedEvents")
        if isinstance(implied_events, list):
            dynamic_sources.extend(str(item or "") for item in implied_events)

        dynamic_keywords = {
            token
            for token in re.findall(r"[a-z]+", " ".join(dynamic_sources).lower())
            if len(token) > 3 and token not in ABSTRACT_AUDIO_ONLY_WORDS
        }
        concrete_keyword_set = set(CONCRETE_AUDIO_HINT_WORDS).union(dynamic_keywords)
        has_concrete_anchor = bool(usage_words.intersection(concrete_keyword_set))
        if not has_concrete_anchor:
            risks.append("abstract_audio_usage")

    anchor_evidence = str(scene.audio_anchor_evidence or "").strip()
    if not anchor_evidence:
        risks.append("missing_audio_anchor_evidence")
    elif len(anchor_evidence) < 10:
        risks.append("weak_audio_anchor")

    context = audio_context if isinstance(audio_context, dict) else {}
    boundary_reason = str(scene.boundary_reason or "").strip().lower()
    if boundary_reason == "phrase" and not (context.get("phrases") or []):
        risks.append("invalid_phrase_boundary")
    if boundary_reason == "pause" and not (context.get("pauseWindows") or []):
        risks.append("invalid_pause_boundary")
    if boundary_reason == "energy" and not (context.get("energyTransitions") or []):
        risks.append("invalid_energy_boundary")

    if _safe_float(scene.confidence, 0.5) < 0.4:
        risks.append("low_scene_confidence")
    return list(dict.fromkeys(risks))


def _validate_world_consistency(scene: ScenarioDirectorScene, audio_understanding: dict[str, Any]) -> list[str]:
    audio_data = audio_understanding if isinstance(audio_understanding, dict) else {}
    world_context = str(audio_data.get("worldContext") or "").strip().lower()
    main_topic = str(audio_data.get("mainTopic") or "").strip().lower()
    implied_events_raw = audio_data.get("impliedEvents")
    implied_events_text = " ".join(str(item or "") for item in implied_events_raw) if isinstance(implied_events_raw, list) else ""
    audio_world_text = " ".join([world_context, main_topic, implied_events_text]).strip()
    scene_location = str(scene.location or "").strip().lower()
    if not audio_world_text or not scene_location:
        return []
    if any(token in audio_world_text for token in WORLD_AUDIO_KEYWORDS) and any(token in scene_location for token in WORLD_MISMATCH_LOCATION_KEYWORDS):
        return ["world_mismatch"]
    return []


def _validate_audio_first_integrity(
    storyboard_out: ScenarioDirectorStoryboardOut,
    structured_planner_diagnostics: dict[str, Any],
    audio_analysis: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = structured_planner_diagnostics if isinstance(structured_planner_diagnostics, dict) else {}
    narrative_strategy = diagnostics.get("narrativeStrategy") if isinstance(diagnostics.get("narrativeStrategy"), dict) else {}
    planner_diagnostics = diagnostics.get("diagnostics") if isinstance(diagnostics.get("diagnostics"), dict) else {}
    audio_understanding = diagnostics.get("audioUnderstanding") if isinstance(diagnostics.get("audioUnderstanding"), dict) else {}
    audio_context = audio_analysis if isinstance(audio_analysis, dict) else {}

    scene_risk_map: list[dict[str, Any]] = []
    scene_risk_total = 0.0
    for scene in storyboard_out.scenes:
        scene_risks = _validate_scene_audio_grounding(scene, audio_context)
        scene_risks.extend(_validate_world_consistency(scene, audio_understanding))
        scene_risks = list(dict.fromkeys(scene_risks))
        for risk in scene_risks:
            if risk in HIGH_SEVERITY_RISKS:
                scene_risk_total += 0.12
            elif risk in LOW_SEVERITY_RISKS:
                scene_risk_total += 0.06
            else:
                scene_risk_total += 0.08
        scene_risk_map.append({"sceneId": scene.scene_id, "risks": scene_risks})

    global_risks: list[str] = []
    if not _coerce_bool(narrative_strategy.get("didAudioRemainPrimary"), False):
        global_risks.append("didAudioRemainPrimary_false")
    if _coerce_bool(planner_diagnostics.get("usedAudioOnlyAsMood"), False):
        global_risks.append("usedAudioOnlyAsMood_true")
    if not _coerce_bool(planner_diagnostics.get("usedAudioAsContentSource"), False):
        global_risks.append("usedAudioAsContentSource_false")
    global_risks = list(dict.fromkeys(global_risks))

    score = 1.0 - scene_risk_total - (0.18 * len(global_risks))
    score = max(0.0, min(1.0, round(score, 3)))
    return {
        "sceneRiskMap": scene_risk_map,
        "globalRisks": global_risks,
        "score": score,
    }


def _build_request_text(
    payload: dict[str, Any],
    *,
    audio_context: dict[str, Any] | None = None,
    audio_analysis: dict[str, Any] | None = None,
    audio_guidance: dict[str, Any] | None = None,
    audio_semantics: dict[str, Any] | None = None,
    strict_json_retry: bool = False,
) -> str:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    director_controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    connected_context_summary = payload.get("connected_context_summary", {})
    metadata = payload.get("metadata", {})
    normalized_audio = audio_context if isinstance(audio_context, dict) else _normalize_audio_context(payload)
    runtime_analysis = audio_analysis if isinstance(audio_analysis, dict) else _build_audio_analysis_fallback(
        _safe_float(normalized_audio.get("audioDurationSec"), 0.0), "analysis_not_requested"
    )
    runtime_guidance = audio_guidance if isinstance(audio_guidance, dict) else {}
    runtime_semantics = audio_semantics if isinstance(audio_semantics, dict) else {}
    audio_duration_sec = _safe_float(
        runtime_analysis.get("audioDurationSec"),
        _safe_float(normalized_audio.get("audioDurationSec"), 0.0),
    )
    audio_duration_source = "analysis" if _safe_float(runtime_analysis.get("audioDurationSec"), 0.0) > 0 else str(normalized_audio.get("audioDurationSource") or "missing")
    source_mode = str(normalized_audio.get("sourceMode") or source.get("source_mode") or "").strip().lower()
    source_origin = str(normalized_audio.get("sourceOrigin") or source.get("source_origin") or payload.get("sourceOrigin") or "connected").strip().lower()
    audio_connected = bool(normalized_audio.get("hasAudio"))
    prefer_audio_over_text = _coerce_bool(normalized_audio.get("preferAudioOverText"), True)
    content_type_policy = _get_content_type_policy(payload)
    is_music_video_mode = str(content_type_policy.get("value") or "").strip().lower() == "music_video"
    effective_director_note_text = _resolve_effective_director_note_text(payload)
    no_text_fallback_mode = "neutral_audio_literal" if not effective_director_note_text else "off"
    authorial_interpretation_level = "low" if no_text_fallback_mode == "neutral_audio_literal" else "medium"
    audio_literalness_level = "high" if no_text_fallback_mode == "neutral_audio_literal" else "balanced"
    global_genre_intent = _resolve_director_genre_intent(payload, None)
    story_core_source = "director_note" if is_music_video_mode and effective_director_note_text else "source_of_truth"
    story_frame_source = "director_note" if is_music_video_mode and effective_director_note_text else "source_of_truth"
    rhythm_source = "audio" if is_music_video_mode else ""
    story_frame_source_reason = "director_note_present_or_payload_text" if is_music_video_mode and effective_director_note_text else "director_note_empty_use_source_truth"
    rhythm_source_reason = "audio_drives_pacing_and_transitions" if is_music_video_mode else ""
    cast_identity_lock = _build_music_video_cast_identity_lock(payload) if is_music_video_mode else {"enabled": False}
    story_core_reason = (
        "music_video_with_effective_director_note_story_frame_plus_audio_rhythm_driver"
        if story_core_source == "director_note"
        else "music_video_without_director_note_or_non_music_mode_defaults_to_source_truth"
    )
    raw_role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    effective_role_type_by_role, _, _ = _resolve_effective_role_type_by_role(payload)
    role_type_by_role: dict[str, str] = {}
    for role, role_type in raw_role_type_by_role.items():
        normalized_role = _normalize_scenario_role(role)
        clean_type = str(role_type or "").strip().lower()
        if normalized_role and clean_type in ALLOWED_EXPLICIT_ROLE_TYPES:
            role_type_by_role[normalized_role] = clean_type
    for role, role_type in effective_role_type_by_role.items():
        normalized_role = _normalize_scenario_role(role)
        if normalized_role:
            role_type_by_role[normalized_role] = str(role_type or "").strip().lower() or "auto"
    runtime_payload = {
        "mode": "oneshot",
        "audioUrl": normalized_audio.get("audioUrl"),
        "audioDurationSec": audio_duration_sec if audio_duration_sec > 0 else None,
        "text": effective_director_note_text or None,
        "refsByRole": _collect_payload_refs_by_role(payload),
        "context_refs": context_refs,
        "roleTypeByRole": role_type_by_role,
        "format": str(director_controls.get("format") or metadata.get("format") or "9:16").strip() or "9:16",
        "contentType": "music_video",
        "preferAudioOverText": prefer_audio_over_text,
        "metadata": {
            "sourceOrigin": source_origin,
            "sourceMode": source_mode,
            "audio": metadata.get("audio") if isinstance(metadata.get("audio"), dict) else {},
        },
        "audioAnalysis": {
            "ok": runtime_analysis.get("ok"),
            "audioDurationSec": runtime_analysis.get("audioDurationSec"),
            "phrases": runtime_analysis.get("phrases") or [],
            "pauseWindows": runtime_analysis.get("pauseWindows") or [],
            "energyTransitions": runtime_analysis.get("energyTransitions") or [],
        },
    }
    retry_suffix = JSON_ONLY_RETRY_SUFFIX if strict_json_retry else ""
    return (
        "You are the ONLY scenario writer/director/router for a music video.\n"
        "Return ONE JSON object only (no markdown/comments).\n"
        "NO hidden assumptions. First explain how you interpreted the inputs, then return storyboard.\n"
        "Top-level JSON must stay compact and compatible:\n"
        "{input_understanding:{audio_visual_read,character_identity_read,location_specification_level,default_world_choice_if_unspecified,marine_words_interpretation,planned_scene_types,lip_sync_importance,identity_lock_importance,same_character_across_all_scenes,can_choose_routes_independently,will_avoid},storyboard:{story_summary,full_scenario,voice_script,director_summary,audio_understanding,narrative_strategy,diagnostics:{total_duration,scene_count},scenes:[{scene_id,start_time_sec,end_time_sec,route,performance_framing,description,content_tags}]}}\n"
        "location_specification_level allowed values: fully_specified | partially_specified | unspecified.\n"
        "marine_words_interpretation allowed values: literal | metaphorical | mixed.\n"
        "Fill input_understanding fields meaningfully from CURRENT inputs (do not leave generic placeholders).\n"
        "If runtime text is null, treat it as no user text mode (do not infer text from filenames/source labels/previews).\n"
        "Keep phrase/word scene segmentation aligned to audio phrases; do NOT optimize by reducing scene count.\n"
        "Scene boundaries must end at natural phrase ends or just before a safe post-phrase spill.\n"
        "Do not let scene end drift after a phrase break, even by a few trailing letters/syllables.\n"
        "If phrase break occurs earlier, prefer the earlier boundary.\n"
        "First scene is NOT a special intro by default; do not stretch it unless intro is explicitly present in audio.\n"
        "No intro invention: do not extend opening scene just because track starts at 0.0.\n"
        "Keep segmentation tight around real vocal phrase boundaries.\n"
        "Scene count may remain phrase-based and compact-director mapping must stay compatible.\n"
        "Preserve audio-first timing and natural phrase alignment.\n"
        "Story arc canon is mandatory even in compact mode: build ENTRY -> DEVELOPMENT/EVENT -> ENDING/RESOLUTION.\n"
        "Meaning canon: reveal meaning through emotional progression, performance progression, and environment/zone progression; do not rely on repeating one spin/dress motif.\n"
        "Short clips still require complete mini-arc feeling (entry, progression, ending), not random excerpt feel.\n"
        "Long clips keep opening/development/ending macro-arc and may include sub-arcs, refrain returns, and secondary turns in the middle.\n"
        "Performance-first clips should not force literal plot, but must still communicate beginning/middle/ending emotional flow.\n"
        "Think like a top-tier cinematic music video director, not a literal lyric illustrator.\n"
        "Prioritize photoreal cinematic, emotionally alive, shootable performance imagery in EVERY scene.\n"
        "Weak/repetitive/poetic lyrics are emotional cues, not mandatory literal world instructions.\n"
        "If effective director note text exists (including payload.text), treat it as an active story-frame/world-direction instruction.\n"
        "The clip must live in ONE coherent real-world venue family across all scenes (same world continuity is hard).\n"
        "Opening scene must establish this grounded photoreal baseline immediately (no editorial/fashion abstraction baseline).\n"
        "If no location ref is provided, choose one production-friendly real venue and keep it stable across the whole clip.\n"
        "Prefer progression through connected sub-zones/angles/blocking/lighting inside that same venue family when it strengthens scene evolution.\n"
        "Intentional setup repetition is allowed when it clearly serves refrain, emphasis, intimacy, tension, or dramatic hold.\n"
        "If location ref exists, treat it as a hard anchor and respect it.\n"
        "Hard negative defaults unless explicitly requested: no salt plains, no barren desert, no cracked wasteland, no repetitive desolate emptiness.\n"
        "Marine/desolation words should usually become lighting mood/atmosphere/emotional tone, not literal ground texture.\n"
        "Prefer wow-factor performance decisions over low-value literal lyric illustration.\n"
        "Route planning must avoid all-i2v output for vocal-driven clips.\n"
        "For ~30s vocal music clip, lip_sync_music is mandatory in multiple scenes: minimum 2 scenes.\n"
        "Pick strongest hook/vocal lines for lip_sync_music with clear face-readable emotional performance beats.\n"
        "For lip_sync_music scenes, source-of-truth framing must be decided and written by you directly in scene description (not left for downstream correction).\n"
        "For lip_sync_music scenes, default framing is tight medium / medium / 3/4 body performance framing.\n"
        "For lip_sync_music scenes, keep lower frame boundary around slightly below waist up to upper thigh when possible.\n"
        "For lip_sync_music scenes, keep face/mouth/neck/shoulders/upper torso clearly readable; include hands when performance helps expression.\n"
        "For lip_sync_music scenes, stage singer-performance-first: performer stays camera-readable, direct eye contact (or near-camera eye line) is preferred on strong lines, only gentle head turns/subtle lean/soft sway/phrase-timed hand gestures are allowed.\n"
        "For lip_sync_music scenes, do NOT use spin-first/twirl-first/full-body dance silhouette/overhead dance spectacle as the primary idea.\n"
        "For lip_sync_music scenes, camera can move but must remain slow and controlled: gentle push-in, slight lateral drift, slow eye-level arc only.\n"
        "For lip_sync_music scenes, forbid overhead orbit, top-down rotation, camera roll, spinning around head, aggressive zoom-out, and fast retreating camera.\n"
        "For lip_sync_music scenes, background extras may move softly but must stay secondary and non-distracting; never turn frame into chaotic dance floor.\n"
        "Do NOT describe lip_sync_music scenes primarily as face-only close-up by default.\n"
        "Avoid pure close-up face framing for lip_sync_music unless the strongest beat explicitly requires that close emotional intent.\n"
        "If the strongest beat is better served by close-up or full-body framing, keep that intentional framing choice and make the reason explicit in description.\n"
        "For non-lip scenes, do NOT inherit lip-sync portrait defaults; prioritize action blocking, spatial progression, and venue zone readability.\n"
        "For non-lip i2v scenes, write ACTION/SPACE/BEAT-first (movement, zone progression, gesture, atmosphere), not portrait-only reads.\n"
        "For non-lip i2v scenes, preserve energy but keep motion model-safe: smooth groove/controlled weight shift/soft step-pivot-sway/phrase-based accents/moderate tempo body rhythm.\n"
        "For non-lip i2v scenes, avoid jerky dance, fast flailing arms, abrupt spins, violent head whipping, high-frequency shaking, and extreme rotation velocity.\n"
        "For non-lip i2v scenes, avoid violent spins/repeated twirls/aggressive fabric sweeps as primary action; prefer camera-led energy with safer body motion.\n"
        "If non-lip scene uses orbit/reveal wrap, orbit must be slow horizontal eye-level/chest-level/waist-level arc; no overhead path, no top orbit, no barrel-roll horizon tilt.\n"
        "For non-lip scenes, preserve intended camera diversity (wide, low-angle, overhead, tracking, crowd/action staging) instead of collapsing to portrait framing.\n"
        "Concert/festival scenes must keep physically plausible performer placement: never standing on audience heads, never floating over crowd, never impossible support planes.\n"
        "Inside one venue, prefer different connected sub-zones across scenes (barricade, side aisle, walkway gap, platform edge, stage-side rail, backstage side entry, merch/bar alley).\n"
        "Each scene must include at least one of: vocal performance, body performance, camera performance, emotional performance.\n"
        "Avoid generic standing, repetitive looking-around, endless spinning, bland symbolic walking, mannequin stiffness.\n"
        "Repeated phrases should usually escalate visually and cinematically; keep intentional repetition when it is the stronger dramatic/music choice.\n"
        "If character refs exist, identity lock is mandatory across ALL scenes: same exact face, hair, body silhouette/proportions, outfit identity, and overall styling.\n"
        "Never reinterpret or drift character identity (no face drift, wardrobe drift, body drift, or random restyling).\n"
        "Do not reduce scenes artificially.\n"
        "Do not optimize for old clip formulas or fixed route quotas.\n"
        "input_understanding must explicitly state world/location interpretation, literal-vs-metaphorical reading, lip-sync importance, identity-lock importance, route freedom, and what you will avoid.\n"
        "Route is REQUIRED in every scene and must be strict enum: i2v | lip_sync_music | first_last.\n"
        "Descriptions and content_tags must encode grounded photoreal visual intent, performance intent, and wow-factor decisions.\n"
        "performance_framing should be explicit per scene when possible using compact values (tight_medium | medium | three_quarter | close_emotional | wide_action | full_body_action).\n"
        "If route=lip_sync_music and performance_framing is missing, treat output as incomplete and repair before returning JSON.\n"
        "System will translate your compact output into production contract and execute it.\n"
        f"Raw inputs: {json.dumps(runtime_payload, ensure_ascii=False)}"
        f"{retry_suffix}"
    )


def _build_audio_coverage_refinement_prompt(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut, coverage: dict[str, Any]) -> str:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    director_controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    compact_scenes: list[dict[str, Any]] = []
    for scene in storyboard_out.scenes:
        compact_scenes.append(
            {
                "scene_id": scene.scene_id,
                "time_start": scene.time_start,
                "time_end": scene.time_end,
                "duration": scene.duration,
                "scene_goal": scene.scene_goal,
                "frame_description": scene.frame_description,
                "action_in_frame": scene.action_in_frame,
                "camera": scene.camera,
            }
        )
    return (
        "Timeline repair pass (not a rewrite). Return strict JSON object only with the same contract keys.\n"
        "Preserve current story direction and keep strong existing scenes.\n"
        "Repair timeline to fully cover audio. Extend or add scenes only where needed.\n"
        "Keep progression natural and cinematic.\n"
        "Final scene must reach audioDurationSec.\n"
        "If audioDurationSec is > 0: first scene must start at 0.0, no large uncovered tail, and no large internal uncovered gap.\n"
        "Prefer preserving story quality while fixing coverage boundaries.\n"
        f"Coverage diagnostics: {json.dumps(coverage, ensure_ascii=False)}\n"
        f"Runtime payload: {json.dumps({'source': source, 'context_refs': context_refs, 'director_controls': director_controls, 'metadata': metadata, 'audioDurationSec': coverage.get('audioDurationSec')}, ensure_ascii=False)}\n"
        f"Current storyboard snapshot: {json.dumps({'story_summary': storyboard_out.story_summary, 'full_scenario': storyboard_out.full_scenario, 'voice_script': storyboard_out.voice_script, 'music_prompt': storyboard_out.music_prompt, 'director_summary': storyboard_out.director_summary, 'scenes': compact_scenes}, ensure_ascii=False)}"
    )


def _send_director_request(api_key: str, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[str]]:
    return _send_director_request_with_debug(api_key, body, debug_context=None)


def _is_quota_or_rate_limited_response(response_payload: dict[str, Any] | None) -> bool:
    if not isinstance(response_payload, dict) or not response_payload.get("__http_error__"):
        return False
    status = int(response_payload.get("status") or 0)
    text_l = str(response_payload.get("text") or "").lower()
    if status == 429:
        return True
    return any(token in text_l for token in ("resource_exhausted", "quota", "rate limit", "too many requests"))


def _send_director_request_with_debug(
    api_key: str,
    body: dict[str, Any],
    *,
    debug_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str, list[str]]:
    def _is_temp_unavailable(response_payload: dict[str, Any] | None) -> bool:
        if not isinstance(response_payload, dict) or not response_payload.get("__http_error__"):
            return False
        status = int(response_payload.get("status") or 0)
        text_l = str(response_payload.get("text") or "").lower()
        return status == 503 or "unavailable" in text_l or "high demand" in text_l

    attempted_models: list[str] = []
    response: dict[str, Any] | None = None
    model_used = DEFAULT_TEXT_MODEL
    attempt_index = 0
    total_attempt_budget = (len([DEFAULT_TEXT_MODEL, FALLBACK_TEXT_MODEL])) * (len(GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC) + 1)
    base_ctx = debug_context if isinstance(debug_context, dict) else {}
    for candidate_model in [DEFAULT_TEXT_MODEL, FALLBACK_TEXT_MODEL]:
        if candidate_model in attempted_models:
            continue
        attempted_models.append(candidate_model)
        for retry_idx in range(0, len(GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC) + 1):
            attempt_index += 1
            started_at = time.perf_counter()
            response = post_generate_content(api_key, candidate_model, body, timeout=120)
            elapsed_ms = round((time.perf_counter() - started_at) * 1000.0, 1)
            response_status = int(response.get("status") or 0) if isinstance(response, dict) and response.get("__http_error__") else 200
            finish_reason = _extract_gemini_finish_reason(response) if isinstance(response, dict) and not response.get("__http_error__") else ""
            logger.info(
                "[SCENARIO DIRECTOR GEMINI ATTEMPT] route=%s requestId=%s geminiAttemptIndex=%s geminiAttemptCount=%s modelUsed=%s attemptedModels=%s isRetry=%s retryReason=%s responseHttpStatus=%s responseFinishReason=%s elapsedMs=%s",
                str(base_ctx.get("route") or "/api/clip/comfy/scenario-director/generate"),
                str(base_ctx.get("scenarioDirectorRequestId") or ""),
                attempt_index,
                total_attempt_budget,
                candidate_model,
                attempted_models,
                bool(base_ctx.get("isRetry")),
                str(base_ctx.get("retryReason") or ""),
                response_status,
                finish_reason or "unknown",
                elapsed_ms,
            )
            if not _is_temp_unavailable(response):
                break
            if retry_idx >= len(GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC):
                break
            backoff_sec = GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC[retry_idx]
            logger.warning(
                "[SCENARIO DIRECTOR] Gemini temporary unavailable model=%s retry=%s backoff=%.1fs",
                candidate_model,
                retry_idx + 1,
                backoff_sec,
            )
            time.sleep(backoff_sec)
        model_used = candidate_model
        if _is_quota_or_rate_limited_response(response):
            break
        if not isinstance(response, dict) or not response.get("__http_error__"):
            break
    return response, model_used, attempted_models


def _build_scenario_director_http_error(response: dict[str, Any], *, fallback_code: str, fallback_message: str) -> ScenarioDirectorError:
    status_code = int(response.get("status") or 502)
    error_text = str(response.get("text") or "")
    error_text_l = error_text.lower()
    is_quota_exceeded = _is_quota_or_rate_limited_response(response)
    if is_quota_exceeded:
        return ScenarioDirectorError(
            "provider_quota_exceeded",
            "Gemini quota exceeded / rate limit exceeded. Проверь billing / limits / key.",
            status_code=429,
            details={
                "provider": "gemini",
                "httpStatus": status_code or 429,
                "retryable": False,
                "reason": "quota_or_rate_limited",
                "upstreamMessage": error_text[:400],
            },
        )
    is_temp_unavailable = status_code == 503 or "unavailable" in error_text_l or "high demand" in error_text_l
    if is_temp_unavailable:
        return ScenarioDirectorError(
            "gemini_temporarily_unavailable",
            "Модель Gemini сейчас перегружена, попробуйте ещё раз через несколько секунд.",
            status_code=503,
            details={
                "httpStatus": status_code,
                "retryable": True,
                "retryAfterSec": GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC[-1],
            },
        )
    return ScenarioDirectorError(
        fallback_code,
        f"{fallback_message} with HTTP {status_code}: {error_text[:400]}",
        status_code=status_code if 400 <= status_code < 600 else 502,
        details={"httpStatus": status_code, "provider": "gemini", "retryable": False},
    )


def _parse_storyboard_payload(
    raw_text: str,
    *,
    parse_stage: str = "initial",
    finish_reason: str = "",
    direct_gemini_storyboard_mode: bool = False,
) -> dict[str, Any]:
    logger.debug(
        "[SCENARIO_DIRECTOR] raw response received chars=%s parse_stage=%s finish_reason=%s",
        len(str(raw_text or "")),
        parse_stage,
        finish_reason or "unknown",
    )
    extracted = _extract_json_object(raw_text)
    if extracted is None:
        raise ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for Scenario Director: could not extract JSON object.",
            status_code=502,
            details={
                "rawPreview": str(raw_text or "")[:1200],
                "rawLength": len(str(raw_text or "")),
                "finishReason": finish_reason or "",
                "parseStage": parse_stage,
            },
        )
    logger.debug("[SCENARIO_DIRECTOR] json extracted keys=%s", ",".join(list(extracted.keys())[:8]))
    if direct_gemini_storyboard_mode:
        candidate = extracted
        if isinstance(candidate.get("storyboard_out"), dict):
            candidate = candidate["storyboard_out"]
        elif isinstance(candidate.get("storyboardOut"), dict):
            candidate = candidate["storyboardOut"]
        elif isinstance(candidate.get("output"), dict) and isinstance(candidate["output"].get("scenes"), list):
            candidate = candidate["output"]
        if not isinstance(candidate, dict):
            raise ScenarioDirectorError(
                "gemini_invalid_contract_shape",
                "Gemini returned invalid Scenario Director shape in direct mode.",
                status_code=502,
                details={"parseStage": parse_stage, "keys": list(extracted.keys())[:12]},
            )
        if not isinstance(candidate.get("scenes"), list):
            raise ScenarioDirectorError(
                "gemini_invalid_contract_scenes",
                "Gemini returned invalid Scenario Director scenes in direct mode.",
                status_code=502,
                details={"parseStage": parse_stage},
            )
        normalized, _, _ = _normalize_scenario_director_scene_defaults(dict(candidate))
        return normalized
    repaired = _repair_scenario_director_payload(extracted, parse_stage=parse_stage)
    return repaired


def _build_audio_first_single_call_prompt(payload: dict[str, Any]) -> str:
    director_note = _resolve_effective_director_note_text(payload)
    audio_duration = _safe_float(
        payload.get("audioDurationSec") or payload.get("metadata", {}).get("audio", {}).get("durationSec"),
        0.0,
    )
    role_labels = _build_reference_role_map(payload)
    available_refs = ", ".join(
        f"{role} ({label})" if label and label != role else role
        for role, label in sorted(role_labels.items())
    )
    references_block = (
        f"Available character references: {available_refs}\n" if available_refs else "Available character references: none\n"
    )
    arc_story_function_hint = "entry | development | transition | peak | ending | outro"
    return (
        "Audio-first scenario director. Return one canonical JSON object immediately.\n"
        "No prose, no analysis, no markdown.\n"
        "Use attached audio + references as real inputs.\n"
        "PRIORITY:\n"
        "- Audio drives segmentation, timestamps, rhythm, and energy transitions.\n"
        "- Director note (if present) biases world/story framing but must not break audio timing.\n"
        "- Keep one coherent grounded world unless user explicitly asks for world switch.\n"
        "CHARACTER + LOCATION LOCK:\n"
        "- Character references are identity source of truth.\n"
        "- Keep same face/hair/body silhouette/outfit identity across scenes unless user explicitly requests change.\n"
        "- Keep venue continuity; vary zone/camera/blocking/light, not identity/world.\n"
        "SEGMENTATION + ROUTE:\n"
        "- Segment by real phrase boundaries, pauses, and energy shifts.\n"
        "- Prefer scene duration about 2.0-5.5 sec when phrase timing allows.\n"
        "- Use route per scene from enum: i2v | lip_sync_music | first_last.\n"
        "- If vocals are present, include lip_sync_music where performance readability is needed.\n"
        "SCENE QUALITY:\n"
        "- Scenes must be shootable and grounded.\n"
        "- For lip_sync_music keep face/mouth/neck/shoulders/upper torso readable and camera motion controlled.\n"
        "- Avoid rotation-first choreography and unstable camera behavior.\n"
        "IMPORTANT: use ONLY canonical role ids in planning fields (character_1, character_2, character_3, animal, group, location, style, props).\n"
        "Never put filenames or display labels into actors/participants/roles.\n"
        "REAL TIMELINE REQUIREMENTS:\n"
        f"- The audio duration is {audio_duration} seconds.\n"
        "- ALL timestamps (t0, t1) MUST be expressed in REAL seconds of the audio.\n"
        "- DO NOT normalize time to a 0..1 scale.\n"
        "- DO NOT compress the timeline.\n"
        "- The full timeline of transcript and scenes MUST span the actual audio duration.\n"
        "- The last scene MUST end close to the full duration of the audio.\n"
        "Return ONLY valid JSON. No markdown. No comments. No prose outside JSON.\n"
        "STRICT OUTPUT CONTRACT: top-level JSON MUST include transcript (array), audioStructure (object), semanticTimeline (array), scenes (array).\n"
        "Do not nest the required fields under storyboard/output.\n"
        f"Director note: {director_note if director_note else 'empty'}\n"
        f"{references_block}"
        "Canonical output JSON contract (required top-level):\n"
        "{\n"
        '  "transcript": [\n'
        '    { "t0": 0.0, "t1": 0.0, "text": "" }\n'
        "  ],\n"
        '  "audioStructure": {\n'
        '    "pauses": [],\n'
        '    "energyPeaks": [],\n'
        '    "transitions": [],\n'
        '    "pacingType": "",\n'
        '    "rhythmDescription": ""\n'
        "  },\n"
        '  "semanticTimeline": [\n'
        '    {\n'
        '      "t0": 0.0,\n'
        '      "t1": 0.0,\n'
        '      "text": "",\n'
        '      "meaning": "",\n'
        '      "visualFocus": "",\n'
        '      "emotion": "",\n'
        '      "sceneType": "",\n'
        '      "transitionHint": ""\n'
        "    }\n"
        "  ],\n"
        '  "scenes": [\n'
        "    {\n"
        '      "sceneId": "S1",\n'
        '      "t0": 0.0,\n'
        '      "t1": 0.0,\n'
        '      "duration": 0.0,\n'
        '      "summary": "",\n'
        '      "visualPrompt": "",\n'
        '      "characters": ["character_1"],\n'
        '      "environment": "",\n'
        '      "camera": "",\n'
        '      "motion": "",\n'
        '      "transitionIn": "",\n'
        '      "transitionOut": "",\n'
        '      "sceneType": "",\n'
        f'      "storyFunction": "{arc_story_function_hint}",\n'
        '      "route": "i2v | lip_sync_music | first_last",\n'
        '      "content_tags": []\n'
        "    }\n"
        "  ]\n"
        "}"
    )


def _adapt_audio_first_compact_to_legacy_contract(compact_payload: dict[str, Any], *, parse_stage: str = "audio_first_initial") -> dict[str, Any]:
    storyboard = compact_payload.get("storyboard") if isinstance(compact_payload.get("storyboard"), dict) else {}
    input_understanding = (
        compact_payload.get("input_understanding") if isinstance(compact_payload.get("input_understanding"), dict) else {}
    )
    compact_scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
    legacy_scenes: list[dict[str, Any]] = []
    transcript: list[dict[str, Any]] = []
    semantic_timeline: list[dict[str, Any]] = []
    safe_summary_default = "Premium music-video performance beat."
    safe_motion_default = "Character performance aligned to the current music phrase."
    safe_camera_default = "Steady medium shot with music-video framing."
    safe_environment_default = "Music-video performance environment."

    world_context = str((storyboard.get("audio_understanding") or {}).get("world_context") or "").strip()
    story_summary = str(storyboard.get("story_summary") or "").strip()
    director_summary = str(storyboard.get("director_summary") or "").strip()
    default_world = str(input_understanding.get("default_world_choice_if_unspecified") or "").strip()
    global_environment = world_context or story_summary or director_summary or default_world or safe_environment_default

    refs = compact_payload.get("refs")
    has_character_1 = False
    if isinstance(refs, dict):
        has_character_1 = "character_1" in refs and bool(refs.get("character_1"))
    if not has_character_1:
        has_character_1 = True

    camera_tokens = {
        "close-up": "Close-up",
        "closeup": "Close-up",
        "cu": "Close-up",
        "medium": "Medium shot",
        "medium-shot": "Medium shot",
        "ms": "Medium shot",
        "wide": "Wide shot",
        "wide-shot": "Wide shot",
        "ws": "Wide shot",
        "push-in": "Push-in",
        "push in": "Push-in",
        "dolly-in": "Dolly-in",
        "tracking": "Tracking move",
        "tracking-shot": "Tracking move",
        "orbit": "Orbit move",
        "low-angle": "Low angle",
        "low angle": "Low angle",
        "high-angle": "High angle",
        "high angle": "High angle",
        "handheld": "Handheld feel",
        "crane": "Crane move",
        "tilt": "Tilt move",
        "pan": "Pan move",
    }

    for idx, scene in enumerate(compact_scenes, start=1):
        if not isinstance(scene, dict):
            continue
        start = _safe_float(scene.get("start_time_sec"), 0.0)
        end = _safe_float(scene.get("end_time_sec"), start)
        if end < start:
            end = start
        description = str(scene.get("description") or "").strip()
        summary = description or safe_summary_default
        motion = description or safe_motion_default
        content_tags = [str(tag).strip() for tag in (scene.get("content_tags") or []) if str(tag).strip()]
        matched_camera_hints: list[str] = []
        for tag in content_tags:
            normalized_tag = tag.lower().replace("_", " ")
            for key, mapped in camera_tokens.items():
                if key in normalized_tag and mapped not in matched_camera_hints:
                    matched_camera_hints.append(mapped)
        camera = ", ".join(matched_camera_hints[:3]) if matched_camera_hints else safe_camera_default

        scene_environment = str(scene.get("environment") or "").strip() or global_environment
        characters = ["character_1"] if has_character_1 else []
        route = _parse_gemini_scene_route_strict(scene.get("route"), scene_index=idx - 1, parse_stage=parse_stage)
        scene_type = str(scene.get("scene_type") or scene.get("sceneType") or "").strip()
        primary_semantic = (
            scene.get("primary_semantic")
            if isinstance(scene.get("primary_semantic"), dict)
            else (
                scene.get("primarySemantic")
                if isinstance(scene.get("primarySemantic"), dict)
                else (scene.get("semantic") if isinstance(scene.get("semantic"), dict) else {})
            )
        )
        transition_hint = str(primary_semantic.get("transitionHint") or "").strip().lower()
        boundary_reason = "phrase"
        if any(token in transition_hint for token in ("energy", "drop", "lift", "peak", "hit")):
            boundary_reason = "energy"
        elif any(token in transition_hint for token in ("emotion", "turn", "mood", "fragile", "sad")):
            boundary_reason = "emotional_turn"
        elif any(token in transition_hint for token in ("arrangement", "instrument", "drum", "chorus", "verse")):
            boundary_reason = "arrangement_shift"
        legacy_scenes.append(
            {
                "sceneId": str(scene.get("scene_id") or f"S{idx}").strip() or f"S{idx}",
                "t0": start,
                "t1": end,
                "duration": max(0.0, end - start),
                "summary": summary,
                "visualPrompt": description or summary or safe_summary_default,
                "characters": characters,
                "environment": scene_environment or safe_environment_default,
                "camera": camera,
                "motion": motion,
                "transitionIn": "",
                "transitionOut": "",
                "sceneType": scene_type,
                "storyFunction": str(scene.get("story_function") or scene.get("storyFunction") or "").strip(),
                "route": route,
                "content_tags": content_tags,
            }
        )
        transcript.append({"t0": start, "t1": end, "text": description})
        semantic_timeline.append(
            {
                "t0": start,
                "t1": end,
                "text": description,
                "meaning": description,
                "visualFocus": "",
                "emotion": "",
                "sceneType": scene_type or "build",
                "transitionHint": "",
            }
        )
    return {
        **compact_payload,
        "transcript": transcript,
        "audioStructure": {
            "pauses": [],
            "energyPeaks": [],
            "transitions": [],
            "pacingType": "",
            "rhythmDescription": "",
        },
        "semanticTimeline": semantic_timeline,
        "scenes": legacy_scenes,
        "globalStory": {
            "overallNarrative": story_summary,
            "mainTopic": "",
            "worldDescription": "",
            "tone": "",
        },
        "debug": {
            "audioUsage": "",
            "alignment": director_summary,
            "boundaryLogic": "",
            "signals": "",
        },
    }


def _is_semantically_nonempty_compact_result(
    compact_payload: dict[str, Any],
    adapted_payload: dict[str, Any],
) -> tuple[bool, str]:
    if not isinstance(compact_payload, dict) or not isinstance(adapted_payload, dict):
        return False, "invalid_payload_type"
    storyboard = compact_payload.get("storyboard") if isinstance(compact_payload.get("storyboard"), dict) else {}
    compact_scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
    adapted_scenes = adapted_payload.get("scenes") if isinstance(adapted_payload.get("scenes"), list) else []
    if not compact_scenes:
        return False, "compact_scenes_missing"
    if not adapted_scenes:
        return False, "adapted_scenes_missing"

    safe_summary_default = "premium music-video performance beat."
    safe_motion_default = "character performance aligned to the current music phrase."
    safe_camera_default = "steady medium shot with music-video framing."
    safe_environment_default = "music-video performance environment."
    defaultish_routes = {
        "",
        "i2v",
        "lip_sync_music",
        "first_last",
        "i2v | lip_sync_music | first_last",
        "i2v|lip_sync_music|first_last",
    }

    def _compact_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    def _norm(value: Any) -> str:
        return _compact_text(value).lower()

    meaningful_scene_detected = False
    for idx, raw_scene in enumerate(compact_scenes):
        if not isinstance(raw_scene, dict):
            continue
        adapted_scene = adapted_scenes[idx] if idx < len(adapted_scenes) and isinstance(adapted_scenes[idx], dict) else {}

        src_description = _compact_text(raw_scene.get("description"))
        src_route = _compact_text(raw_scene.get("route"))
        src_story_function = _compact_text(raw_scene.get("story_function") or raw_scene.get("storyFunction"))
        src_environment = _compact_text(raw_scene.get("environment"))
        src_performance = _compact_text(raw_scene.get("performance_framing") or raw_scene.get("performanceFraming"))
        src_tags = [str(tag).strip() for tag in (raw_scene.get("content_tags") or []) if str(tag).strip()]
        src_start = raw_scene.get("start_time_sec")
        src_end = raw_scene.get("end_time_sec")
        src_has_timing = src_start is not None or src_end is not None

        src_has_real_signal = any(
            [
                bool(src_description),
                bool(src_route),
                bool(src_story_function),
                bool(src_environment),
                bool(src_performance),
                bool(src_tags),
                src_has_timing,
            ]
        )
        if not src_has_real_signal:
            continue

        summary_norm = _norm(adapted_scene.get("summary"))
        visual_norm = _norm(adapted_scene.get("visualPrompt"))
        motion_norm = _norm(adapted_scene.get("motion"))
        camera_norm = _norm(adapted_scene.get("camera"))
        environment_norm = _norm(adapted_scene.get("environment"))
        route_norm = _norm(adapted_scene.get("route"))
        story_function_norm = _norm(adapted_scene.get("storyFunction"))
        adapted_tags = [str(tag).strip() for tag in (adapted_scene.get("content_tags") or []) if str(tag).strip()]
        t0 = _safe_float(adapted_scene.get("t0"), 0.0)
        t1 = _safe_float(adapted_scene.get("t1"), t0)
        duration = _safe_float(adapted_scene.get("duration"), max(0.0, t1 - t0))
        has_real_timing = (t1 - t0) > 0.0 or duration > 0.0
        route_is_non_default = bool(route_norm) and route_norm not in defaultish_routes

        text_is_non_default = any(
            [
                bool(summary_norm) and summary_norm != safe_summary_default,
                bool(visual_norm) and visual_norm != safe_summary_default,
            ]
        )
        scene_metadata_non_default = any(
            [
                bool(motion_norm) and motion_norm != safe_motion_default,
                bool(camera_norm) and camera_norm != safe_camera_default,
                bool(environment_norm) and environment_norm != safe_environment_default,
                bool(story_function_norm),
                bool(adapted_tags),
            ]
        )

        # route is intentionally not treated as a strong semantic signal:
        # even non-default route values alone should not pass semantic gate.
        if route_is_non_default and not (text_is_non_default or scene_metadata_non_default):
            continue

        if (text_is_non_default or scene_metadata_non_default) and has_real_timing:
            meaningful_scene_detected = True
            break

    if not meaningful_scene_detected:
        return False, "no_meaningful_scene_content_after_defaults"
    return True, "ok"


def _adapt_audio_first_rich_payload_to_legacy_contract(rich_payload: dict[str, Any], *, parse_stage: str = "audio_first_initial") -> dict[str, Any] | None:
    if not isinstance(rich_payload, dict):
        return None
    input_understanding = (
        rich_payload.get("input_understanding")
        if isinstance(rich_payload.get("input_understanding"), dict)
        else (
            rich_payload.get("inputUnderstanding")
            if isinstance(rich_payload.get("inputUnderstanding"), dict)
            else {}
        )
    )
    storyboard_candidates: list[dict[str, Any]] = []
    for key in ("storyboard", "storyBoard", "director_output", "directorOutput", "output", "plan", "scenario_plan"):
        candidate = rich_payload.get(key)
        if isinstance(candidate, dict):
            storyboard_candidates.append(candidate)
    storyboard_candidates.append(rich_payload)

    storyboard = next(
        (
            candidate
            for candidate in storyboard_candidates
            if isinstance(candidate.get("scenes"), list) and len(candidate.get("scenes") or []) > 0
        ),
        {},
    )
    compact_scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
    if not compact_scenes and isinstance(rich_payload.get("scenes"), list):
        compact_scenes = rich_payload.get("scenes") or []
    normalized_compact_scenes: list[dict[str, Any]] = []
    for idx, raw_scene in enumerate(compact_scenes, start=1):
        if not isinstance(raw_scene, dict):
            continue
        scene_id = raw_scene.get("scene_id") or raw_scene.get("sceneId") or raw_scene.get("id") or idx
        start = _safe_float(
            raw_scene.get("start_time_sec")
            if raw_scene.get("start_time_sec") is not None
            else raw_scene.get("startSec")
            if raw_scene.get("startSec") is not None
            else raw_scene.get("t0")
            if raw_scene.get("t0") is not None
            else raw_scene.get("start")
            if raw_scene.get("start") is not None
            else raw_scene.get("time_start"),
            0.0,
        )
        end = _safe_float(
            raw_scene.get("end_time_sec")
            if raw_scene.get("end_time_sec") is not None
            else raw_scene.get("endSec")
            if raw_scene.get("endSec") is not None
            else raw_scene.get("t1")
            if raw_scene.get("t1") is not None
            else raw_scene.get("end")
            if raw_scene.get("end") is not None
            else raw_scene.get("time_end"),
            start,
        )
        if end < start:
            end = start
        route_hint = str(
            raw_scene.get("route")
            or raw_scene.get("planned_video_generation_route")
            or raw_scene.get("plannedVideoGenerationRoute")
            or raw_scene.get("video_generation_route")
            or raw_scene.get("videoGenerationRoute")
            or raw_scene.get("ltx_mode")
            or raw_scene.get("ltxMode")
            or ""
        ).strip().lower()
        if route_hint in {"f_l", "first_last_frame", "first-last"}:
            route_hint = "first_last"
        elif route_hint in {"lip_sync", "lip-sync", "lipsync"}:
            route_hint = "lip_sync_music"
        elif route_hint in {"image_video", "image-to-video"}:
            route_hint = "i2v"
        description = str(
            raw_scene.get("description")
            or raw_scene.get("summary")
            or raw_scene.get("scene_goal")
            or raw_scene.get("frame_description")
            or raw_scene.get("visualPrompt")
            or raw_scene.get("what_from_audio_this_scene_uses")
            or ""
        ).strip()
        content_tags_raw = (
            raw_scene.get("content_tags")
            if isinstance(raw_scene.get("content_tags"), list)
            else raw_scene.get("contentTags")
            if isinstance(raw_scene.get("contentTags"), list)
            else raw_scene.get("tags")
            if isinstance(raw_scene.get("tags"), list)
            else []
        )
        content_tags = [str(tag).strip() for tag in content_tags_raw if str(tag).strip()]
        normalized_compact_scenes.append(
            {
                "scene_id": scene_id,
                "start_time_sec": start,
                "end_time_sec": end,
                "route": route_hint or "i2v",
                "performance_framing": str(
                    raw_scene.get("performance_framing")
                    or raw_scene.get("performanceFraming")
                    or raw_scene.get("camera")
                    or ""
                ).strip(),
                "story_function": str(raw_scene.get("story_function") or raw_scene.get("storyFunction") or "").strip(),
                "description": description,
                "content_tags": content_tags,
                "environment": str(raw_scene.get("environment") or raw_scene.get("location") or "").strip(),
            }
        )

    if not normalized_compact_scenes:
        return None
    compact_payload = {
        "input_understanding": input_understanding,
        "storyboard": {
            "story_summary": str(storyboard.get("story_summary") or storyboard.get("storySummary") or "").strip(),
            "full_scenario": str(storyboard.get("full_scenario") or storyboard.get("fullScenario") or "").strip(),
            "voice_script": str(storyboard.get("voice_script") or storyboard.get("voiceScript") or "").strip(),
            "director_summary": str(storyboard.get("director_summary") or storyboard.get("directorSummary") or "").strip(),
            "audio_understanding": storyboard.get("audio_understanding") if isinstance(storyboard.get("audio_understanding"), dict) else {},
            "narrative_strategy": storyboard.get("narrative_strategy") if isinstance(storyboard.get("narrative_strategy"), dict) else {},
            "diagnostics": storyboard.get("diagnostics") if isinstance(storyboard.get("diagnostics"), dict) else {},
            "scenes": normalized_compact_scenes,
        },
    }
    return _adapt_audio_first_compact_to_legacy_contract(compact_payload, parse_stage=f"{parse_stage}:rich_to_legacy_adapter")


def _build_inline_audio_part(audio_context: dict[str, Any]) -> dict[str, Any]:
    audio_url = str(audio_context.get("audioUrl") or "").strip()
    resolution = _resolve_audio_source_for_analysis(audio_url)
    if not resolution.get("ok"):
        raise ScenarioDirectorError(
            "audio_source_unavailable",
            "Audio source is required for audio-first single-call mode.",
            status_code=400,
            details={"audioUrl": audio_url or None, "reason": resolution.get("reason"), "hint": resolution.get("hint")},
        )

    mime_type = (
        str(audio_context.get("audioMimeType") or "").strip()
        or str(audio_context.get("mimeType") or "").strip()
        or mimetypes.guess_type(str(audio_url or ""))[0]
        or "audio/mpeg"
    )
    raw_audio = b""
    if resolution.get("mode") == "local_file" and resolution.get("path"):
        with open(str(resolution.get("path")), "rb") as fp:
            raw_audio = fp.read()
    else:
        fetch_url = str(resolution.get("url") or "").strip()
        if not fetch_url:
            raise ScenarioDirectorError(
                "audio_source_unavailable",
                "Resolved audio source has no readable path or URL.",
                status_code=400,
                details={"audioUrl": audio_url or None, "reason": resolution.get("reason")},
            )
        response = requests.get(fetch_url, timeout=60)
        response.raise_for_status()
        raw_audio = response.content
        mime_type = response.headers.get("content-type", mime_type).split(";")[0].strip() or mime_type
    if not raw_audio:
        raise ScenarioDirectorError(
            "audio_source_unavailable",
            "Audio file is empty for audio-first single-call mode.",
            status_code=400,
            details={"audioUrl": audio_url or None},
        )
    raw_audio_bytes = len(raw_audio)
    logger.info(
        "[SCENARIO DIRECTOR] inline audio raw size bytes=%s max=%s",
        raw_audio_bytes,
        MAX_INLINE_AUDIO_BYTES,
    )
    if raw_audio_bytes > MAX_INLINE_AUDIO_BYTES:
        raise ScenarioDirectorError(
            "audio_too_large_for_inline",
            "Audio file is too large for inline Gemini payload.",
            status_code=413,
            details={
                "audioUrl": audio_url or None,
                "rawAudioBytes": raw_audio_bytes,
                "maxInlineAudioBytes": MAX_INLINE_AUDIO_BYTES,
            },
        )
    return {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(raw_audio).decode("utf-8")}}


def _parse_audio_first_single_call_payload(raw_text: str, *, parse_stage: str = "initial", finish_reason: str = "") -> dict[str, Any]:
    extracted = _extract_json_object(raw_text)
    if extracted is None:
        raise ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for audio-first single-call mode.",
            status_code=502,
            details={
                "rawPreview": str(raw_text or "")[:1200],
                "rawLength": len(str(raw_text or "")),
                "finishReason": finish_reason or "",
                "parseStage": parse_stage,
            },
        )
    top_level_keys = list(extracted.keys()) if isinstance(extracted, dict) else []
    logger.info("[SCENARIO DIRECTOR] audio-first parse_stage=%s top_level_keys=%s", parse_stage, top_level_keys)
    required = ("transcript", "audioStructure", "semanticTimeline", "scenes")

    def _is_valid_legacy_contract(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if any(key not in payload for key in required):
            return False
        if not isinstance(payload.get("transcript"), list):
            return False
        if not isinstance(payload.get("audioStructure"), dict):
            return False
        if not isinstance(payload.get("semanticTimeline"), list):
            return False
        scenes_value = payload.get("scenes")
        return isinstance(scenes_value, list) and len(scenes_value) > 0

    parse_branch = ""
    parse_reason = ""
    if _is_valid_legacy_contract(extracted):
        parse_branch = "legacy_parse"
    else:
        compact_adapted = _adapt_audio_first_compact_to_legacy_contract(extracted, parse_stage=parse_stage)
        compact_is_valid = _is_valid_legacy_contract(compact_adapted)
        compact_is_semantic, compact_reason = _is_semantically_nonempty_compact_result(extracted, compact_adapted)
        if compact_is_valid and compact_is_semantic:
            extracted = compact_adapted
            parse_branch = "compact_to_legacy_adapter"
        else:
            parse_branch = "compact_rejected_semantic_empty" if compact_is_valid else "compact_invalid_contract"
            parse_reason = compact_reason if compact_is_valid else "compact_contract_invalid"
            rich_adapted = _adapt_audio_first_rich_payload_to_legacy_contract(extracted, parse_stage=parse_stage)
            if isinstance(rich_adapted, dict) and _is_valid_legacy_contract(rich_adapted):
                extracted = rich_adapted
                parse_branch = "rich_to_legacy_adapter"
                parse_reason = ""
            else:
                parse_branch = "fatal_invalid"
                if not parse_reason:
                    parse_reason = "rich_adapter_failed"
    parse_branch = parse_branch or "unknown"
    parse_reason = parse_reason or ("legacy_contract_valid" if parse_branch == "legacy_parse" else "none")
    raw_length = len(str(raw_text or ""))
    finish_reason_normalized = (finish_reason or "").strip()
    likely_truncated = finish_reason_normalized in {"MAX_TOKENS", "LENGTH"} or raw_length >= 3500

    missing = [key for key in required if key not in extracted]
    if missing:
        logger.warning(
            "[SCENARIO DIRECTOR] audio-first parser branch=%s reason=%s parse_stage=%s top_level_keys=%s missing=%s",
            parse_branch,
            parse_reason,
            parse_stage,
            top_level_keys,
            missing,
        )
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload missed required fields.",
            status_code=502,
            details={
                "missingFields": missing,
                "rawPreview": str(raw_text or "")[:800],
                "rawLength": raw_length,
                "finishReason": finish_reason_normalized,
                "topLevelKeys": top_level_keys,
                "parseBranch": parse_branch,
                "parseReason": parse_reason,
                "likelyTruncated": likely_truncated,
            },
        )
    if not isinstance(extracted.get("transcript"), list):
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload has invalid transcript type.",
            status_code=502,
            details={
                "field": "transcript",
                "expectedType": "list",
                "actualType": type(extracted.get("transcript")).__name__,
                "rawLength": raw_length,
                "finishReason": finish_reason_normalized,
                "topLevelKeys": top_level_keys,
                "parseBranch": parse_branch,
                "parseReason": parse_reason,
                "likelyTruncated": likely_truncated,
            },
        )
    if not isinstance(extracted.get("audioStructure"), dict):
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload has invalid audioStructure type.",
            status_code=502,
            details={
                "field": "audioStructure",
                "expectedType": "dict",
                "actualType": type(extracted.get("audioStructure")).__name__,
                "rawLength": raw_length,
                "finishReason": finish_reason_normalized,
                "topLevelKeys": top_level_keys,
                "parseBranch": parse_branch,
                "parseReason": parse_reason,
                "likelyTruncated": likely_truncated,
            },
        )
    if not isinstance(extracted.get("semanticTimeline"), list):
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload has invalid semanticTimeline type.",
            status_code=502,
            details={
                "field": "semanticTimeline",
                "expectedType": "list",
                "actualType": type(extracted.get("semanticTimeline")).__name__,
                "rawLength": raw_length,
                "finishReason": finish_reason_normalized,
                "topLevelKeys": top_level_keys,
                "parseBranch": parse_branch,
                "parseReason": parse_reason,
                "likelyTruncated": likely_truncated,
            },
        )
    scenes = extracted.get("scenes")
    if not isinstance(scenes, list):
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload has invalid scenes type.",
            status_code=502,
            details={
                "field": "scenes",
                "expectedType": "list",
                "actualType": type(scenes).__name__,
                "rawLength": raw_length,
                "finishReason": finish_reason_normalized,
                "topLevelKeys": top_level_keys,
                "parseBranch": parse_branch,
                "parseReason": parse_reason,
                "likelyTruncated": likely_truncated,
            },
        )
    if not scenes:
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload returned empty scenes.",
            status_code=502,
            details={
                "field": "scenes",
                "reason": "empty_list",
                "rawLength": raw_length,
                "finishReason": finish_reason_normalized,
                "topLevelKeys": top_level_keys,
                "parseBranch": parse_branch,
                "parseReason": parse_reason,
                "likelyTruncated": likely_truncated,
            },
        )
    logger.info(
        "[SCENARIO DIRECTOR] audio-first parser branch=%s reason=%s parse_stage=%s top_level_keys=%s scenes=%s",
        parse_branch,
        parse_reason,
        parse_stage,
        top_level_keys,
        len(scenes),
    )
    return extracted


def _scale_audio_first_timeline_if_normalized(result: dict[str, Any], audio_duration_sec: float) -> dict[str, Any]:
    if audio_duration_sec <= 0:
        return result
    max_t1 = 0.0
    for section_key in ("transcript", "semanticTimeline", "scenes"):
        section = result.get(section_key)
        if not isinstance(section, list):
            continue
        for row in section:
            if not isinstance(row, dict):
                continue
            max_t1 = max(max_t1, _safe_float(row.get("t1"), 0.0))
    if max_t1 <= 0:
        return result
    if max_t1 >= audio_duration_sec * 0.3 and abs(max_t1 - 1.0) > 0.05:
        return result
    scale = audio_duration_sec / max_t1
    for section_key in ("transcript", "semanticTimeline", "scenes"):
        section = result.get(section_key)
        if not isinstance(section, list):
            continue
        for row in section:
            if not isinstance(row, dict):
                continue
            t0 = _safe_float(row.get("t0"), 0.0)
            t1 = _safe_float(row.get("t1"), t0)
            row["t0"] = t0 * scale
            row["t1"] = t1 * scale
            if section_key == "scenes":
                row["duration"] = _safe_float(row.get("duration"), max(0.0, t1 - t0)) * scale
    print("[SCENARIO DIRECTOR] timeline normalized → scaled to real duration")
    return result


SCENARIO_CHUNK_PREFERRED_MIN_SEC = 3.0
SCENARIO_CHUNK_HARD_MIN_SEC = 2.4
SCENARIO_CHUNK_MICRO_SEC = 2.0
SCENARIO_SHORT_FIRST_LAST_MIN_SEC = 2.8
DIRECT_GEMINI_MIN_RENDERABLE_SCENE_SEC = 1.6
DIRECT_GEMINI_ESTABLISHING_SCENE_MAX_SEC = 1.2


def _normalize_scene_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _scene_has_hard_shift(a: dict[str, Any], b: dict[str, Any]) -> bool:
    shift_hints = ("hard cut", "time jump", "jump cut", "smash cut", "new location", "state shift", "scene break")
    blob_a = " ".join([
        _normalize_scene_text(a.get("summary")),
        _normalize_scene_text(a.get("motion")),
        _normalize_scene_text(a.get("camera")),
        _normalize_scene_text(a.get("transitionHint")),
    ])
    blob_b = " ".join([
        _normalize_scene_text(b.get("summary")),
        _normalize_scene_text(b.get("motion")),
        _normalize_scene_text(b.get("camera")),
        _normalize_scene_text(b.get("transitionHint")),
    ])
    return any(hint in blob_a or hint in blob_b for hint in shift_hints)


def _can_merge_short_scene_pair(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, str]:
    left_env = _normalize_scene_text(left.get("environment"))
    right_env = _normalize_scene_text(right.get("environment"))
    left_chars = {str(x).strip().lower() for x in (left.get("characters") or []) if str(x).strip()}
    right_chars = {str(x).strip().lower() for x in (right.get("characters") or []) if str(x).strip()}
    has_world_overlap = bool(left_chars & right_chars) or (left_env and right_env and left_env == right_env)
    if not has_world_overlap:
        return False, "world_or_cast_mismatch"
    if _scene_has_hard_shift(left, right):
        return False, "hard_state_shift_detected"
    return True, "merge_allowed_shared_continuity"


def _merge_generation_short_scenes(raw_scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenes = [dict(scene) for scene in raw_scenes if isinstance(scene, dict)]
    if len(scenes) < 2:
        return scenes
    idx = 0
    while idx < len(scenes):
        scene = scenes[idx]
        t0 = _safe_float(scene.get("t0"), 0.0)
        t1 = _safe_float(scene.get("t1"), t0)
        dur = max(0.0, t1 - t0)
        if dur >= SCENARIO_CHUNK_HARD_MIN_SEC:
            idx += 1
            continue
        print("[SCENARIO MIN DURATION] " + json.dumps({
            "sceneId": str(scene.get("sceneId") or f"S{idx+1}"),
            "durationSec": round(dur, 3),
            "preferredMinimumSec": SCENARIO_CHUNK_PREFERRED_MIN_SEC,
            "hardMinimumSec": SCENARIO_CHUNK_HARD_MIN_SEC,
            "microScene": dur < SCENARIO_CHUNK_MICRO_SEC,
        }, ensure_ascii=False))
        merged = False
        for neighbor_idx in [idx + 1, idx - 1]:
            if neighbor_idx < 0 or neighbor_idx >= len(scenes):
                continue
            neighbor = scenes[neighbor_idx]
            can_merge, reason = _can_merge_short_scene_pair(scene, neighbor)
            if not can_merge:
                print("[SCENARIO CHUNK MERGE] " + json.dumps({
                    "action": "kept_separate",
                    "sceneId": str(scene.get("sceneId") or f"S{idx+1}"),
                    "neighborSceneId": str(neighbor.get("sceneId") or f"S{neighbor_idx+1}"),
                    "reason": reason,
                }, ensure_ascii=False))
                continue
            left_idx, right_idx = sorted([idx, neighbor_idx])
            left = scenes[left_idx]
            right = scenes[right_idx]
            left["t0"] = _safe_float(left.get("t0"), 0.0)
            left["t1"] = _safe_float(right.get("t1"), _safe_float(left.get("t1"), _safe_float(left.get("t0"), 0.0)))
            left["duration"] = round(max(0.0, _safe_float(left.get("t1"), 0.0) - _safe_float(left.get("t0"), 0.0)), 3)
            left["summary"] = " / ".join([part for part in [str(left.get("summary") or "").strip(), str(right.get("summary") or "").strip()] if part])
            left["motion"] = " / ".join([part for part in [str(left.get("motion") or "").strip(), str(right.get("motion") or "").strip()] if part])
            left_chars = [str(x).strip() for x in (left.get("characters") or []) if str(x).strip()]
            right_chars = [str(x).strip() for x in (right.get("characters") or []) if str(x).strip()]
            left["characters"] = list(dict.fromkeys(left_chars + right_chars))
            scenes.pop(right_idx)
            print("[SCENARIO CHUNK MERGE] " + json.dumps({
                "action": "merged",
                "sceneId": str(left.get("sceneId") or f"S{left_idx+1}"),
                "mergedWithSceneId": str(right.get("sceneId") or f"S{right_idx+1}"),
                "reason": reason,
                "resultDurationSec": left.get("duration"),
            }, ensure_ascii=False))
            merged = True
            idx = max(0, left_idx - 1)
            break
        if not merged:
            print("[SCENARIO CHUNK MERGE] " + json.dumps({
                "action": "kept_short_scene",
                "sceneId": str(scene.get("sceneId") or f"S{idx+1}"),
                "durationSec": round(dur, 3),
                "reason": "no_safe_neighbor_merge",
            }, ensure_ascii=False))
            idx += 1
    return scenes


def _scene_is_environment_establishing(raw_scene: dict[str, Any]) -> bool:
    text_bundle = " ".join([
        str(raw_scene.get("summary") or ""),
        str(raw_scene.get("motion") or ""),
        str(raw_scene.get("camera") or ""),
        str(raw_scene.get("environment") or ""),
        str(raw_scene.get("sceneType") or raw_scene.get("scene_type") or ""),
    ]).lower()
    establishing_tokens = (
        "establish",
        "wide",
        "venue reveal",
        "stage reveal",
        "crowd scale",
        "atmosphere",
        "laser",
        "lights",
        "concert",
        "festival",
        "audience",
        "opening",
        "intro",
    )
    return any(token in text_bundle for token in establishing_tokens)


def _preprocess_direct_gemini_short_scenes(raw_scenes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scenes = [dict(scene) for scene in raw_scenes if isinstance(scene, dict)]
    if not scenes:
        return [], {"direct_short_scene_min_sec": DIRECT_GEMINI_MIN_RENDERABLE_SCENE_SEC}
    prepared: list[dict[str, Any]] = []
    dropped_scene_ids: list[str] = []
    merged_scene_ids: list[str] = []
    hidden_story_beats: list[dict[str, Any]] = []
    for idx, scene in enumerate(scenes):
        start = _safe_float(scene.get("t0"), 0.0)
        end = _safe_float(scene.get("t1"), start)
        duration = max(0.0, end - start)
        scene_id = str(scene.get("sceneId") or f"S{idx + 1}").strip() or f"S{idx + 1}"
        scene_story_function = str(scene.get("storyFunction") or scene.get("story_function") or "").strip().lower()
        absorbed_function = scene_story_function or ("entry" if _scene_is_environment_establishing(scene) else "transition")
        is_short = duration < DIRECT_GEMINI_MIN_RENDERABLE_SCENE_SEC
        is_short_establishing = duration <= DIRECT_GEMINI_ESTABLISHING_SCENE_MAX_SEC and _scene_is_environment_establishing(scene)
        if not is_short:
            prepared.append(scene)
            continue
        if is_short_establishing:
            dropped_scene_ids.append(scene_id)
            if idx + 1 < len(scenes):
                next_scene = scenes[idx + 1]
                absorbed = list(next_scene.get("absorbedStoryFunctions") or [])
                absorbed.append(absorbed_function)
                next_scene["absorbedStoryFunctions"] = list(dict.fromkeys([str(item).strip() for item in absorbed if str(item).strip()]))
                hidden_story_beats.append(
                    {
                        "sceneId": scene_id,
                        "storyFunction": absorbed_function,
                        "resolution": "merged_into_next_renderable_scene_without_visual_prompt_blend",
                    }
                )
                scenes[idx + 1]["t0"] = round(min(_safe_float(scenes[idx + 1].get("t0"), start), start), 3)
            continue
        if idx + 1 < len(scenes):
            scenes[idx + 1]["t0"] = round(min(_safe_float(scenes[idx + 1].get("t0"), start), start), 3)
            prev_summary = str(scene.get("summary") or "").strip()
            next_summary = str(scenes[idx + 1].get("summary") or "").strip()
            if prev_summary and prev_summary.lower() not in next_summary.lower():
                scenes[idx + 1]["summary"] = f"{prev_summary}. {next_summary}".strip(". ")
            merged_scene_ids.append(scene_id)
            absorbed = list(scenes[idx + 1].get("absorbedStoryFunctions") or [])
            absorbed.append(absorbed_function)
            scenes[idx + 1]["absorbedStoryFunctions"] = list(dict.fromkeys([str(item).strip() for item in absorbed if str(item).strip()]))
            hidden_story_beats.append(
                {
                    "sceneId": scene_id,
                    "storyFunction": absorbed_function,
                    "resolution": "merged_into_next_renderable_scene",
                }
            )
            continue
        if prepared:
            prepared[-1]["t1"] = round(max(_safe_float(prepared[-1].get("t1"), 0.0), end), 3)
            merged_scene_ids.append(scene_id)
            absorbed = list(prepared[-1].get("absorbedStoryFunctions") or [])
            absorbed.append(absorbed_function)
            prepared[-1]["absorbedStoryFunctions"] = list(dict.fromkeys([str(item).strip() for item in absorbed if str(item).strip()]))
            hidden_story_beats.append(
                {
                    "sceneId": scene_id,
                    "storyFunction": absorbed_function,
                    "resolution": "merged_into_previous_renderable_scene",
                }
            )
            continue
        prepared.append(scene)
    return prepared, {
        "direct_short_scene_min_sec": DIRECT_GEMINI_MIN_RENDERABLE_SCENE_SEC,
        "droppedEnvironmentEstablishingSceneIds": dropped_scene_ids,
        "mergedShortSceneIds": merged_scene_ids,
        "hiddenStoryBeats": hidden_story_beats,
    }


def _apply_story_arc_canon_to_legacy_scenes(legacy_scenes: list[dict[str, Any]]) -> None:
    total = len(legacy_scenes)
    if total <= 0:
        return
    for idx, scene in enumerate(legacy_scenes):
        explicit_story_function = str(scene.get("story_function") or scene.get("storyFunction") or "").strip().lower()
        absorbed_story_functions = [
            str(item).strip().lower()
            for item in (scene.get("absorbed_story_functions") or scene.get("absorbedStoryFunctions") or [])
            if str(item).strip()
        ]
        explicit_stage = ""
        if explicit_story_function in {"entry", "opening"}:
            explicit_stage = "opening"
        elif explicit_story_function in {"ending", "outro"}:
            explicit_stage = "ending"
        elif explicit_story_function in {"development", "transition", "peak"}:
            explicit_stage = "development"
        if total == 1:
            stage = "opening_to_ending"
            purpose = "ending_hold"
            hook = "Single-scene mini-arc: establish world/hero and finish with deliberate closure."
            progression = "entry_and_resolution_compacted_for_short_audio"
        elif idx == 0:
            stage = "opening"
            purpose = "hook"
            hook = "Opening beat establishes world, hero presence, and emotional starting point."
            progression = "entry_anchor_for_story_arc"
        elif idx == total - 1:
            stage = "ending"
            purpose = "ending_hold"
            hook = "Final beat lands emotional resolution/afterglow instead of abrupt cut."
            progression = "outro_resolution_for_story_arc"
        else:
            stage = "development"
            purpose = "build"
            hook = "Development beat advances action, energy, or emotional turn."
            progression = "middle_progression_event"
        if explicit_stage and stage != "opening_to_ending":
            stage = explicit_stage
        absorbed_hint = ", ".join(absorbed_story_functions)
        if absorbed_hint:
            progression = f"{progression}; absorbed_hidden_beats={absorbed_hint}"
            if "hidden beat" not in hook.lower():
                hook = f"{hook} Carries hidden beat(s): {absorbed_hint}."
        scene["clip_arc_stage"] = stage
        scene["scene_purpose"] = purpose
        scene["viewer_hook"] = str(scene.get("viewer_hook") or "").strip() or hook
        scene["progression_reason"] = progression
        scene["display_index"] = idx + 1
        scene["story_function"] = explicit_story_function or stage






def _is_short_music_intro_segment(*, text: Any = "", t0: Any = 0.0, t1: Any = 0.0, scene_type: Any = "", actors: Any = None) -> bool:
    phrase = str(text or "").strip().lower()
    if not phrase:
        return False
    normalized_phrase = re.sub(r"[^a-zа-я0-9]+", " ", phrase).strip()
    if normalized_phrase not in {"music intro", "instrumental intro"}:
        return False
    start_sec = _safe_float(t0, 0.0)
    end_sec = _safe_float(t1, start_sec)
    duration_sec = max(0.0, end_sec - start_sec)
    if start_sec > 0.05 or duration_sec > 1.0:
        return False
    scene_type_value = str(scene_type or "").strip().lower()
    if scene_type_value and scene_type_value not in {"intro", "instrumental", "music_intro", "music-intro"}:
        return False
    actor_list = [str(actor).strip() for actor in (actors or []) if str(actor).strip()] if isinstance(actors, (list, tuple, set)) else []
    if actor_list:
        return False
    return True


def _filter_short_music_intro_scenes(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    if not scenes:
        return storyboard_out
    first = scenes[0]
    if _is_short_music_intro_segment(
        text=first.local_phrase,
        t0=first.time_start,
        t1=first.time_end,
        scene_type=first.scene_purpose,
        actors=first.actors,
    ):
        storyboard_out.scenes = scenes[1:]
        logger.debug("[SCENARIO_DIRECTOR] filtered short intro scene scene_id=%s t0=%.3f t1=%.3f", first.scene_id, first.time_start, first.time_end)
    return storyboard_out


def _map_single_call_to_storyboard_out(result: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime_payload = payload if isinstance(payload, dict) else {}
    effective_director_note_text = _resolve_effective_director_note_text(runtime_payload)
    has_effective_director_note = bool(effective_director_note_text)
    global_story = result.get("globalStory") if isinstance(result.get("globalStory"), dict) else {}
    debug = result.get("debug") if isinstance(result.get("debug"), dict) else {}
    transcript_rows = result.get("transcript") if isinstance(result.get("transcript"), list) else []
    semantic_timeline = result.get("semanticTimeline") if isinstance(result.get("semanticTimeline"), list) else []
    raw_scenes = result.get("scenes") if isinstance(result.get("scenes"), list) else []
    def _resolve_single_call_duration_sec() -> tuple[float, str]:
        payload_duration = _safe_float(runtime_payload.get("audioDurationSec"), 0.0)
        if payload_duration > 0:
            return payload_duration, "payload.audioDurationSec"

        parsed_candidates = [
            ("result.audioDurationSec", result.get("audioDurationSec")),
            ("result.duration", result.get("duration")),
            (
                "result.metadata.audio.durationSec",
                (result.get("metadata") or {}).get("audio", {}).get("durationSec")
                if isinstance(result.get("metadata"), dict)
                and isinstance((result.get("metadata") or {}).get("audio"), dict)
                else None,
            ),
        ]
        for source_name, candidate in parsed_candidates:
            parsed_duration = _safe_float(candidate, 0.0)
            if parsed_duration > 0:
                return parsed_duration, source_name

        def _max_t1(rows: list[Any]) -> float:
            max_end = 0.0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_end = _safe_float(
                    row.get("t1"),
                    _safe_float(
                        row.get("end"),
                        _safe_float(row.get("time_end"), _safe_float(row.get("timeEnd"), 0.0)),
                    ),
                )
                if row_end > max_end:
                    max_end = row_end
            return max_end

        scene_end = _max_t1(raw_scenes)
        if scene_end > 0:
            return scene_end, "fallback.scenes_max_t1"
        semantic_end = _max_t1(semantic_timeline)
        if semantic_end > 0:
            return semantic_end, "fallback.semanticTimeline_end"
        transcript_end = _max_t1(transcript_rows)
        if transcript_end > 0:
            return transcript_end, "fallback.transcript_end"
        return 0.0, "fallback.default_zero"

    resolved_duration_sec, resolved_duration_source = _resolve_single_call_duration_sec()
    direct_gemini_storyboard_mode = _is_direct_gemini_storyboard_mode(runtime_payload)
    scene_merge_applied = False
    direct_short_scene_policy_debug: dict[str, Any] = {}
    if direct_gemini_storyboard_mode:
        raw_scenes, direct_short_scene_policy_debug = _preprocess_direct_gemini_short_scenes(raw_scenes)
    else:
        raw_scenes = _merge_generation_short_scenes(raw_scenes)
        scene_merge_applied = True
    transcript_text_parts = [
        str(item.get("text") or "").strip()
        for item in transcript_rows
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    voice_script = " ".join(transcript_text_parts).strip()
    def _overlap_sec(a0: float, a1: float, b0: float, b1: float) -> float:
        return max(0.0, min(a1, b1) - max(a0, b0))

    def _match_scene_phrases_by_time(scene_start: float, scene_end: float) -> dict[str, Any]:
        def _collect(rows: list[Any]) -> list[dict[str, Any]]:
            matches: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                t0 = _safe_float(row.get("t0"), 0.0)
                t1 = _safe_float(row.get("t1"), t0)
                overlap = _overlap_sec(scene_start, scene_end, t0, t1)
                if overlap <= 0:
                    continue
                matches.append({
                    "text": text,
                    "t0": t0,
                    "t1": t1,
                    "overlap": overlap,
                    "emotion": str(row.get("emotion") or "").strip(),
                    "meaning": str(row.get("meaning") or "").strip(),
                    "transitionHint": str(row.get("transitionHint") or "").strip(),
                })
            return sorted(matches, key=lambda item: float(item.get("overlap") or 0.0), reverse=True)

        transcript_matches = _collect(transcript_rows)
        semantic_matches = _collect(semantic_timeline)
        selected = transcript_matches if transcript_matches else semantic_matches
        unique_texts = list(dict.fromkeys([str(item.get("text") or "").strip() for item in selected if str(item.get("text") or "").strip()]))
        primary = unique_texts[0] if unique_texts else ""
        return {
            "matchedTranscriptPhrases": transcript_matches,
            "matchedSemanticPhrases": semantic_matches,
            "matchedPhraseTexts": unique_texts,
            "phraseCount": len(unique_texts),
            "primaryPhrase": primary,
            "combinedPhraseText": " · ".join(unique_texts),
            "sourceUsed": "transcript" if transcript_matches else ("semanticTimeline" if semantic_matches else "none"),
            "primarySemantic": semantic_matches[0] if semantic_matches else {},
        }

    def _direct_mode_trim_scene_end(scene_start: float, scene_end: float) -> tuple[float, bool, str]:
        if not direct_gemini_storyboard_mode:
            return scene_end, False, "direct_mode_disabled"
        if scene_end <= scene_start:
            return scene_end, False, "invalid_scene_window"
        candidates: list[float] = []
        for row in [*transcript_rows, *semantic_timeline]:
            if not isinstance(row, dict):
                continue
            t1 = _safe_float(row.get("t1"), _safe_float(row.get("end"), -1.0))
            if t1 <= scene_start or t1 >= scene_end:
                continue
            candidates.append(t1)
        if not candidates:
            return scene_end, False, "no_phrase_boundary_candidate"
        nearest_back = max(candidates)
        trim_delta = scene_end - nearest_back
        min_duration_after_trim = 0.8
        max_safe_trim = 0.65
        min_trim_delta = 0.08
        if trim_delta < min_trim_delta:
            return scene_end, False, "trim_delta_too_small"
        if trim_delta > max_safe_trim:
            return scene_end, False, "trim_delta_too_large"
        if (nearest_back - scene_start) < min_duration_after_trim:
            return scene_end, False, "trim_makes_scene_too_short"
        return round(nearest_back, 3), True, "trimmed_to_nearest_phrase_end"

    print(
        "[SCENARIO PHRASE MAP] transcript=%s semanticTimeline=%s scenes=%s",
        len(transcript_rows),
        len(semantic_timeline),
        len(raw_scenes),
    )
    legacy_scenes: list[dict[str, Any]] = []
    for idx, scene in enumerate(raw_scenes, start=1):
        if not isinstance(scene, dict):
            continue
        scene_start = _safe_float(scene.get("t0"), 0.0)
        raw_scene_end = _safe_float(scene.get("t1"), scene_start)
        scene_end, trimmed, trim_reason = _direct_mode_trim_scene_end(scene_start, raw_scene_end)
        scene_duration = _safe_float(scene.get("duration"), max(0.0, scene_end - scene_start))
        scene_phrase_match = _match_scene_phrases_by_time(scene_start, scene_end)
        primary_semantic = scene_phrase_match.get("primarySemantic") if isinstance(scene_phrase_match.get("primarySemantic"), dict) else {}
        print(
            "[SCENARIO SCENE PHRASES] sceneId=%s t0=%.3f t1=%.3f matched=%s source=%s primary=%s texts=%s",
            str(scene.get("sceneId") or f"S{idx}").strip() or f"S{idx}",
            scene_start,
            scene_end,
            scene_phrase_match.get("phraseCount"),
            scene_phrase_match.get("sourceUsed"),
            scene_phrase_match.get("primaryPhrase"),
            scene_phrase_match.get("matchedPhraseTexts"),
        )
        primary_phrase = str(scene_phrase_match.get("primaryPhrase") or "").strip()
        raw_boundary_reason = str(scene.get("boundary_reason") or scene.get("boundaryReason") or "").strip().lower()
        if raw_boundary_reason in {"phrase", "pause", "semantic", "energy", "fallback"}:
            boundary_reason = raw_boundary_reason
        elif raw_boundary_reason in {"phrase_boundary", "pause_boundary", "energy_boundary"}:
            boundary_reason = raw_boundary_reason.replace("_boundary", "")
        else:
            boundary_reason = "phrase" if primary_phrase else "fallback"
        if (not direct_gemini_storyboard_mode) and _is_short_music_intro_segment(
            text=primary_phrase,
            t0=scene_start,
            t1=scene_end,
            scene_type=scene.get("sceneType") or scene.get("scene_type"),
            actors=scene.get("characters") or [],
        ):
            continue
        is_first_renderable_scene = len(legacy_scenes) == 0
        route = _parse_gemini_scene_route_strict(scene.get("route"), scene_index=idx - 1, parse_stage="audio_first_mapping")
        mapped_workflow_key = GEMINI_ROUTE_TO_WORKFLOW_KEY[route]
        is_lip_sync_route = route == "lip_sync_music"
        is_first_last_route = route == "first_last"
        performance_framing = str(scene.get("performance_framing") or scene.get("performanceFraming") or "").strip()
        summary_text = str(scene.get("summary") or "").strip()
        motion_text = str(scene.get("motion") or "").strip()
        camera_text = str(scene.get("camera") or "").strip()
        route_description = " ".join(part for part in (summary_text, motion_text, camera_text) if part).strip()
        normalized_route_description, performance_framing = _normalize_scene_canon_by_route(
            route=route,
            description=route_description,
            performance_framing=performance_framing,
            content_tags=[str(scene.get("sceneType") or "").strip(), str(scene.get("storyFunction") or "").strip()],
        )
        if normalized_route_description and not summary_text:
            summary_text = normalized_route_description
        motion_text_lc = motion_text.lower()
        camera_text_lc = camera_text.lower()
        motion_is_risky = any(marker in motion_text_lc for marker in (*LIP_SYNC_SPIN_RISK_MARKERS, *NON_LIP_RISKY_ROTATION_MARKERS))
        camera_is_risky = any(marker in camera_text_lc for marker in (*LIP_SYNC_SPIN_RISK_MARKERS, *NON_LIP_RISKY_ROTATION_MARKERS))
        if is_lip_sync_route:
            if not motion_text or motion_is_risky:
                motion_text = (
                    "Singer-performance-first: emotional lyric delivery with readable mouth/neck/shoulders/upper torso, expressive hands, subtle sway, "
                    "small step or gentle torso shift; beat drives emotional intensity and performance energy."
                )
            if not camera_text or camera_is_risky:
                camera_text = (
                    "Camera supports song immersion with gentle push/pull, drift, or side arc while preserving close facial readability."
                )
        elif route in {"i2v", "first_last"}:
            if not motion_text or motion_is_risky:
                motion_text = (
                    "Action-space progression through venue zones with safe step/pivot/gesture and evolving shoulder/head/body angles; "
                    "avoid sharp full-body spins as primary motion while keeping scene-specific narrative development."
                )
            if not camera_text or camera_is_risky:
                camera_text = "Tracking or angled reveal/parallax move that keeps readable progression through space."
        is_environment_establishing = _scene_is_environment_establishing(scene)
        if scene_duration <= DIRECT_GEMINI_ESTABLISHING_SCENE_MAX_SEC and is_environment_establishing and not is_lip_sync_route:
            scene.setdefault("characters", [])
        internal_route = "f_l" if is_first_last_route else route
        lip_sync_route_state_consistent = is_lip_sync_route
        image_prompt_value = _normalize_image_prompt_by_route(
            route=route,
            image_prompt=str(scene.get("visualPrompt") or "").strip(),
            fallback_text=" ".join(part for part in (summary_text, motion_text, camera_text) if part).strip(),
        )
        legacy_scenes.append(
            {
                "scene_id": str(scene.get("sceneId") or f"S{idx}").strip() or f"S{idx}",
                "display_index": len(legacy_scenes) + 1,
                "time_start": scene_start,
                "time_end": scene_end,
                "duration": scene_duration,
                "actors": [str(actor).strip() for actor in (scene.get("characters") or []) if str(actor).strip()],
                "location": str(scene.get("environment") or "").strip(),
                "props": [],
                "emotion": str(primary_semantic.get("emotion") or "").strip(),
                "scene_goal": summary_text,
                "frame_description": summary_text,
                "action_in_frame": motion_text,
                "camera": camera_text,
                "image_prompt": image_prompt_value,
                "video_prompt": motion_text or "Beat-synced camera and subject motion evolving through the scene.",
                "ltx_mode": "lip_sync_music" if is_lip_sync_route else ("f_l" if is_first_last_route else "i2v"),
                "ltx_reason": "Audio-first single-call route mapped via strict enum contract.",
                "render_mode": "lip_sync_music" if is_lip_sync_route else ("first_last" if is_first_last_route else "image_video"),
                "resolved_workflow_key": mapped_workflow_key,
                "resolved_workflow_file": CLIP_CANONICAL_WORKFLOW_FILE_BY_KEY["lip_sync_music" if is_lip_sync_route else ("f_l" if is_first_last_route else "i2v")],
                "start_frame_source": "new",
                "needs_two_frames": is_first_last_route,
                "continuation_from_previous": False,
                "narration_mode": "full",
                "local_phrase": primary_phrase or None,
                "audio_slice_kind": "music_vocal" if is_lip_sync_route else ("voice_only" if primary_phrase else "none"),
                "music_vocal_lipsync_allowed": is_lip_sync_route,
                "lip_sync": is_lip_sync_route,
                "lipSync": is_lip_sync_route,
                "isLipSync": is_lip_sync_route,
                "send_audio_to_generator": is_lip_sync_route,
                "sendAudioToGenerator": is_lip_sync_route,
                "audio_slice_start_sec": scene_start,
                "audio_slice_end_sec": scene_end,
                "audio_slice_expected_duration_sec": scene_duration,
                "matched_phrase_texts": scene_phrase_match.get("matchedPhraseTexts") or [],
                "scene_phrase_count": int(scene_phrase_match.get("phraseCount") or 0),
                "scene_phrase_source": str(scene_phrase_match.get("sourceUsed") or "none"),
                "sfx": "",
                "music_mix_hint": "off",
                "scene_purpose": "hook" if is_first_renderable_scene else "build",
                "viewer_hook": "Immediate rhythmic visual anchor." if is_first_renderable_scene else "Beat-matched progression.",
                "performance_framing": performance_framing,
                "story_function": str(scene.get("storyFunction") or scene.get("story_function") or "").strip() or ("opening" if is_first_renderable_scene else "development"),
                "absorbed_story_functions": [str(item).strip() for item in (scene.get("absorbedStoryFunctions") or []) if str(item).strip()],
                "what_from_audio_this_scene_uses": str(primary_semantic.get("meaning") or scene.get("summary") or "").strip(),
                "director_note_layer": "",
                "boundary_reason": boundary_reason,
                "audio_anchor_evidence": str(primary_semantic.get("transitionHint") or "").strip(),
                "performer_presentation": "female" if is_lip_sync_route else "unknown",
                "vocal_presentation": "female" if is_lip_sync_route else "unknown",
                "performance_phase": str(scene.get("clipArcStage") or scene.get("clip_arc_stage") or ("build" if is_lip_sync_route else "")).strip(),
                "lip_sync_voice_compatibility": "compatible" if is_lip_sync_route else "unknown",
                "lip_sync_decision_reason": "audio_first_route_lip_sync_music_selected" if is_lip_sync_route else "not_lip_sync_route",
                "audioEmotionDirection": (
                    "energetic_hook"
                    if is_lip_sync_route and str(primary_semantic.get("transitionHint") or "").strip()
                    else ("restrained_ache" if is_lip_sync_route else "")
                ),
                "confidence": 0.9,
                "sourceRoute": route,
                "video_generation_route": internal_route,
                "planned_video_generation_route": route,
                "phrase_boundary_trim_applied": trimmed,
                "phrase_boundary_trim_reason": trim_reason,
                "segment_boundary_decision_reason": f"{boundary_reason}; evidence={str(primary_semantic.get('transitionHint') or primary_phrase or 'timeline_overlap')}",
                "segment_trim_decision_reason": "trim_applied_to_phrase_boundary" if trimmed else f"trim_not_applied:{trim_reason}",
                "original_scene_end": raw_scene_end,
                "trimmed_scene_end": scene_end,
                "lip_sync_route_state_consistent": lip_sync_route_state_consistent,
                "audio_slice_bounds_filled_from_scene": is_lip_sync_route,
                "environment_only_establishing": bool(scene_duration <= DIRECT_GEMINI_ESTABLISHING_SCENE_MAX_SEC and is_environment_establishing),
            }
        )
    _apply_story_arc_canon_to_legacy_scenes(legacy_scenes)
    trim_applied_count = len([scene for scene in legacy_scenes if isinstance(scene, dict) and bool(scene.get("phrase_boundary_trim_applied"))])
    phrase_texts = [str(scene.get("local_phrase") or "").strip().lower() for scene in legacy_scenes if isinstance(scene, dict)]
    phrase_loop_detected = len(phrase_texts) != len(list(dict.fromkeys([text for text in phrase_texts if text])))
    phrase_loop_prevention_action = "story_arc_canon_dedupe" if trim_applied_count > 0 else ""
    phrase_loop_prevented = bool(phrase_loop_detected and phrase_loop_prevention_action)
    durations = [float(scene.get("duration") or 0.0) for scene in legacy_scenes if isinstance(scene, dict)]
    duration_span = (max(durations) - min(durations)) if durations else 0.0
    clip_formula_rebalance_applied = False
    clip_formula_rebalance_detected_need = bool(len(durations) >= 4 and duration_span > 2.0)
    oversized_threshold = 5.5 if 20.0 <= resolved_duration_sec <= 40.0 else 6.0
    oversized_scene_ids = [
        str(scene.get("scene_id") or "").strip()
        for scene in legacy_scenes
        if isinstance(scene, dict) and _safe_float(scene.get("duration"), 0.0) > oversized_threshold
    ]
    director_summary = (
        str(debug.get("alignment") or "").strip()
        or str(global_story.get("overallNarrative") or "").strip()
        or "Audio-first single-call Gemini output."
    )
    global_narrative = str(global_story.get("overallNarrative") or "").strip()
    return {
        "story_summary": global_narrative,
        "full_scenario": global_narrative,
        "voice_script": voice_script,
        "music_prompt": "",
        "director_summary": director_summary,
        "audio_understanding": {
            "main_topic": str(global_story.get("mainTopic") or "").strip(),
            "world_context": str(global_story.get("worldDescription") or "").strip(),
            "implied_events": [],
            "emotional_tone_from_audio": str(global_story.get("tone") or "").strip(),
            "confidence_audio_understood": 0.9,
            "what_from_audio_defines_world": str(global_story.get("worldDescription") or "").strip(),
        },
        "conflict_analysis": {
            "audio_vs_director_note_conflict": False,
            "conflict_description": "",
            "resolution_strategy": "",
        },
        "narrative_strategy": {
            "story_core_source": "director_note" if has_effective_director_note else "audio",
            "story_frame_source": "director_note" if has_effective_director_note else "source_of_truth",
            "rhythm_source": "audio",
            "story_frame_source_reason": "single_call_effective_director_note_integrated" if has_effective_director_note else "single_call_audio_first_no_director_note",
            "rhythm_source_reason": "single_call_audio_timeline_drives_pacing",
            "did_audio_remain_primary": True,
            "did_director_note_override_audio": has_effective_director_note,
            "why": (
                "Audio-first timing with effective director note driving story frame/world bias."
                if has_effective_director_note
                else "Audio-first single-call output."
            ),
        },
        "story": {
            "title": str(global_story.get("mainTopic") or "").strip(),
            "summary": str(global_story.get("overallNarrative") or "").strip(),
            "how_director_note_was_integrated": "effective_director_note_story_frame_bias_plus_audio_rhythm_driver" if has_effective_director_note else "",
            "how_romance_exists_inside_audio_world": "",
        },
        "diagnostics": {
            "used_audio_as_content_source": True,
            "used_audio_only_as_mood": False,
            "did_fallback_from_audio_content_truth": False,
            "biggest_risk": str(debug.get("boundaryLogic") or "").strip(),
            "what_may_be_wrong": str(debug.get("signals") or "").strip(),
            "planner_mode": "full_audio_first",
            "how_director_note_was_integrated": "effective_director_note_story_frame_bias_plus_audio_rhythm_driver" if has_effective_director_note else "",
            "direct_gemini_storyboard_mode": direct_gemini_storyboard_mode,
            "intro_logic_applied": False if direct_gemini_storyboard_mode else True,
            "scene_merge_applied": scene_merge_applied,
            "direct_short_scene_policy": direct_short_scene_policy_debug,
            "story_arc_canon_applied": True,
            "story_arc_stages_present": list(
                dict.fromkeys(
                    [
                        str(scene.get("clip_arc_stage") or "").strip()
                        for scene in legacy_scenes
                        if isinstance(scene, dict) and str(scene.get("clip_arc_stage") or "").strip()
                    ]
                )
            ),
            "backend_route_override_applied": False,
            "transcript_segment_count": len([row for row in transcript_rows if isinstance(row, dict)]),
            "final_scene_count": len(legacy_scenes),
            "scene_count_matches_transcript_beats": len(legacy_scenes) == len([row for row in transcript_rows if isinstance(row, dict)]),
            "phrase_boundary_trim_applied": bool(trim_applied_count > 0),
            "phrase_boundary_trim_applied_count": int(trim_applied_count),
            "phrase_loop_prevented": bool(phrase_loop_prevented),
            "phrase_loop_detected": bool(phrase_loop_detected),
            "phrase_loop_prevention_action": phrase_loop_prevention_action,
            "phrase_loop_prevention_reason": (
                "phrase_duplicates_detected_and_story_arc_dedupe_applied"
                if phrase_loop_prevented
                else "no_active_prevention_action"
            ),
            "clip_formula_rebalance_applied": bool(clip_formula_rebalance_applied),
            "clip_formula_rebalance_detected_need": bool(clip_formula_rebalance_detected_need),
            "resolvedAudioDurationSec": round(_safe_float(resolved_duration_sec, 0.0), 3),
            "resolvedAudioDurationSource": resolved_duration_source,
            "duration_span_debug": round(duration_span, 3),
            "rebalance_reason": "duration_span_heuristic_only_no_rebalance_action",
            "rebalance_actions": [],
            "sentenceBoundaryCandidates": [],
            "clauseBoundaryCandidates": [],
            "finalSceneOversizeDetected": bool(
                legacy_scenes
                and _safe_float((legacy_scenes[-1] or {}).get("duration"), 0.0) > oversized_threshold
            ),
            "finalSceneSplitConsidered": False,
            "finalSceneSplitApplied": False,
            "finalSceneSplitReason": "not_evaluated_in_initial_single_call_parse",
            "segmentationRepairSource": "",
            "oversizedScenesDetected": oversized_scene_ids,
            "oversizedScenesSplitCount": 0,
        },
        "scenes": legacy_scenes,
    }


def _run_audio_first_single_call(payload: dict[str, Any], audio_context: dict[str, Any], api_key: str) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    scenario_director_request_id = str(metadata.get("scenarioDirectorRequestId") or "").strip() or f"sd-{uuid4().hex[:12]}"
    route_path = "/api/clip/comfy/scenario-director/generate"
    logger.info("[SCENARIO DIRECTOR] audio-first single-call mode requestId=%s route=%s", scenario_director_request_id, route_path)
    prompt = _build_audio_first_single_call_prompt(payload)
    logger.info("[SCENARIO DIRECTOR] sending inline audio to Gemini")
    inline_audio_part = _build_inline_audio_part(audio_context)
    reference_image_parts = _build_reference_image_parts(payload)
    request_parts = [{"text": prompt}, *reference_image_parts, inline_audio_part]
    prompt_length_chars = len(str(prompt or ""))
    reference_image_part_count = len(reference_image_parts)
    request_part_count = len(request_parts)
    has_inline_audio = bool(inline_audio_part)
    max_output_tokens = 6144
    logger.info(
        "[SCENARIO DIRECTOR PAYLOAD ESTIMATE] route=%s requestId=%s promptLengthChars=%s requestPartCount=%s referenceImagePartCount=%s hasInlineAudio=%s maxOutputTokens=%s",
        route_path,
        scenario_director_request_id,
        prompt_length_chars,
        request_part_count,
        reference_image_part_count,
        has_inline_audio,
        max_output_tokens,
    )
    body = {
        "systemInstruction": {
            "parts": [{"text": "Return strict JSON only."}],
        },
        "contents": [{"role": "user", "parts": request_parts}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "maxOutputTokens": max_output_tokens,
        },
    }
    response, model_used, attempted_models = _send_director_request_with_debug(
        api_key,
        body,
        debug_context={
            "route": route_path,
            "scenarioDirectorRequestId": scenario_director_request_id,
            "isRetry": False,
            "retryReason": "",
        },
    )
    if not isinstance(response, dict):
        raise ScenarioDirectorError("gemini_request_failed", "Gemini did not return a JSON object.", status_code=502)
    if response.get("__http_error__"):
        raise _build_scenario_director_http_error(
            response,
            fallback_code="gemini_request_failed",
            fallback_message="Gemini request failed",
        )
    raw_text = _extract_gemini_text(response)
    finish_reason = _extract_gemini_finish_reason(response)
    logger.info(
        "[SCENARIO DIRECTOR] audio-first raw response chars=%s finish_reason=%s",
        len(str(raw_text or "")),
        finish_reason or "unknown",
    )
    try:
        parsed_single = _parse_audio_first_single_call_payload(raw_text, parse_stage="audio_first_initial", finish_reason=finish_reason)
    except ScenarioDirectorError:
        retry_body = {
            **body,
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{prompt}\n{AUDIO_FIRST_JSON_RETRY_SUFFIX}"}, *reference_image_parts, inline_audio_part],
                }
            ],
        }
        retry_response, retry_model_used, retry_attempts = _send_director_request_with_debug(
            api_key,
            retry_body,
            debug_context={
                "route": route_path,
                "scenarioDirectorRequestId": scenario_director_request_id,
                "isRetry": True,
                "retryReason": "parse_or_contract_retry",
            },
        )
        attempted_models.extend(model for model in retry_attempts if model not in attempted_models)
        if not isinstance(retry_response, dict):
            raise
        if retry_response.get("__http_error__"):
            raise _build_scenario_director_http_error(
                retry_response,
                fallback_code="gemini_request_failed",
                fallback_message="Gemini audio-first strict JSON retry failed",
            )
        raw_text = _extract_gemini_text(retry_response)
        finish_reason = _extract_gemini_finish_reason(retry_response)
        model_used = retry_model_used
        logger.info(
            "[SCENARIO DIRECTOR] audio-first retry raw response chars=%s finish_reason=%s model=%s",
            len(str(raw_text or "")),
            finish_reason or "unknown",
            model_used,
        )
        parsed_single = _parse_audio_first_single_call_payload(raw_text, parse_stage="audio_first_retry", finish_reason=finish_reason)
    audio_duration_sec = _safe_float(
        payload.get("audioDurationSec") or payload.get("metadata", {}).get("audio", {}).get("durationSec"),
        0.0,
    )
    parsed_single = _scale_audio_first_timeline_if_normalized(parsed_single, audio_duration_sec)
    logger.info("[SCENARIO DIRECTOR] received single-call json keys=%s", list(parsed_single.keys()))
    legacy_payload = _map_single_call_to_storyboard_out(parsed_single, payload=payload)
    logger.info("[SCENARIO DIRECTOR] mapped single-call result to legacy storyboardOut")
    storyboard_out = ScenarioDirectorStoryboardOut.model_validate(legacy_payload)
    storyboard_out = _maybe_split_final_hybrid_outro_scene(storyboard_out)
    hardening_payload = {**payload, "_single_call_payload": parsed_single}
    storyboard_out = _harden_storyboard_out(storyboard_out, hardening_payload)
    generation_scenes = [
        {
            "sceneId": str(scene.scene_id or "").strip(),
            "t0": round(_safe_float(scene.time_start, 0.0), 3),
            "t1": round(_safe_float(scene.time_end, _safe_float(scene.time_start, 0.0)), 3),
            "duration": round(max(0.0, _safe_float(scene.time_end, 0.0) - _safe_float(scene.time_start, 0.0)), 3),
            "summary": str(scene.scene_goal or scene.frame_description or "").strip(),
            "motion": str(scene.action_in_frame or "").strip(),
            "camera": str(scene.camera or "").strip(),
            "performanceFraming": str(scene.performance_framing or "").strip(),
            "environment": str(scene.location or "").strip(),
            "visualPrompt": str(scene.image_prompt or "").strip(),
            "characters": [str(actor).strip() for actor in (scene.actors or []) if str(actor).strip()],
        }
        for scene in (storyboard_out.scenes or [])
    ]
    logger.info(
        "[SCENARIO CHUNKING] response_sync top_level_scenes=%s storyboard_scenes=%s",
        len(generation_scenes),
        len(storyboard_out.scenes or []),
    )
    director_output = _build_director_output(storyboard_out, payload)
    brain_package = _build_brain_package(storyboard_out, payload)
    content_type_policy = _get_content_type_policy(payload)
    effective_global_music_prompt = _resolve_effective_global_music_prompt(payload, storyboard_out.music_prompt)
    return {
        "ok": True,
        "transcript": parsed_single.get("transcript") or [],
        "audioStructure": parsed_single.get("audioStructure") if isinstance(parsed_single.get("audioStructure"), dict) else {},
        "semanticTimeline": parsed_single.get("semanticTimeline") or [],
        "scenes": generation_scenes,
        "globalStory": parsed_single.get("globalStory") if isinstance(parsed_single.get("globalStory"), dict) else {},
        "debug": parsed_single.get("debug") if isinstance(parsed_single.get("debug"), dict) else {},
        "storyboardOut": storyboard_out.model_dump(mode="json"),
        "directorOutput": director_output,
        "canonicalSceneContract": director_output.get("scenes") if isinstance(director_output, dict) else [],
        "finalSceneContract": director_output.get("scenes") if isinstance(director_output, dict) else [],
        "scenario": storyboard_out.full_scenario,
        "voiceScript": storyboard_out.voice_script,
        "bgMusicPrompt": effective_global_music_prompt,
        "brainPackage": brain_package,
        "meta": {
            "plannerSource": "gemini",
            "modelUsed": model_used,
            "attemptedModels": attempted_models,
            "audioFirstSingleCall": True,
            "rawGeminiTextPreview": raw_text[:2000],
            "requestedContentType": content_type_policy.get("requestedValue"),
            "effectiveContentType": content_type_policy.get("value"),
            "requestedContentTypeEnabled": content_type_policy.get("requestedEnabled"),
            "contentTypeFallbackApplied": content_type_policy.get("fallbackApplied"),
            "contentTypePolicy": content_type_policy,
        },
    }


def run_scenario_director(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ScenarioDirectorError(
            "gemini_api_key_missing",
            "GEMINI_API_KEY is missing for Scenario Director generation.",
            status_code=503,
        )

    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    source_audio_meta = source_meta.get("audio") if isinstance(source_meta.get("audio"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata_audio = metadata.get("audio") if isinstance(metadata.get("audio"), dict) else {}
    audio_context = _normalize_audio_context(payload)
    if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" and _coerce_bool(audio_context.get("hasAudio"), False):
        return _run_audio_first_single_call(payload, audio_context, api_key)
    source_origin_raw = str(
        payload.get("source_origin")
        or source.get("source_origin")
        or source.get("origin")
        or metadata_audio.get("origin")
        or payload.get("sourceOrigin")
        or ""
    ).strip()
    source_origin_normalized = str(audio_context.get("sourceOrigin") or source_origin_raw or "connected").strip().lower()
    audio_url_raw = str(
        source.get("source_value")
        or source.get("value")
        or payload.get("source_value")
        or metadata_audio.get("url")
        or source_audio_meta.get("url")
        or ""
    ).strip()
    audio_url_normalized = str(audio_context.get("audioUrl") or audio_url_raw).strip()
    audio_analysis = _build_audio_analysis_fallback(_safe_float(audio_context.get("audioDurationSec"), 0.0), "analysis_skipped")
    audio_hints: list[str] = []
    audio_errors: list[str] = []
    audio_analysis_attempted = False
    if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" and audio_context.get("hasAudio"):
        audio_analysis_attempted = True
        audio_analysis = _analyze_audio_for_scenario_director(audio_context)
        if audio_analysis.get("ok"):
            audio_hints.append("audio_analysis_ok")
        else:
            audio_hints.append(str(audio_analysis.get("hint") or "audio_analysis_failed"))
            audio_errors.extend(audio_analysis.get("errors") or [])
    elif str(audio_context.get("sourceMode") or "").upper() == "AUDIO":
        audio_hints.append("audio_mode_without_audio_url")
    audio_semantics = _analyze_audio_semantics_for_scenario_director(payload, audio_context)

    can_use_phrase_first = bool(
        str(audio_context.get("sourceMode") or "").upper() == "AUDIO"
        and _coerce_bool(audio_context.get("preferAudioOverText"), True)
        and audio_analysis.get("ok")
        and (
            len(audio_analysis.get("phrases") or []) > 0
            or len(audio_analysis.get("pauseWindows") or []) > 0
            or len(audio_analysis.get("sections") or []) > 0
        )
    )
    audio_guidance = _build_phrase_first_segmentation_guidance(audio_analysis, audio_context) if can_use_phrase_first else {}
    request_text = _build_request_text(
        payload,
        audio_context=audio_context,
        audio_analysis=audio_analysis,
        audio_guidance=audio_guidance,
        audio_semantics=audio_semantics,
    )
    reference_image_parts = _build_reference_image_parts(payload)
    request_parts = [{"text": request_text}, *reference_image_parts]
    body = {
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "You are the production Scenario Director for PhotoStudio COMFY. Return strict JSON only. "
                        "When sourceMode=AUDIO and sourceOrigin=connected, preserve explicit audio facts/entities/events as truth. "
                        "If effective director note exists (including payload.text), treat it as active world/story-frame direction in music_video mode. "
                        "Audio remains rhythm/segmentation/timing driver and should not force world choice when director note requests another performance world. "
                        "If audioDurationSec > 0, timeline must cover full audio from 0.0 to audioDurationSec. "
                        "Every scene must include narration_mode as non-null string (full|duck|pause) and audio usage evidence fields."
                    ),
                }
            ]
        },
        "contents": [{"role": "user", "parts": request_parts}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "maxOutputTokens": 4096,
        },
    }

    response, model_used, attempted_models = _send_director_request(api_key, body)

    if not isinstance(response, dict):
        raise ScenarioDirectorError("gemini_request_failed", "Gemini did not return a JSON object.", status_code=502)
    if response.get("__http_error__"):
        raise _build_scenario_director_http_error(
            response,
            fallback_code="gemini_request_failed",
            fallback_message="Gemini request failed",
        )

    raw_text = _extract_gemini_text(response)
    finish_reason = _extract_gemini_finish_reason(response)
    logger.info(
        "[SCENARIO DIRECTOR] raw response chars=%s finish_reason=%s model=%s",
        len(str(raw_text or "")),
        finish_reason or "unknown",
        model_used,
    )
    retried_for_json = False
    try:
        parsed_payload = _parse_storyboard_payload(
            raw_text,
            parse_stage="initial",
            finish_reason=finish_reason,
            direct_gemini_storyboard_mode=direct_gemini_storyboard_mode,
        )
    except ScenarioDirectorError as first_exc:
        retried_for_json = True
        retry_body = {
            **body,
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": _build_request_text(
                                payload,
                                audio_context=audio_context,
                                audio_analysis=audio_analysis,
                                audio_guidance=audio_guidance,
                                audio_semantics=audio_semantics,
                                strict_json_retry=True,
                            )
                        },
                        *reference_image_parts,
                    ],
                }
            ],
        }
        retry_response, retry_model_used, retry_attempts = _send_director_request(api_key, retry_body)
        attempted_models.extend(model for model in retry_attempts if model not in attempted_models)
        if not isinstance(retry_response, dict):
            raise first_exc
        if retry_response.get("__http_error__"):
            raise _build_scenario_director_http_error(
                retry_response,
                fallback_code="gemini_request_failed",
                fallback_message="Gemini strict JSON retry failed",
            )
        raw_text = _extract_gemini_text(retry_response)
        retry_finish_reason = _extract_gemini_finish_reason(retry_response)
        logger.info(
            "[SCENARIO DIRECTOR] retry raw response chars=%s finish_reason=%s model=%s",
            len(str(raw_text or "")),
            retry_finish_reason or "unknown",
            retry_model_used,
        )
        model_used = retry_model_used
        parsed_payload = _parse_storyboard_payload(
            raw_text,
            parse_stage="strict_json_retry",
            finish_reason=retry_finish_reason,
            direct_gemini_storyboard_mode=direct_gemini_storyboard_mode,
        )

    parsed_payload, normalized_contract_fields, normalization_warnings = _normalize_scenario_director_scene_defaults(parsed_payload)
    structured_planner_diagnostics = _extract_structured_diagnostics(parsed_payload)

    try:
        storyboard_out = ScenarioDirectorStoryboardOut.model_validate(parsed_payload)
        logger.debug("[SCENARIO_DIRECTOR] validation ok scenes=%s retry=%s", len(storyboard_out.scenes), retried_for_json)
    except ValidationError as exc:
        logger.debug("[SCENARIO_DIRECTOR] validation failed errors=%s", len(exc.errors()))
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini Scenario Director response does not match the required contract.",
            status_code=502,
            details={"validationErrors": exc.errors(), "rawPreview": raw_text[:1000]},
        ) from exc

    storyboard_out = _harden_storyboard_out(storyboard_out, payload)
    explicit_role_warnings: list[str] = []
    audio_duration_from_analysis = _safe_float(audio_analysis.get("audioDurationSec"), 0.0)
    audio_duration_from_payload = _safe_float(audio_context.get("audioDurationSec"), 0.0)
    audio_duration_sec = audio_duration_from_analysis or audio_duration_from_payload
    audio_duration_source = "analysis" if audio_duration_from_analysis > 0 else ("payload" if audio_duration_from_payload > 0 else "missing")
    audio_connected = _is_audio_connected(payload)
    audio_connected_reason = (
        "audio_connected_via_legacy_origin" if audio_connected and source_origin_raw.lower() in {"audio_node", "audio_upload", "audio_generated"} else "audio_connected"
    ) if audio_connected else (
        "source_origin_not_connected"
        if str(audio_context.get("hasAudio"))
        else "audio_url_missing"
    )
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    prefer_audio_over_text = _coerce_bool(controls.get("preferAudioOverText"), _coerce_bool(audio_context.get("preferAudioOverText"), True))
    text_hint_present = bool(_resolve_effective_director_note_text(payload))
    content_type_policy = _get_content_type_policy(payload)
    is_music_video_mode = str(content_type_policy.get("value") or "").strip().lower() == "music_video"
    story_core_source = str(storyboard_out.narrative_strategy.story_core_source or "mixed").strip().lower() or "mixed"
    story_frame_source = str(storyboard_out.narrative_strategy.story_frame_source or "").strip().lower()
    rhythm_source = str(storyboard_out.narrative_strategy.rhythm_source or "").strip().lower()
    story_frame_source_reason = str(storyboard_out.narrative_strategy.story_frame_source_reason or "").strip()
    rhythm_source_reason = str(storyboard_out.narrative_strategy.rhythm_source_reason or "").strip()
    story_core_source_reason = str(storyboard_out.narrative_strategy.why or "").strip() or "preserved_gemini_narrative_strategy"
    cast_identity_lock_info = {"enabled": False, "lockReason": "preserved_gemini_output", "textRewritesApplied": 0}
    effective_role_type_by_role, role_assignment_source, role_override_applied = _resolve_effective_role_type_by_role(payload)
    coverage = _validate_audio_timeline_coverage(storyboard_out.scenes, audio_duration_sec, coverage_source=audio_duration_source if audio_duration_source in {"analysis", "payload"} else "fallback")
    coverage_warnings: list[str] = list(coverage.get("warnings") or [])
    audio_led_warnings: list[str] = []
    if audio_connected and audio_duration_sec <= 0:
        audio_led_warnings.append("audio_connected_but_duration_missing")
    narrative_bias_estimate, text_hint_influence, audio_influence, bias_warnings = _estimate_narrative_bias(
        payload,
        storyboard_out,
        audio_connected=audio_connected,
        prefer_audio_over_text=prefer_audio_over_text,
    )
    audio_led_warnings.extend(bias_warnings)
    timeline_refinement_attempted = False
    timeline_refinement_succeeded = False
    if audio_duration_sec > 0 and coverage.get("timelineCoverageStatus") == "invalid":
        raise ScenarioDirectorError(
            "contract_invalid_for_timeline",
            "Scenario Director timeline does not fully cover audioDurationSec after refinement.",
            status_code=502,
            details=coverage,
        )

    phrase_signals_available = bool(
        len(audio_analysis.get("phrases") or []) > 0
        or len(audio_analysis.get("pauseWindows") or []) > 0
        or len(audio_analysis.get("sections") or []) > 0
        or len(audio_analysis.get("energyTransitions") or []) > 0
    )
    is_audio_mode = str(audio_context.get("sourceMode") or "").upper() == "AUDIO"
    full_audio_first = bool(is_audio_mode and audio_connected and audio_analysis.get("ok") and phrase_signals_available)
    partial_audio_first = bool(
        is_audio_mode
        and not full_audio_first
        and (audio_connected or audio_duration_sec > 0 or _coerce_bool(audio_context.get("preferAudioOverText"), True))
    )
    fallback_mode = "full_audio_first" if full_audio_first else ("partial_audio_first" if partial_audio_first else "text_fallback")
    if full_audio_first:
        fallback_reason = "audio_connected_with_usable_analysis_signals"
        audio_primary_driver_reason = "full_audio_phrase_pause_section_guidance"
    elif partial_audio_first:
        fallback_reason = "audio_mode_with_duration_or_partial_signals"
        audio_primary_driver_reason = "partial_audio_guidance_duration_aware"
    else:
        fallback_reason = "audio_unusable_or_non_audio_source"
        audio_primary_driver_reason = "text_fallback_only"
    no_text_fallback_mode = "off"
    authorial_interpretation_level = "low" if no_text_fallback_mode == "neutral_audio_literal" else "medium"
    audio_literalness_level = "high" if no_text_fallback_mode == "neutral_audio_literal" else "balanced"
    storyboard_out.diagnostics.no_text_fallback_mode = no_text_fallback_mode
    storyboard_out.diagnostics.no_text_clip_policy = "off"
    storyboard_out.diagnostics.no_text_clip_policy_applied = False
    storyboard_out.diagnostics.authorial_interpretation_level = authorial_interpretation_level
    storyboard_out.diagnostics.audio_literalness_level = audio_literalness_level
    planner_narrative_strategy = structured_planner_diagnostics.get("narrativeStrategy") if isinstance(structured_planner_diagnostics.get("narrativeStrategy"), dict) else {}
    planner_diagnostics = structured_planner_diagnostics.get("diagnostics") if isinstance(structured_planner_diagnostics.get("diagnostics"), dict) else {}
    planner_audio_understanding = structured_planner_diagnostics.get("audioUnderstanding") if isinstance(structured_planner_diagnostics.get("audioUnderstanding"), dict) else {}
    audio_semantics_ok = _coerce_bool(audio_semantics.get("ok"), False)
    audio_transcript_available = bool(str(audio_semantics.get("transcript") or "").strip())
    audio_semantic_summary = str(audio_semantics.get("semanticSummary") or "").strip()
    audio_world_context = str(audio_semantics.get("worldContext") or "").strip()
    audio_narrative_core = str(audio_semantics.get("narrativeCore") or "").strip()
    audio_entities = [str(item).strip() for item in (audio_semantics.get("entities") or []) if str(item).strip()]
    audio_implied_events = [str(item).strip() for item in (audio_semantics.get("impliedEvents") or []) if str(item).strip()]
    audio_semantic_summary_available = bool(audio_semantic_summary)
    audio_content_truth_available = bool(audio_semantic_summary or audio_world_context or audio_entities)
    planner_scene_evidence = []
    for scene in storyboard_out.scenes:
        planner_scene_evidence.append(
            {
                "sceneId": scene.scene_id,
                "whatFromAudioThisSceneUses": scene.what_from_audio_this_scene_uses,
                "directorNoteLayer": scene.director_note_layer,
                "boundaryReason": scene.boundary_reason,
                "audioAnchorEvidence": scene.audio_anchor_evidence,
                "confidence": scene.confidence,
            }
        )
    fake_audio_first_risks: list[str] = []
    audio_grounding_validation = {"sceneRiskMap": [], "globalRisks": [], "score": 1.0}
    if is_audio_mode and audio_connected:
        if not _coerce_bool(planner_narrative_strategy.get("didAudioRemainPrimary"), False):
            fake_audio_first_risks.append("didAudioRemainPrimary_false")
        if _coerce_bool(planner_narrative_strategy.get("didDirectorNoteOverrideAudio"), False):
            fake_audio_first_risks.append("didDirectorNoteOverrideAudio_true")
        if _coerce_bool(planner_diagnostics.get("usedAudioOnlyAsMood"), False):
            fake_audio_first_risks.append("usedAudioOnlyAsMood_true")
        if not _coerce_bool(planner_diagnostics.get("usedAudioAsContentSource"), False):
            fake_audio_first_risks.append("usedAudioAsContentSource_false")
        if audio_semantics_ok and audio_content_truth_available and not _coerce_bool(planner_diagnostics.get("usedAudioAsContentSource"), False):
            fake_audio_first_risks.append("audio_content_truth_ignored")
        if not str(planner_audio_understanding.get("whatFromAudioDefinesWorld") or "").strip():
            fake_audio_first_risks.append("whatFromAudioDefinesWorld_missing")
        if any(
            not str(item.get("whatFromAudioThisSceneUses") or "").strip() or not str(item.get("audioAnchorEvidence") or "").strip()
            for item in planner_scene_evidence
        ):
            fake_audio_first_risks.append("scene_level_audio_evidence_missing")
        audio_grounding_validation = _validate_audio_first_integrity(
            storyboard_out=storyboard_out,
            structured_planner_diagnostics=structured_planner_diagnostics,
            audio_analysis=audio_analysis if isinstance(audio_analysis, dict) else {},
        )
        scene_level_risks = [
            risk
            for row in (audio_grounding_validation.get("sceneRiskMap") or [])
            if isinstance(row, dict)
            for risk in (row.get("risks") or [])
            if str(risk).strip()
        ]
        fake_audio_first_risks.extend(audio_grounding_validation.get("globalRisks") or [])
        fake_audio_first_risks.extend(scene_level_risks)
        logger.debug(
            "[AUDIO_GROUNDING] score=%s scene_risks=%s global_risks=%s",
            audio_grounding_validation.get("score"),
            len(scene_level_risks),
            len(audio_grounding_validation.get("globalRisks") or []),
        )
    fake_audio_first_risks = list(dict.fromkeys(fake_audio_first_risks))
    fake_audio_first_suspected = bool(fake_audio_first_risks) or _safe_float(audio_grounding_validation.get("score"), 1.0) < 0.5

    director_output = _build_director_output(storyboard_out, payload)
    brain_package = _build_brain_package(storyboard_out, payload)
    effective_global_music_prompt = _resolve_effective_global_music_prompt(payload, storyboard_out.music_prompt)
    role_influence_applied_scenes = 0
    identity_lock_applied_scenes = 0
    for scene in (storyboard_out.scenes or []):
        reason = str(scene.clip_decision_reason or "")
        if scene.role_influence_applied or "roleInfluenceApplied=true" in reason:
            role_influence_applied_scenes += 1
        if scene.multi_character_identity_lock or "multiCharacterIdentityLock=true" in reason:
            identity_lock_applied_scenes += 1
    return {
        "ok": True,
        "storyboardOut": storyboard_out.model_dump(mode="json"),
        "directorOutput": director_output,
        "canonicalSceneContract": director_output.get("scenes") if isinstance(director_output, dict) else [],
        "finalSceneContract": director_output.get("scenes") if isinstance(director_output, dict) else [],
        "scenario": storyboard_out.full_scenario,
        "voiceScript": storyboard_out.voice_script,
        "bgMusicPrompt": effective_global_music_prompt,
        "brainPackage": brain_package,
        "meta": {
            "plannerSource": "gemini",
            "modelUsed": model_used,
            "attemptedModels": attempted_models,
            "retriedForJson": retried_for_json,
            "rawGeminiTextPreview": raw_text[:2000],
            "contractNormalizationApplied": bool(normalized_contract_fields),
            "normalizedContractFields": normalized_contract_fields,
            "audioDurationSec": coverage.get("audioDurationSec"),
            "audioDurationSec_payload": audio_duration_from_payload,
            "audioDurationSec_analysis": audio_duration_from_analysis,
            "audioConnected": audio_connected,
            "audioDurationSource": audio_duration_source,
            "audioUsedAsPrimaryNarrativeDriver": bool(full_audio_first or partial_audio_first),
            "audioPrimaryDriverReason": audio_primary_driver_reason,
            "fallbackMode": fallback_mode,
            "fallbackReason": fallback_reason,
            "noTextFallbackMode": no_text_fallback_mode,
            "authorialInterpretationLevel": authorial_interpretation_level,
            "audioLiteralnessLevel": audio_literalness_level,
            "audioSourceMode": audio_context.get("sourceMode"),
            "sourceOrigin_raw": source_origin_raw or None,
            "sourceOrigin_normalized": source_origin_normalized or None,
            "audioConnectedReason": audio_connected_reason,
            "audioUrl_raw": audio_url_raw or None,
            "audioUrl_normalized": str(audio_analysis.get("audioUrlNormalized") or audio_url_normalized or "") or None,
            "audioUrlResolutionMode": str(audio_analysis.get("audioUrlResolutionMode") or ("missing" if not audio_url_normalized else "invalid")),
            "audioResolvedPath": str(audio_analysis.get("audioResolvedPath") or "") or None,
            "audioResolutionReason": str(audio_analysis.get("audioResolutionReason") or "") or None,
            "preferAudioOverText": prefer_audio_over_text,
            "timelineSource": audio_context.get("timelineSource"),
            "segmentationMode": audio_context.get("segmentationMode"),
            "audioAnalysisAttempted": audio_analysis_attempted,
            "audioAnalysisOk": bool(audio_analysis.get("ok")),
            "audioAnalysisReason": str(audio_analysis.get("hint") or ("analysis_not_attempted" if not audio_analysis_attempted else "analysis_failed")),
            "phraseCount": len(audio_analysis.get("phrases") or []),
            "pauseCount": len(audio_analysis.get("pauseWindows") or []),
            "sectionCount": len(audio_analysis.get("sections") or []),
            "energyTransitionCount": len(audio_analysis.get("energyTransitions") or []),
            "usedPhraseFirstSegmentation": can_use_phrase_first,
            "sceneBoundaryStrategy": "phrase_pause_energy_section" if can_use_phrase_first else ("audio_duration_fallback" if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" else "text_default"),
            "audioAnalysisSource": audio_analysis.get("source"),
            "audioAnalysisHint": audio_analysis.get("hint"),
            "audioSemanticsOk": audio_semantics_ok,
            "audioTranscriptAvailable": audio_transcript_available,
            "audioSemanticSummaryAvailable": audio_semantic_summary_available,
            "audioNarrativeCore": audio_narrative_core[:600],
            "audioWorldContext": audio_world_context[:600],
            "audioEntities": audio_entities[:20],
            "audioImpliedEvents": audio_implied_events[:20],
            "audioSemanticsConfidence": _safe_float(audio_semantics.get("confidence"), 0.0),
            "audioContentTruthAvailable": audio_content_truth_available,
            "structuredPlannerDiagnostics": structured_planner_diagnostics,
            "sceneAudioEvidence": planner_scene_evidence,
            "fakeAudioFirstRiskSignals": fake_audio_first_risks,
            "fakeAudioFirstSuspected": fake_audio_first_suspected,
            "audioGroundingValidation": audio_grounding_validation,
            "audioGroundingScore": audio_grounding_validation.get("score"),
            "requestedContentType": content_type_policy.get("requestedValue"),
            "effectiveContentType": content_type_policy.get("value"),
            "requestedContentTypeEnabled": content_type_policy.get("requestedEnabled"),
            "contentTypeFallbackApplied": content_type_policy.get("fallbackApplied"),
            "contentTypePolicy": content_type_policy,
            "textHintPresent": text_hint_present,
            "storyCoreSource": story_core_source,
            "storyCoreSourceReason": story_core_source_reason,
            "storyFrameSource": story_frame_source if is_music_video_mode else None,
            "rhythmSource": rhythm_source if is_music_video_mode else None,
            "storyFrameSourceReason": story_frame_source_reason if is_music_video_mode else None,
            "rhythmSourceReason": rhythm_source_reason if is_music_video_mode else None,
            "story_core_source": story_core_source,
            "story_core_source_reason": story_core_source_reason,
            "story_frame_source": story_frame_source if is_music_video_mode else "",
            "rhythm_source": rhythm_source if is_music_video_mode else "",
            "story_frame_source_reason": story_frame_source_reason if is_music_video_mode else "",
            "rhythm_source_reason": rhythm_source_reason if is_music_video_mode else "",
            "castIdentityLocked": _coerce_bool(cast_identity_lock_info.get("enabled"), False) if is_music_video_mode else False,
            "castIdentityLockReason": cast_identity_lock_info.get("lockReason") if is_music_video_mode else "not_music_video",
            "castIdentityLockRewritesApplied": cast_identity_lock_info.get("textRewritesApplied") if is_music_video_mode else 0,
            "lockedRolePresentationByRole": cast_identity_lock_info.get("lockedRolePresentationByRole") if is_music_video_mode else {},
            "textHintInfluence": text_hint_influence,
            "audioInfluence": audio_influence,
            "narrativeBiasEstimate": narrative_bias_estimate,
            "effectiveRoleTypeByRole": effective_role_type_by_role,
            "roleAssignmentSource": role_assignment_source,
            "roleOverrideApplied": role_override_applied,
            "roleInfluenceAppliedScenes": role_influence_applied_scenes if is_music_video_mode else 0,
            "multiCharacterIdentityLockScenes": identity_lock_applied_scenes if is_music_video_mode else 0,
            "timelineStartSec": coverage.get("timelineStartSec"),
            "timelineEndSec": coverage.get("timelineEndSec"),
            "timelineCoverageSec": coverage.get("timelineCoverageSec"),
            "timelineCoverageRatio": coverage.get("timelineCoverageRatio"),
            "coverageRatio": coverage.get("coverageRatio"),
            "expectedAudioDurationSec": coverage.get("expectedAudioDurationSec"),
            "actualCoveredDurationSec": coverage.get("actualCoveredDurationSec"),
            "coverageSource": coverage.get("coverageSource"),
            "uncoveredTailSec": coverage.get("uncoveredTailSec"),
            "internalGapCount": coverage.get("internalGapCount"),
            "timelineCoverageStatus": coverage.get("timelineCoverageStatus"),
            "timelineCoverageWarnings": coverage.get("warnings") or [],
            "sceneBoundaryCandidatesSample": (audio_guidance.get("boundaryCandidates") or [])[:12] if isinstance(audio_guidance, dict) else [],
            "timelineRefinementAttempted": timeline_refinement_attempted,
            "timelineRefinementSucceeded": timeline_refinement_succeeded,
            "hints": list(dict.fromkeys([*audio_hints, *(audio_guidance.get("hints") or [])])) if isinstance(audio_guidance, dict) else audio_hints,
            "errors": audio_errors,
            "warnings": list(
                dict.fromkeys(
                    [
                        *normalization_warnings,
                        *coverage_warnings,
                        *audio_led_warnings,
                        *explicit_role_warnings,
                    ]
                )
            ),
        },
    }


def _build_master_request_text(payload: dict[str, Any], *, audio_context: dict[str, Any], audio_analysis: dict[str, Any], retry_level: int = 0) -> str:
    base = _build_request_text(payload, audio_context=audio_context, audio_analysis=audio_analysis, audio_guidance={})
    retry_hint = ""
    if retry_level >= 1:
        retry_hint += MASTER_JSON_RETRY_SUFFIX
    if retry_level >= 2:
        retry_hint += "\nRETRY 2 OVERRIDE: Reduce acts to 5. Minimal output."
    return (
        f"{base}\n\n"
        "MASTER MODE:\n"
        "- DO NOT generate scenes.\n"
        "- Return only world truth and story arc.\n"
        "- Keep summary <= 300 chars.\n"
        "- Keep acts count between 1 and 8.\n"
        "- Keep text compact.\n"
        "Return JSON contract:\n"
        "{\n"
        '  "audioUnderstanding": {},\n'
        '  "worldContext": "",\n'
        '  "narrativeStrategy": {\n'
        '    "didAudioRemainPrimary": true,\n'
        '    "didDirectorNoteOverrideAudio": false\n'
        "  },\n"
        '  "storyArc": {\n'
        '    "summary": "",\n'
        '    "acts": [\n'
        "      {\n"
        '        "id": "A1",\n'
        '        "approxStart": 0,\n'
        '        "approxEnd": 30,\n'
        '        "purpose": "",\n'
        '        "whatFromAudioDefinesThisAct": ""\n'
        "      }\n"
        "    ]\n"
        "  }\n"
        "}\n"
        f"{retry_hint}"
    )


def _short_text(value: Any, *, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _sanitize_master_output(parsed_payload: dict[str, Any]) -> dict[str, Any]:
    audio_understanding = parsed_payload.get("audioUnderstanding")
    if not isinstance(audio_understanding, dict):
        audio_understanding = {}
    world_context = _short_text(
        parsed_payload.get("worldContext")
        or (audio_understanding.get("worldContext") if isinstance(audio_understanding, dict) else "")
        or "",
        limit=300,
    )
    narrative_strategy = parsed_payload.get("narrativeStrategy") if isinstance(parsed_payload.get("narrativeStrategy"), dict) else {}
    story_arc = parsed_payload.get("storyArc") if isinstance(parsed_payload.get("storyArc"), dict) else {}
    acts_raw = story_arc.get("acts") if isinstance(story_arc.get("acts"), list) else []
    acts: list[dict[str, Any]] = []
    for idx, act in enumerate(acts_raw[:8], start=1):
        item = act if isinstance(act, dict) else {}
        acts.append(
            {
                "id": _short_text(item.get("id") or f"A{idx}", limit=12) or f"A{idx}",
                "approxStart": _safe_float(item.get("approxStart"), 0.0),
                "approxEnd": _safe_float(item.get("approxEnd"), 0.0),
                "purpose": _short_text(item.get("purpose"), limit=160),
                "whatFromAudioDefinesThisAct": _short_text(item.get("whatFromAudioDefinesThisAct"), limit=160),
            }
        )
    return {
        "audioUnderstanding": audio_understanding,
        "worldContext": world_context,
        "narrativeStrategy": {
            "didAudioRemainPrimary": _coerce_bool(narrative_strategy.get("didAudioRemainPrimary"), True),
            "didDirectorNoteOverrideAudio": _coerce_bool(narrative_strategy.get("didDirectorNoteOverrideAudio"), False),
        },
        "storyArc": {
            "summary": _short_text(story_arc.get("summary"), limit=300),
            "acts": acts,
        },
    }


def run_scenario_director_master(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ScenarioDirectorError("gemini_api_key_missing", "GEMINI_API_KEY is missing for Scenario Director generation.", status_code=503)

    audio_context = _normalize_audio_context(payload)
    audio_analysis = _build_audio_analysis_fallback(_safe_float(audio_context.get("audioDurationSec"), 0.0), "analysis_skipped")
    if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" and audio_context.get("hasAudio"):
        audio_analysis = _analyze_audio_for_scenario_director(audio_context)

    attempted_models: list[str] = []
    model_used = DEFAULT_TEXT_MODEL
    last_error: ScenarioDirectorError | None = None
    for retry_level in range(0, 3):
        body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are the production Scenario Director for PhotoStudio COMFY. Return strict JSON only. "
                            "MASTER MODE: DO NOT generate scenes. Return only world truth and story arc."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": _build_master_request_text(payload, audio_context=audio_context, audio_analysis=audio_analysis, retry_level=retry_level)}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 4096},
        }
        response, model_used, retry_models = _send_director_request(api_key, body)
        attempted_models.extend(model for model in retry_models if model not in attempted_models)
        if not isinstance(response, dict) or response.get("__http_error__"):
            if isinstance(response, dict) and response.get("__http_error__"):
                last_error = _build_scenario_director_http_error(
                    response,
                    fallback_code="gemini_request_failed",
                    fallback_message="Gemini request failed in Scenario Director master mode",
                )
                if last_error.code == "gemini_temporarily_unavailable":
                    break
            else:
                last_error = ScenarioDirectorError("gemini_request_failed", "Gemini request failed in Scenario Director master mode.", status_code=502)
            continue
        raw_text = _extract_gemini_text(response)
        finish_reason = _extract_gemini_finish_reason(response)
        logger.info(
            "[SCENARIO DIRECTOR][master] raw response chars=%s finish_reason=%s retry=%s",
            len(str(raw_text or "")),
            finish_reason or "unknown",
            retry_level,
        )
        parsed_payload = _extract_json_object(raw_text)
        if isinstance(parsed_payload, dict):
            sanitized = _sanitize_master_output(parsed_payload)
            return {
                "ok": True,
                "mode": "master",
                "masterOutput": sanitized,
                "meta": {"plannerSource": "gemini", "modelUsed": model_used, "attemptedModels": attempted_models, "retryCount": retry_level},
            }
        last_error = ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for Scenario Director master mode.",
            status_code=502,
            details={"rawPreview": str(raw_text or "")[:4000], "rawLength": len(str(raw_text or "")), "finishReason": finish_reason or "", "parseStage": f"master_retry_{retry_level}"},
        )

    raise last_error or ScenarioDirectorError("gemini_invalid_json", "Gemini returned invalid JSON for Scenario Director master mode.", status_code=502)


def _build_scenes_request_text(
    payload: dict[str, Any],
    *,
    master_output: dict[str, Any],
    start_sec: float,
    end_sec: float,
    expected_scenes: int,
    audio_analysis: dict[str, Any],
    retry_level: int = 0,
) -> str:
    retry_hint = ""
    if retry_level >= 1:
        retry_hint += SCENES_JSON_RETRY_SUFFIX + "\nRETRY 1 OVERRIDE: Return ONLY JSON. Short fields only."
    if retry_level >= 2:
        retry_hint += "\nRETRY 2 OVERRIDE: Keep compact fields, but preserve phrase boundaries; do not collapse adjacent distinct phrases."
    compact_audio = {
        "audioDurationSec": _safe_float(audio_analysis.get("audioDurationSec"), 0.0),
        "phrases": (audio_analysis.get("phrases") or [])[:16],
        "pauseWindows": (audio_analysis.get("pauseWindows") or [])[:16],
        "energyTransitions": (audio_analysis.get("energyTransitions") or [])[:16],
    }
    return (
        "SCENES MODE:\n"
        "- DO NOT redefine story.\n"
        "- Use provided MASTER as immutable truth.\n"
        "- Do not rewrite worldContext.\n"
        "- Do not expand beyond given time window.\n"
        "- Keep scene count <= expectedScenes and <= 12.\n"
        "- Keep every string <= 160 chars.\n"
        "Return JSON contract:\n"
        "{\n"
        '  "scenes": [\n'
        "    {\n"
        '      "id": "S1",\n'
        '      "t0": 0,\n'
        '      "t1": 5,\n'
        '      "whatFromAudioThisSceneUses": "",\n'
        '      "audioAnchorEvidence": "",\n'
        '      "frame": "",\n'
        '      "action": "",\n'
        '      "confidence": 0.8\n'
        "    }\n"
        "  ],\n"
        '  "diagnostics": { "audioGroundingScore": 0.82 }\n'
        "}\n\n"
        f"Runtime: {json.dumps({'timeWindow': {'startSec': start_sec, 'endSec': end_sec}, 'expectedScenes': expected_scenes, 'masterOutput': master_output, 'audioAnalysis': compact_audio, 'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}}, ensure_ascii=False)}"
        f"{retry_hint}"
    )


def _sanitize_scenes_output(parsed_payload: dict[str, Any], *, start_sec: float, end_sec: float, max_scenes: int) -> dict[str, Any]:
    scenes_raw = parsed_payload.get("scenes") if isinstance(parsed_payload.get("scenes"), list) else []
    scenes: list[dict[str, Any]] = []
    for idx, scene in enumerate(scenes_raw[:max_scenes], start=1):
        item = scene if isinstance(scene, dict) else {}
        t0 = max(start_sec, min(end_sec, _safe_float(item.get("t0"), start_sec)))
        t1 = max(t0, min(end_sec, _safe_float(item.get("t1"), t0)))
        scenes.append(
            {
                "id": _short_text(item.get("id") or f"S{idx}", limit=12) or f"S{idx}",
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "whatFromAudioThisSceneUses": _short_text(item.get("whatFromAudioThisSceneUses"), limit=160),
                "audioAnchorEvidence": _short_text(item.get("audioAnchorEvidence"), limit=160),
                "frame": _short_text(item.get("frame"), limit=160),
                "action": _short_text(item.get("action"), limit=160),
                "confidence": max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.5))),
            }
        )
    diagnostics_raw = parsed_payload.get("diagnostics") if isinstance(parsed_payload.get("diagnostics"), dict) else {}
    return {
        "scenes": scenes,
        "diagnostics": {"audioGroundingScore": max(0.0, min(1.0, _safe_float(diagnostics_raw.get("audioGroundingScore"), 0.0)))},
    }


def run_scenario_director_scenes(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ScenarioDirectorError("gemini_api_key_missing", "GEMINI_API_KEY is missing for Scenario Director generation.", status_code=503)

    master_output = payload.get("master_output") if isinstance(payload.get("master_output"), dict) else {}
    if not master_output:
        raise ScenarioDirectorError("master_output_missing", "master_output is required for Scenario Director scenes mode.", status_code=422)
    time_window = payload.get("timeWindow") if isinstance(payload.get("timeWindow"), dict) else {}
    start_sec = max(0.0, _safe_float(time_window.get("startSec"), 0.0))
    raw_end_sec = _safe_float(time_window.get("endSec"), start_sec + 60.0)
    end_sec = raw_end_sec if raw_end_sec >= start_sec else start_sec
    if (end_sec - start_sec) > 60.0:
        end_sec = round(start_sec + 60.0, 3)
    expected_scenes = int(_safe_float(payload.get("expectedScenes"), max(1, round((end_sec - start_sec) / 4.0))))
    expected_scenes = max(1, min(expected_scenes, 12))

    audio_context = _normalize_audio_context(payload)
    audio_analysis = _build_audio_analysis_fallback(_safe_float(audio_context.get("audioDurationSec"), 0.0), "analysis_skipped")
    if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" and audio_context.get("hasAudio"):
        audio_analysis = _analyze_audio_for_scenario_director(audio_context)

    attempted_models: list[str] = []
    model_used = DEFAULT_TEXT_MODEL
    last_error: ScenarioDirectorError | None = None
    for retry_level in range(0, 3):
        body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are the production Scenario Director for PhotoStudio COMFY. Return strict JSON only. "
                            "SCENES MODE: Do not redefine story. Use MASTER as immutable truth. Do not rewrite worldContext."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": _build_scenes_request_text(payload, master_output=master_output, start_sec=start_sec, end_sec=end_sec, expected_scenes=expected_scenes, audio_analysis=audio_analysis, retry_level=retry_level)}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 4096},
        }
        response, model_used, retry_models = _send_director_request(api_key, body)
        attempted_models.extend(model for model in retry_models if model not in attempted_models)
        if not isinstance(response, dict) or response.get("__http_error__"):
            if isinstance(response, dict) and response.get("__http_error__"):
                last_error = _build_scenario_director_http_error(
                    response,
                    fallback_code="gemini_request_failed",
                    fallback_message="Gemini request failed in Scenario Director scenes mode",
                )
                if last_error.code == "gemini_temporarily_unavailable":
                    break
            else:
                last_error = ScenarioDirectorError("gemini_request_failed", "Gemini request failed in Scenario Director scenes mode.", status_code=502)
            continue
        raw_text = _extract_gemini_text(response)
        finish_reason = _extract_gemini_finish_reason(response)
        logger.info(
            "[SCENARIO DIRECTOR][scenes] raw response chars=%s finish_reason=%s retry=%s",
            len(str(raw_text or "")),
            finish_reason or "unknown",
            retry_level,
        )
        parsed_payload = _extract_json_object(raw_text)
        if isinstance(parsed_payload, dict):
            max_scenes = 5 if retry_level >= 2 else expected_scenes
            sanitized = _sanitize_scenes_output(parsed_payload, start_sec=start_sec, end_sec=end_sec, max_scenes=max_scenes)
            return {
                "ok": True,
                "mode": "scenes",
                "timeWindow": {"startSec": start_sec, "endSec": end_sec},
                **sanitized,
                "meta": {"plannerSource": "gemini", "modelUsed": model_used, "attemptedModels": attempted_models, "retryCount": retry_level, "expectedScenes": expected_scenes},
            }
        last_error = ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for Scenario Director scenes mode.",
            status_code=502,
            details={"rawPreview": str(raw_text or "")[:4000], "rawLength": len(str(raw_text or "")), "finishReason": finish_reason or "", "parseStage": f"scenes_retry_{retry_level}"},
        )

    raise last_error or ScenarioDirectorError("gemini_invalid_json", "Gemini returned invalid JSON for Scenario Director scenes mode.", status_code=502)
