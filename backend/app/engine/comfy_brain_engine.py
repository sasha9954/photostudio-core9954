import base64
import json
import logging
import mimetypes
import re
import socket
import urllib.error
import urllib.request
from typing import Any

from app.core.config import settings
from app.engine.audio_first_planner import build_audio_first_planner_output, build_project_planning_input
from app.engine.gemini_planner_contract import (
    GeminiPlannerValidationReport,
    GeminiPlanningStatus,
    build_gemini_planner_input as build_audio_first_gemini_planner_input,
    build_gemini_planner_output_contract as build_audio_first_gemini_planner_output_contract,
    build_gemini_planner_runtime_payload as build_audio_first_gemini_planner_runtime_payload,
    build_gemini_planner_system_rules as build_audio_first_gemini_planner_system_rules,
    map_gemini_plan_to_canonical_audio_first_output,
    parse_gemini_planner_output,
    validate_gemini_planner_output as validate_audio_first_gemini_planner_output,
)
from app.engine.clip_scene_planner import _load_audio_analysis, plan_comfy_clip
from app.engine.comfy_reference_profile import (
    _load_image_inline_part,
    _read_local_static_asset,
    _resolve_reference_url,
    build_reference_profiles,
    resolve_reference_role_type,
    summarize_profiles,
)
from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)


PRIMARY_GEMINI_PLANNER_MODEL = "gemini-3.1-pro-preview"
FALLBACK_GEMINI_MODEL = (getattr(settings, "GEMINI_TEXT_MODEL_FALLBACK", None) or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
RAW_GEMINI_FALLBACK_CHAIN = str(getattr(settings, "GEMINI_TEXT_MODEL_FALLBACK_CHAIN", "") or "").strip()
GEMINI_ONLY_PLANNER_MODEL_FALLBACKS = [
    model.strip()
    for model in (RAW_GEMINI_FALLBACK_CHAIN.split(",") if RAW_GEMINI_FALLBACK_CHAIN else [FALLBACK_GEMINI_MODEL, "gemini-2.5-pro"])
    if model.strip()
]
if FALLBACK_GEMINI_MODEL not in GEMINI_ONLY_PLANNER_MODEL_FALLBACKS:
    GEMINI_ONLY_PLANNER_MODEL_FALLBACKS.insert(0, FALLBACK_GEMINI_MODEL)
PROMPT_SYNC_STATUS_SYNCED = "synced"
PROMPT_SYNC_STATUS_NEEDS_SYNC = "needs_sync"
PROMPT_SYNC_STATUS_SYNCING = "syncing"
PROMPT_SYNC_STATUS_SYNC_ERROR = "sync_error"
COMFY_REF_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
COMFY_REF_DIRECTIVES = {"hero", "supporting", "environment_required", "required", "optional", "omit"}
COMFY_ACTIVE_DIRECTIVES = {"hero", "supporting", "environment_required", "required"}
COMFY_FALLBACK_ROLE_PRIORITY = ["character_1", "character_2", "character_3", "group", "animal", "location", "props", "style"]
COMFY_PLANNER_MODES = {"legacy", "gemini_only"}
COMFY_GENRES = {"horror", "romance", "comedy", "drama", "action", "thriller", "noir", "dreamy", "melancholy", "fashion", "surreal", "performance", "experimental"}
GEMINI_ONLY_MEDIA_ROLE_PRIORITY = ["character_1", "character_2", "character_3", "group", "animal", "props", "location", "style"]
MAX_GEMINI_IMAGE_PARTS = 8
MAX_GEMINI_AUDIO_INLINE_BYTES = 20 * 1024 * 1024
GEMINI_ONLY_TRANSITION_TYPES = {"start", "continuation", "enter_transition", "justified_cut", "match_cut", "perspective_shift"}
GEMINI_ONLY_HUMAN_ANCHOR_TYPES = {"character", "POV", "human_trace", "none"}
GEMINI_ONLY_VISUAL_MODE_DEFAULT = "cinematic_real_world"
COMFY_CHARACTER_ROLE_DEFAULTS = {"character_1": "hero", "character_2": "support", "character_3": "support"}
COMFY_CHARACTER_ROLE_TYPES = {"auto", "hero", "antagonist", "support"}
COMFY_ROLE_DOMINANCE_MODES = {"off", "soft", "strict"}
SCENE_INTENTS = (
    "setup",
    "pursuit",
    "confrontation",
    "threat",
    "escape",
    "support",
    "dialogue",
    "reveal",
    "observation",
    "transition",
)

SCENE_INTENT_PHRASE_PATTERNS: list[tuple[str, tuple[str, ...], float]] = [
    ("escape", ("trying to escape", "tries to escape", "attempts to escape", "tries to get away", "runs away", "breaks free"), 0.87),
    ("pursuit", ("moves toward", "move toward", "moving toward", "closing distance", "closes distance", "closes in", "follows", "gives chase"), 0.86),
    ("confrontation", ("stands against", "stand against", "faces off", "faces ", "turns to", "direct standoff"), 0.85),
    ("threat", ("approaches slowly", "intimidates", "threatens"), 0.86),
    ("observation", ("watching", "observing silently", "watching silently", "keeps observing"), 0.84),
]

SCENE_INTENT_KEYWORD_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("pursuit", ("chase", "run", "follow", "track", "hunt")),
    ("confrontation", ("fight", "argue", "attack", "clash", "challenge")),
    ("threat", ("danger", "fear", "control", "menace", "intimidate", "threat")),
    ("escape", ("escape", "flee", "evade", "break out")),
    ("support", ("help", "comfort", "assist", "protect")),
    ("dialogue", ("talk", "say", "explain", "discuss", "converse")),
    ("reveal", ("discover", "realize", "uncover", "reveal")),
    ("observation", ("watch", "observe", "notice", "scan")),
]
SYSTEM_PROMPT_CLIP_PLANNER_VERSION = "clip_planner_v1"
SYSTEM_PROMPT_CLIP_PLANNER = """You are a strict cinematic storyboard planner for COMFY CLIP.
You are a planner only, not an image generator.
Return JSON only.

Hard constraints:
- Allowed roles only: character_1, character_2, character_3, animal, group, location, style, props.
- Never invent new characters, entities, or role names.
- Never rename roles.
- Genre controls every scene consistently.
- Genre must materially shape sceneMeaning, visualIdea, newThreatOrChange, image prompts, and video prompts — not just metadata labels.
- Audio drives pacing, scene progression, escalation, and beat timing when audio is primary.
- For clip/gemini_only planning, you must decide scene count yourself. Scene count is not predetermined by the client.
- Derive scene boundaries from natural audio/semantic structure: phrase endings, pauses, energy shifts, repeated hooks, semantic pivots, dramatic turns, and escalation beats.
- Do not split the clip into evenly sized chunks unless the source genuinely supports it. Uneven scene durations are allowed and expected when justified.
- Optional analyzer cues may help timing, but they are helper-only and never the source of truth for final scene boundaries or scene count.
- Anti-stagnation is required: scenes must progress and avoid static repetition.
- Each scene must introduce at least one meaningful change: new action, threat, information, emotional state, spatial constraint, or escalation beat.
- STRICT INTENT RULE: each scene MUST have a strong narrative intent. Avoid generic intents like "transition" unless transition evidence is explicit.
- If multiple scenes share the same intent, vary intents to preserve progression.
- Do not generate filler scenes that only restate the same moment with cosmetic variation.
- Escalation is required across the sequence unless the request explicitly calls for flat stillness.
- If a transformed character remains physically present in a scene, that active character must remain in activeRoles.
- tensionLevel must be numeric 1-10, or clearly convertible to that scale.
- Props are real physical objects when provided and may drive the story.
- propFunction is required only when props are active in the scene.
- The storyboard must be technically usable for downstream rendering and front-end consumption.

Output contract:
- Top level JSON object only.
- Include: genre, sceneCount, scenes.
- Each scene should stay compact, concrete, and production-usable.
- No literary prose, no markdown, no explanations outside JSON."""

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_GENRE_SCENE_DIRECTIVES: dict[str, dict[str, str]] = {
    "horror": {
        "en": "Play true horror: escalating dread, active threat, corrupted realism, trap logic, safety violation, and imminent attack.",
        "ru": "Играй в полноценный хоррор: нарастающий ужас, активную угрозу, искажённый реализм, ловушку, нарушение безопасности и ощущение неминуемой атаки.",
    },
    "thriller": {
        "en": "Play tense thriller: suspicion, pursuit, hidden intent, tactical pressure, unstable control, and hard revelations.",
        "ru": "Играй в напряжённый триллер: подозрение, преследование, скрытый умысел, тактическое давление, потерю контроля и жёсткие раскрытия.",
    },
    "romance": {
        "en": "Play sincere romance: emotional reciprocity, longing, intimacy, vulnerability, and relational change that truly matters.",
        "ru": "Играй в искреннюю романтику: эмоциональную взаимность, тоску, близость, уязвимость и по-настоящему значимые изменения в отношениях.",
    },
    "melancholy": {
        "en": "Play melancholy: emptiness, fragile memory, emotional distance, fading warmth, and pain that still lingers.",
        "ru": "Играй в меланхолию: пустоту, хрупкую память, эмоциональную дистанцию, уходящее тепло и боль, которая всё ещё держится внутри.",
    },
    "dreamy": {
        "en": "Play dreamy mood: suspended time, surreal softness, dream logic, floating transitions, and luminous ambiguity.",
        "ru": "Играй в dreamlike-настроение: подвешенное время, сюрреалистическую мягкость, логику сна, парящие переходы и светящуюся неоднозначность.",
    },
}


def _to_float(value: Any) -> float | None:
    try:
        n = float(value)
    except Exception:
        return None
    return n if n == n and n != float("inf") and n != float("-inf") else None


def _round_sec(value: float | None) -> float | None:
    return round(float(value), 3) if value is not None else None


def _clean_refs_by_role(refs_by_role: dict[str, Any] | None) -> dict[str, list[dict[str, str]]]:
    roles = COMFY_REF_ROLES
    src = refs_by_role if isinstance(refs_by_role, dict) else {}
    out: dict[str, list[dict[str, str]]] = {}
    for role in roles:
        items = src.get(role)
        clean = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                clean_item = {"url": url, "name": str(item.get("name") or "").strip()}
                role_type = str(item.get("roleType") or "").strip().lower()
                if role_type in COMFY_CHARACTER_ROLE_TYPES:
                    clean_item["roleType"] = role_type
                    clean_item["roleTypeSource"] = "user_explicit" if role_type != "auto" else "user_auto"
                clean.append(clean_item)
        out[role] = clean
    return out


def _resolve_scene_active_roles(
    refs_used: list[str] | dict[str, Any] | None,
    directives: dict[str, str],
    available_roles: set[str],
    primary_role: str,
) -> list[str]:
    selected_from_used: list[str] = []
    if isinstance(refs_used, list):
        selected_from_used = [str(role).strip() for role in refs_used if str(role).strip() in COMFY_REF_ROLES]
    elif isinstance(refs_used, dict):
        selected_from_used = [str(role).strip() for role, include in refs_used.items() if str(role).strip() in COMFY_REF_ROLES and bool(include)]
    selected_from_used = [role for role in selected_from_used if role in available_roles and directives.get(role) != "omit"]

    selected_from_directives = [
        role
        for role in COMFY_REF_ROLES
        if role in available_roles and directives.get(role) in COMFY_ACTIVE_DIRECTIVES and directives.get(role) != "omit"
    ]

    active_roles: list[str] = []
    for role in selected_from_used + selected_from_directives:
        if role not in active_roles:
            active_roles.append(role)

    if not active_roles and primary_role in available_roles and directives.get(primary_role) != "omit":
        active_roles = [primary_role]
    if not active_roles:
        fallback_role = next(
            (role for role in COMFY_FALLBACK_ROLE_PRIORITY if role in available_roles and directives.get(role) != "omit"),
            None,
        )
        if fallback_role:
            active_roles = [fallback_role]
    return active_roles


def _normalize_scene_ref_roles(src: dict[str, Any], available_refs_by_role: dict[str, list[dict[str, str]]] | None) -> tuple[list[str], dict[str, str], str, list[str]]:
    available = available_refs_by_role if isinstance(available_refs_by_role, dict) else {}
    available_roles = {role for role in COMFY_REF_ROLES if isinstance(available.get(role), list) and len(available.get(role) or []) > 0}

    refs_used_raw = src.get("refsUsed")
    refs_used: list[str] = []
    if isinstance(refs_used_raw, list):
        refs_used = [str(role).strip() for role in refs_used_raw if str(role).strip() in COMFY_REF_ROLES]
    elif isinstance(refs_used_raw, dict):
        refs_used = [str(role).strip() for role, include in refs_used_raw.items() if str(role).strip() in COMFY_REF_ROLES and bool(include)]
    refs_used = list(dict.fromkeys([role for role in refs_used if role in available_roles]))

    primary_role = str(src.get("primaryRole") or "").strip()
    if primary_role not in COMFY_REF_ROLES or primary_role not in available_roles:
        primary_role = next((role for role in COMFY_FALLBACK_ROLE_PRIORITY if role in available_roles), "character_1")

    secondary_roles_raw = src.get("secondaryRoles")
    secondary_roles = [
        role for role in ([str(item).strip() for item in secondary_roles_raw] if isinstance(secondary_roles_raw, list) else [])
        if role in COMFY_REF_ROLES and role in available_roles and role != primary_role
    ]
    secondary_roles = list(dict.fromkeys(secondary_roles))

    directives_raw = src.get("refDirectives") if isinstance(src.get("refDirectives"), dict) else {}
    directives: dict[str, str] = {role: "omit" for role in COMFY_REF_ROLES}
    for role, value in directives_raw.items():
        clean_role = str(role).strip()
        clean_value = str(value).strip()
        if clean_role in COMFY_REF_ROLES and clean_value in COMFY_REF_DIRECTIVES:
            directives[clean_role] = clean_value

    directives[primary_role] = "hero" if primary_role in {"character_1", "character_2", "character_3", "group", "animal"} else "required"
    for role in secondary_roles:
        if role == "location":
            directives[role] = "environment_required"
        elif role == "style":
            directives[role] = "optional"
        elif role == "props":
            directives[role] = "required"
        else:
            directives[role] = "supporting"

    for role in refs_used:
        if directives.get(role) == "omit":
            if role == "location":
                directives[role] = "environment_required"
            elif role == "style":
                directives[role] = "optional"
            elif role == "props":
                directives[role] = "required"
            else:
                directives[role] = "supporting"

    active_roles = _resolve_scene_active_roles(refs_used, directives, available_roles, primary_role)

    if primary_role not in active_roles and active_roles:
        primary_role = active_roles[0]
        directives[primary_role] = "hero" if primary_role in {"character_1", "character_2", "character_3", "group", "animal"} else "required"

    return active_roles, directives, primary_role, secondary_roles


def _normalize_genre(value: Any) -> str:
    raw = str(value or "").strip()
    return raw if raw.lower() in COMFY_GENRES else ""


def _has_semantic_audio_hints(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(str(item or "").strip() for item in value)
    if isinstance(value, dict):
        return any(str(item or "").strip() for item in value.values())
    return False


def _resolve_clip_gemini_audio_story_mode(payload: dict[str, Any], requested_mode: str) -> tuple[str, str]:
    mode = str(payload.get("mode") or "clip").strip().lower()
    planner_mode = str(payload.get("plannerMode") or "legacy").strip().lower()
    normalized_requested = str(requested_mode or "lyrics_music").strip().lower() or "lyrics_music"
    if mode != "clip" or planner_mode != "gemini_only" or normalized_requested != "speech_narrative":
        return normalized_requested, ""

    transcript_text = str(payload.get("transcriptText") or "").strip()
    spoken_text_hint = str(payload.get("spokenTextHint") or "").strip()
    audio_semantic_summary = str(payload.get("audioSemanticSummary") or "").strip()
    audio_semantic_hints = payload.get("audioSemanticHints")
    lyrics_text = str(payload.get("lyricsText") or "").strip()
    has_speech_semantic_support = any(
        [
            transcript_text,
            spoken_text_hint,
            audio_semantic_summary,
            _has_semantic_audio_hints(audio_semantic_hints),
        ]
    )
    if has_speech_semantic_support:
        return normalized_requested, ""

    fallback_mode = "lyrics_music" if lyrics_text else "music_only"
    return fallback_mode, f"speech_narrative_disabled_without_semantic_support:{fallback_mode}"


def _classify_prompt_language(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return "missing"
    has_cyrillic = bool(_CYRILLIC_RE.search(value))
    has_latin = bool(_LATIN_RE.search(value))
    if has_cyrillic and not has_latin:
        return "ru"
    if has_latin and not has_cyrillic:
        return "en"
    if has_cyrillic and has_latin:
        return "mixed"
    return "unknown"


def _normalize_prompt_language_fields(*, ru_value: Any = "", en_value: Any = "", generic_value: Any = "") -> tuple[str, str]:
    ru_text = str(ru_value or "").strip()
    en_text = str(en_value or "").strip()
    generic_text = str(generic_value or "").strip()

    if ru_text and _classify_prompt_language(ru_text) == "en" and not en_text:
        en_text, ru_text = ru_text, ""
    if en_text and _classify_prompt_language(en_text) == "ru" and not ru_text:
        ru_text, en_text = en_text, ""

    generic_lang = _classify_prompt_language(generic_text)
    if generic_text:
        if generic_lang == "ru":
            if not ru_text:
                ru_text = generic_text
        elif generic_lang == "en":
            if not en_text:
                en_text = generic_text
        elif not en_text:
            en_text = generic_text

    return ru_text, en_text


def _genre_scene_directive(genre: Any, language: str = "en") -> str:
    genre_key = str(genre or "").strip().lower()
    payload = _GENRE_SCENE_DIRECTIVES.get(genre_key) or {}
    return str(payload.get(language) or payload.get("en") or "").strip()


def _ensure_genre_pressure(text: Any, genre: Any, *, language: str = "en") -> str:
    clean = str(text or "").strip()
    directive = _genre_scene_directive(genre, language=language)
    if not clean or not directive:
        return clean
    if directive.lower() in clean.lower():
        return clean
    separator = " " if clean.endswith((".", "!", "?")) else ". "
    return f"{clean}{separator}{directive}"


def _is_gemini_first_clip_mode(payload: dict[str, Any]) -> bool:
    return (
        str(payload.get("mode") or "clip").strip().lower() == "clip"
        and str(payload.get("plannerMode") or "legacy").strip().lower() == "gemini_only"
    )


def _build_role_type_by_role(refs_by_role: dict[str, Any] | None) -> dict[str, str]:
    refs = refs_by_role if isinstance(refs_by_role, dict) else {}
    role_types: dict[str, str] = {}
    for role in COMFY_CHARACTER_ROLE_DEFAULTS:
        items = refs.get(role)
        if not isinstance(items, list) or len(items) == 0:
            continue
        explicit_role_type = next(
            (
                str((item or {}).get("roleType") or "").strip().lower()
                for item in items
                if isinstance(item, dict) and str((item or {}).get("roleType") or "").strip().lower() in COMFY_CHARACTER_ROLE_TYPES
            ),
            "",
        )
        if explicit_role_type == "auto":
            role_types[role] = "auto"
            continue
        role_types[role] = resolve_reference_role_type(role, items)
    return role_types


def _build_role_selection_source_by_role(
    refs_by_role: dict[str, Any] | None,
    role_type_by_role: dict[str, str] | None,
) -> dict[str, str]:
    refs = refs_by_role if isinstance(refs_by_role, dict) else {}
    role_types = role_type_by_role if isinstance(role_type_by_role, dict) else {}
    selection_source_by_role: dict[str, str] = {}
    for role, default_role_type in COMFY_CHARACTER_ROLE_DEFAULTS.items():
        items = refs.get(role)
        if not isinstance(items, list) or not items:
            continue
        if any(str((item or {}).get("roleTypeSource") or "").strip().lower() == "user_explicit" for item in items if isinstance(item, dict)):
            selection_source_by_role[role] = "user_explicit"
            continue
        if any(str((item or {}).get("roleTypeSource") or "").strip().lower() == "user_auto" for item in items if isinstance(item, dict)):
            selection_source_by_role[role] = "user_auto"
            continue
        resolved_role_type = str(role_types.get(role) or "").strip().lower()
        if resolved_role_type and resolved_role_type != default_role_type:
            selection_source_by_role[role] = "inferred"
            continue
        selection_source_by_role[role] = "default_fallback"
    return selection_source_by_role


def _derive_role_mode(
    role_type_by_role: dict[str, str] | None,
    role_selection_source_by_role: dict[str, str] | None = None,
) -> tuple[str, str]:
    role_types = role_type_by_role if isinstance(role_type_by_role, dict) else {}
    selection_sources = role_selection_source_by_role if isinstance(role_selection_source_by_role, dict) else {}
    active_roles = {
        role
        for role in COMFY_CHARACTER_ROLE_DEFAULTS
        if str(role_types.get(role) or "").strip() or str(selection_sources.get(role) or "").strip()
    }
    if any(str(selection_sources.get(role) or "").strip().lower() == "user_explicit" for role in active_roles):
        return "locked", "user_explicit_roles_present"
    if any(str(selection_sources.get(role) or "").strip().lower() == "inferred" for role in active_roles):
        return "auto", "inferred_roles_auto_mode"
    return "auto", "defaults_only_auto_mode"


def _derive_role_dominance_mode(payload: dict[str, Any], role_mode: str) -> tuple[str, str, bool]:
    raw_value = str(payload.get("roleDominanceMode") or "").strip().lower()
    normalized_role_mode = str(role_mode or "").strip().lower() or "auto"
    if raw_value in COMFY_ROLE_DOMINANCE_MODES:
        return raw_value, "user_explicit", raw_value != "off"
    if normalized_role_mode == "locked":
        return "soft", "locked_mode_default", True
    return "off", "default_off", False


def normalize_comfy_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    mode = str(data.get("mode") or "clip").strip().lower()
    if mode not in {"clip", "kino", "reklama", "scenario"}:
        mode = "clip"
    planner_mode = str(data.get("plannerMode") or "legacy").strip().lower()
    if planner_mode not in COMFY_PLANNER_MODES:
        planner_mode = "legacy"
    output = str(data.get("output") or "comfy image").strip().lower()
    if output not in {"comfy image", "comfy text"}:
        output = "comfy image"

    requested_audio_story_mode = str(data.get("audioStoryMode") or "lyrics_music").strip().lower()
    if requested_audio_story_mode not in {"lyrics_music", "music_only", "music_plus_text", "speech_narrative"}:
        requested_audio_story_mode = "lyrics_music"
    audio_story_mode, audio_story_mode_guard_reason = _resolve_clip_gemini_audio_story_mode(data, requested_audio_story_mode)
    refs_by_role = _clean_refs_by_role(data.get("refsByRole"))
    role_type_by_role = _build_role_type_by_role(refs_by_role)
    role_selection_source_by_role = _build_role_selection_source_by_role(refs_by_role, role_type_by_role)
    role_mode, role_mode_reason = _derive_role_mode(role_type_by_role, role_selection_source_by_role)
    role_dominance_mode, role_dominance_mode_reason, role_dominance_applied = _derive_role_dominance_mode(data, role_mode)
    direct_mode_raw = data.get("direct_gemini_storyboard_mode")
    if direct_mode_raw is None:
        direct_mode_raw = data.get("directGeminiStoryboardMode")
    if isinstance(direct_mode_raw, bool):
        direct_mode_enabled = direct_mode_raw
    else:
        direct_mode_enabled = str(direct_mode_raw or "").strip().lower() in {"1", "true", "yes", "on"}

    return {
        "mode": mode,
        "plannerMode": planner_mode,
        "output": output,
        "audioStoryMode": audio_story_mode,
        "audioStoryModeRequested": requested_audio_story_mode,
        "audioStoryModeGuardReason": audio_story_mode_guard_reason,
        "stylePreset": str(data.get("stylePreset") or "realism").strip().lower(),
        "genre": _normalize_genre(data.get("genre")),
        "freezeStyle": bool(data.get("freezeStyle")),
        "text": str(data.get("text") or data.get("storyText") or "").strip(),
        "storyText": str(data.get("storyText") or data.get("text") or "").strip(),
        "inputMode": str(data.get("inputMode") or "").strip().lower() or None,
        "projectMode": str(data.get("projectMode") or "narration_first").strip().lower() or "narration_first",
        "lyricsText": str(data.get("lyricsText") or "").strip(),
        "transcriptText": str(data.get("transcriptText") or "").strip(),
        "spokenTextHint": str(data.get("spokenTextHint") or "").strip(),
        "audioSemanticHints": data.get("audioSemanticHints") if isinstance(data.get("audioSemanticHints"), (list, dict, str)) else "",
        "audioSemanticSummary": str(data.get("audioSemanticSummary") or "").strip(),
        "audioUrl": str(data.get("audioUrl") or data.get("masterAudioUrl") or "").strip(),
        "masterAudioUrl": str(data.get("masterAudioUrl") or data.get("audioUrl") or "").strip(),
        "audioDurationSec": _to_float(data.get("audioDurationSec")),
        "globalMusicTrackUrl": str(data.get("globalMusicTrackUrl") or data.get("musicTrackUrl") or data.get("sunoUrl") or "").strip(),
        "musicTrackUrl": str(data.get("musicTrackUrl") or data.get("globalMusicTrackUrl") or data.get("sunoUrl") or "").strip(),
        "refsByRole": refs_by_role,
        "roleTypeByRole": role_type_by_role,
        "roleSelectionSourceByRole": role_selection_source_by_role,
        "roleMode": role_mode,
        "roleModeReason": role_mode_reason,
        "roleDominanceMode": role_dominance_mode,
        "roleDominanceModeReason": role_dominance_mode_reason,
        "roleDominanceApplied": role_dominance_applied,
        "storyControlMode": str(data.get("storyControlMode") or "").strip(),
        "storyMissionSummary": str(data.get("storyMissionSummary") or "").strip(),
        "timelineSource": str(data.get("timelineSource") or "").strip(),
        "narrativeSource": str(data.get("narrativeSource") or "").strip(),
        "plannerRules": data.get("plannerRules") if isinstance(data.get("plannerRules"), dict) else {},
        "plannerOverrides": data.get("plannerOverrides") if isinstance(data.get("plannerOverrides"), dict) else {},
        "sceneCandidates": data.get("sceneCandidates") if isinstance(data.get("sceneCandidates"), list) else (data.get("scenes") if isinstance(data.get("scenes"), list) else []),
        "direct_gemini_storyboard_mode": direct_mode_enabled,
        "directGeminiStoryboardMode": direct_mode_enabled,
    }


def _summarize_profile_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts[:8])
    if isinstance(value, dict):
        parts = [f"{key}: {str(item).strip()}" for key, item in value.items() if str(item).strip()]
        return "; ".join(parts[:8])
    return ""


def normalize_entity_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip().lower()
    if not value:
        return "unknown"

    compact = value.replace("-", "_").replace(" ", "_")
    direct_map = {
        "human": "human",
        "person": "human",
        "people": "human",
        "character": "human",
        "character_ref": "human",
        "woman": "human",
        "man": "human",
        "girl": "human",
        "boy": "human",
        "actor": "human",
        "actress": "human",
        "animal": "animal",
        "pet": "animal",
        "dog": "animal",
        "cat": "animal",
        "horse": "animal",
        "bird": "animal",
        "wolf": "animal",
        "object": "object",
        "prop": "object",
        "props": "object",
        "item": "object",
        "accessory": "object",
        "thing": "object",
        "location": "location",
        "environment": "location",
        "place": "location",
        "scene": "location",
        "background": "location",
        "style": "style",
        "aesthetic": "style",
        "visual_style": "style",
        "look": "style",
        "group": "group",
        "crowd": "group",
        "people_group": "group",
    }
    if compact in direct_map:
        return direct_map[compact]

    if any(token in compact for token in ["character", "person", "human", "woman", "man", "actor", "actress"]):
        return "human"
    if any(token in compact for token in ["animal", "pet", "dog", "cat", "horse", "bird", "wolf"]):
        return "animal"
    if any(token in compact for token in ["object", "prop", "item", "accessory", "thing"]):
        return "object"
    if any(token in compact for token in ["location", "environment", "place", "scene", "background"]):
        return "location"
    if any(token in compact for token in ["style", "aesthetic", "visual"]):
        return "style"
    if any(token in compact for token in ["group", "crowd", "people"]):
        return "group"
    return "unknown"


def _normalize_transition_type(raw_value: Any, idx: int) -> str:
    value = str(raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
    alias_map = {
        "continuous": "continuation",
        "continue": "continuation",
        "same_camera": "continuation",
        "single": "justified_cut",
        "hard_cut": "justified_cut",
        "cut": "justified_cut",
        "entry_transition": "enter_transition",
        "enter": "enter_transition",
        "match": "match_cut",
        "pov_shift": "perspective_shift",
    }
    clean = alias_map.get(value, value)
    if clean in GEMINI_ONLY_TRANSITION_TYPES:
        return clean
    return "start" if idx == 0 else "continuation"


def _normalize_human_anchor_type(raw_value: Any, active_refs: list[str], src: dict[str, Any]) -> str:
    value = str(raw_value or "").strip()
    if value in GEMINI_ONLY_HUMAN_ANCHOR_TYPES:
        return value

    if any(role in active_refs for role in ["character_1", "character_2", "character_3", "group"]):
        return "character"

    text_blob = " ".join(
        [
            str(src.get("imagePromptRu") or src.get("imagePrompt") or ""),
            str(src.get("imagePromptEn") or ""),
            str(src.get("videoPromptRu") or src.get("videoPrompt") or ""),
            str(src.get("videoPromptEn") or ""),
            str(src.get("sceneAction") or ""),
            str(src.get("visualDescription") or ""),
            str(src.get("cameraPlan") or src.get("cameraIntent") or ""),
        ]
    ).lower()
    if any(token in text_blob for token in ["pov", "point of view", "first-person", "first person", "through the eyes", "from the explorer's view"]):
        return "POV"
    if any(token in text_blob for token in ["footprint", "footprints", "shadow", "hand", "hands", "glove", "breath", "breathing", "flashlight beam", "helmet cam", "equipment"]):
        return "human_trace"
    return "none"


def _infer_camera_type(camera_text: str) -> str:
    text = str(camera_text or "").strip().lower()
    if not text:
        return "locked_camera"
    camera_markers = [
        ("drone", ["drone", "aerial", "bird's-eye", "birds-eye", "overhead flyover", "helicopter"]),
        ("handheld", ["handheld", "shaky cam", "shoulder cam", "body cam", "helmet cam"]),
        ("dolly", ["dolly", "track", "tracking shot", "slider"]),
        ("crane", ["crane", "jib"]),
        ("steadicam", ["steadicam", "gimbal", "stabilized follow"]),
        ("POV", ["pov", "point of view", "first-person", "first person"]),
        ("static", ["static", "locked off", "tripod", "still frame"]),
        ("push_in", ["push in", "push-in"]),
    ]
    for label, markers in camera_markers:
        if any(marker in text for marker in markers):
            return label
    return "cinematic_camera"


def _extract_audio_mime_type(url: str, headers: dict[str, str], data: bytes) -> str:
    header_mime = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    if header_mime.startswith("audio/"):
        return header_mime
    guessed_from_url, _ = mimetypes.guess_type(url)
    if guessed_from_url and guessed_from_url.startswith("audio/"):
        return guessed_from_url
    if data.startswith(b"ID3") or data[:2] == b"\xff\xfb":
        return "audio/mpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    return ""


def _load_audio_inline_part(audio_url: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    resolved = _resolve_reference_url(audio_url)
    if not resolved:
        return None, "missing_audio_url", None

    data: bytes
    data_source_for_mime = resolved
    headers: dict[str, str] = {}
    local_data, local_source, local_error = _read_local_static_asset(resolved)
    if local_error and local_error != "local_asset_not_found":
        return None, local_error, None
    if local_data is not None:
        data = local_data
        data_source_for_mime = local_source
    else:
        req = urllib.request.Request(resolved, headers={"User-Agent": "photostudio-gemini-planner/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            return None, "audio_http_error", f"http_status:{exc.code}"
        except (socket.timeout, TimeoutError):
            return None, "audio_timeout", None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                return None, "audio_timeout", None
            return None, "audio_download_failed", str(exc.reason)[:180] if exc.reason else None
        except ValueError:
            return None, "audio_download_failed", None
        except Exception as exc:
            return None, "audio_download_failed", str(exc)[:180]

    if not data:
        return None, "audio_download_failed", None

    mime_type = _extract_audio_mime_type(data_source_for_mime, headers, data)
    if not mime_type:
        return None, "audio_invalid_mime", None
    if len(data) > MAX_GEMINI_AUDIO_INLINE_BYTES:
        return None, "audio_too_large_for_inline", f"bytes:{len(data)}"

    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }, "inline_audio_attached", None


def _build_gemini_only_multimodal_parts(normalized: dict[str, Any], request_text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"text": request_text}]
    refs_by_role = normalized.get("refsByRole") if isinstance(normalized.get("refsByRole"), dict) else {}

    audio_part_attached = False
    audio_attach_reason = "missing_audio_url"
    audio_attach_error = None
    audio_url = str(normalized.get("audioUrl") or "").strip()
    if audio_url:
        audio_part, audio_attach_reason, audio_attach_error = _load_audio_inline_part(audio_url)
        if audio_part:
            parts.append(audio_part)
            audio_part_attached = True

    attached_ref_roles: list[str] = []
    skipped_ref_roles: dict[str, str] = {}
    image_attach_errors: list[str] = []
    image_parts_attached_count = 0

    for role in GEMINI_ONLY_MEDIA_ROLE_PRIORITY:
        if image_parts_attached_count >= MAX_GEMINI_IMAGE_PARTS:
            skipped_ref_roles[role] = "global_image_part_limit_reached"
            continue
        refs = refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []
        if not refs:
            skipped_ref_roles[role] = "no_refs"
            continue

        attached_for_role = False
        first_error = None
        for item in refs[:2]:
            ref_url = str((item or {}).get("url") or "").strip()
            if not ref_url:
                first_error = first_error or "missing_ref_url"
                continue
            image_part, image_error = _load_image_inline_part(ref_url)
            if image_part:
                parts.append({"text": f"Reference image for role {role}."})
                parts.append(image_part)
                attached_ref_roles.append(role)
                image_parts_attached_count += 1
                attached_for_role = True
                break
            first_error = first_error or image_error or "image_attach_failed"
        if attached_for_role:
            continue
        skipped_ref_roles[role] = first_error or "image_attach_failed"
        image_attach_errors.append(f"{role}:{first_error or 'image_attach_failed'}")

    return parts, {
        "audioPartAttached": audio_part_attached,
        "audioAttachReason": audio_attach_reason,
        "audioAttachError": audio_attach_error,
        "imagePartsAttachedCount": image_parts_attached_count,
        "attachedRefRoles": attached_ref_roles,
        "skippedRefRoles": skipped_ref_roles,
        "imageAttachErrors": image_attach_errors,
        "mediaAttachSummary": {
            "audio": "attached" if audio_part_attached else "not_attached",
            "audioReason": audio_attach_reason,
            "imagePartsAttachedCount": image_parts_attached_count,
            "attachedRefRoles": attached_ref_roles,
            "skippedRefRoleCount": len(skipped_ref_roles),
        },
    }


def _collect_world_signal_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ["text", "lyricsText", "transcriptText", "spokenTextHint", "audioSemanticSummary", "storyMissionSummary"]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    hints = payload.get("audioSemanticHints")
    if isinstance(hints, str) and hints.strip():
        parts.append(hints.strip())
    elif isinstance(hints, list):
        parts.extend([str(item).strip() for item in hints if str(item).strip()])
    elif isinstance(hints, dict):
        parts.extend([f"{key}: {str(item).strip()}" for key, item in hints.items() if str(item).strip()])

    scene_candidates = payload.get("sceneCandidates") if isinstance(payload.get("sceneCandidates"), list) else []
    for scene in scene_candidates[:3]:
        if not isinstance(scene, dict):
            continue
        for key in ["sceneMeaning", "visualDescription", "sceneAction", "environmentMotion", "continuity"]:
            value = scene.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return " ".join(parts).strip().lower()


def _infer_world_detail(signal_text: str, mapping: dict[str, list[str]], fallback: str) -> str:
    haystack = f" {signal_text} "
    for label, variants in mapping.items():
        for variant in variants:
            needle = str(variant or "").strip().lower()
            if needle and f" {needle} " in haystack:
                return label
    return fallback


def _append_unique_strings(items: list[str], additions: list[str]) -> list[str]:
    out: list[str] = []
    for item in [*items, *additions]:
        clean = str(item or "").strip()
        if clean and clean not in out:
            out.append(clean)
    return out


def _has_refs(refs_by_role: dict[str, Any] | None) -> bool:
    refs = refs_by_role if isinstance(refs_by_role, dict) else {}
    return any(isinstance(items, list) and len(items) > 0 for items in refs.values())


def _build_optional_audio_cues(normalized: dict[str, Any]) -> dict[str, Any]:
    audio_url = str(normalized.get("audioUrl") or "").strip()
    if not audio_url:
        return {}
    analysis, _analysis_debug = _load_audio_analysis(audio_url, _to_float(normalized.get("audioDurationSec")))
    if not isinstance(analysis, dict):
        return {}

    def _sample_marks(items: Any, keys: tuple[str, ...], limit: int = 6) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(items, list):
            return out
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            compact: dict[str, Any] = {}
            for key in keys:
                value = item.get(key)
                if value is None:
                    continue
                if isinstance(value, (int, float)):
                    compact[key] = _round_sec(_to_float(value))
                else:
                    text = str(value).strip()
                    if text:
                        compact[key] = text
            if compact:
                out.append(compact)
        return out

    cues = {
        "helperOnly": True,
        "helperRule": "Use these as optional timing, phrase, pause, or energy hints only. They must never override your own scene-count or scene-boundary decisions.",
        "beatsSample": _sample_marks(analysis.get("beats"), ("time",)),
        "downbeatsSample": _sample_marks(analysis.get("downbeats"), ("time",)),
        "barsSample": _sample_marks(analysis.get("bars"), ("time", "index")),
        "vocalPhrasesSample": _sample_marks(analysis.get("vocalPhrases"), ("start", "end", "text")),
        "sectionHints": _sample_marks(analysis.get("sections"), ("start", "end", "label", "energy")),
        "energyHints": _sample_marks(analysis.get("energyHints"), ("start", "end", "label", "energy")),
        "pauseHints": _sample_marks(analysis.get("pauseHints"), ("start", "end", "label")),
    }
    return {key: value for key, value in cues.items() if value not in ("", None, [], {})}


def _derive_gemini_only_story_context(payload: dict[str, Any]) -> dict[str, Any]:
    has_audio = bool(str(payload.get("audioUrl") or "").strip())
    text_value = str(payload.get("text") or "").strip()
    has_text = bool(text_value)
    has_refs = _has_refs(payload.get("refsByRole"))
    audio_story_mode = str(payload.get("audioStoryMode") or "lyrics_music").strip().lower() or "lyrics_music"
    requested_audio_story_mode = str(payload.get("audioStoryModeRequested") or audio_story_mode).strip().lower() or audio_story_mode
    transcript_text = str(payload.get("transcriptText") or "").strip()
    spoken_text_hint = str(payload.get("spokenTextHint") or "").strip()
    audio_semantic_summary = str(payload.get("audioSemanticSummary") or "").strip()
    lyrics_text = str(payload.get("lyricsText") or "").strip()
    audio_semantic_hints = payload.get("audioSemanticHints")
    has_audio_semantic_hints = False
    has_audio_semantic_hints = _has_semantic_audio_hints(audio_semantic_hints)

    story_source = "none"
    narrative_source = "none"
    timeline_source = str(payload.get("timelineSource") or "").strip()
    story_mission_summary = str(payload.get("storyMissionSummary") or "").strip()
    genre = str(payload.get("genre") or "").strip()
    warnings: list[str] = []
    errors: list[str] = []
    weak_semantic_context = False
    semantic_context_reason = ""
    guard_reason = str(payload.get("audioStoryModeGuardReason") or "").strip()

    if has_audio:
        story_source = "audio"
        narrative_source = "audio"
        if audio_story_mode == "speech_narrative":
            timeline_source = "spoken semantic flow"
            if not story_mission_summary:
                story_mission_summary = "Build scenes from spoken meaning and semantic progression."
            semantic_support_present = any([transcript_text, spoken_text_hint, audio_semantic_summary, text_value])
            weak_semantic_context = not semantic_support_present
            if weak_semantic_context:
                semantic_context_reason = "audio present but no transcript/hints/text support"
                warnings.append("weak_semantic_context:audio present but no transcript/hints/text support")
        elif requested_audio_story_mode == "speech_narrative" and guard_reason:
            warnings.append(guard_reason)
        elif not timeline_source:
            timeline_source = "audio rhythm"
        if not story_mission_summary:
            if audio_story_mode == "music_only":
                story_mission_summary = "Build scenes from audio rhythm and emotional contour."
            elif audio_story_mode == "music_plus_text" and has_text:
                story_mission_summary = text_value[:220]
            else:
                story_mission_summary = "Build scenes from audio meaning, pacing and progression."
    elif has_text:
        story_source = "text"
        narrative_source = "text"
        if not timeline_source:
            timeline_source = "text semantic flow"
        if not story_mission_summary:
            story_mission_summary = text_value[:220]
    else:
        errors.append("no_story_source")
        warnings.append("narrative_source_missing")
        if not timeline_source:
            timeline_source = "none"
        if not story_mission_summary:
            story_mission_summary = "Narrative source missing."

    story_source, narrative_source = _normalize_story_sources(story_source, narrative_source)

    return {
        "storySource": story_source,
        "narrativeSource": narrative_source,
        "timelineSource": timeline_source,
        "storyMissionSummary": story_mission_summary,
        "genre": genre,
        "weakSemanticContext": weak_semantic_context,
        "semanticContextReason": semantic_context_reason,
        "audioStoryModeRequested": requested_audio_story_mode,
        "audioStoryModeGuardReason": guard_reason,
        "warnings": warnings,
        "errors": errors,
        "hasAudio": has_audio,
        "hasText": has_text,
        "hasRefs": has_refs,
        "hasTranscriptText": bool(transcript_text),
        "hasSpokenTextHint": bool(spoken_text_hint),
        "hasAudioSemanticSummary": bool(audio_semantic_summary),
        "hasAudioSemanticHints": has_audio_semantic_hints,
        "hasLyricsText": bool(lyrics_text),
    }


def _estimate_scene_count_target(normalized: dict[str, Any]) -> int:
    candidates = normalized.get("sceneCandidates") if isinstance(normalized.get("sceneCandidates"), list) else []
    candidate_count = len([item for item in candidates if isinstance(item, dict)])
    if candidate_count > 0 and not _is_gemini_first_clip_mode(normalized):
        return max(1, candidate_count)

    duration = _to_float(normalized.get("audioDurationSec"))
    if duration is not None and duration > 0:
        if _is_gemini_first_clip_mode(normalized):
            if duration <= 8:
                return 3
            if duration <= 15:
                return 4
            if duration <= 25:
                return 6
            if duration <= 40:
                return max(7, int(round(duration / 4.5)))
            return min(14, max(8, int(round(duration / 4.2))))
        if duration <= 8:
            return 2
        if duration <= 15:
            return 3
        if duration <= 25:
            return 4
        if duration <= 40:
            return 5
        return min(10, max(6, int(round(duration / 7.0))))

    text_signal = " ".join(
        str(normalized.get(key) or "").strip()
        for key in ["text", "lyricsText", "transcriptText", "spokenTextHint", "audioSemanticSummary"]
    ).strip()
    if text_signal:
        return 4
    return 3


def _compact_world_lock(world_lock: dict[str, Any]) -> dict[str, Any]:
    src = world_lock if isinstance(world_lock, dict) else {}
    compact = {
        "locationName": str(src.get("locationName") or src.get("environmentType") or "").strip(),
        "environmentType": str(src.get("environmentType") or "").strip(),
        "environmentSubtype": str(src.get("environmentSubtype") or "").strip(),
        "timeOfDay": str(src.get("timeOfDay") or "").strip(),
        "weather": str(src.get("weather") or "").strip(),
        "lighting": str(src.get("lighting") or "").strip(),
        "atmosphere": str(src.get("atmosphere") or "").strip(),
        "styleSummary": str(src.get("styleSummary") or src.get("visualStyle") or "").strip(),
        "continuityRules": [
            str(item).strip()
            for item in (src.get("continuityRules") if isinstance(src.get("continuityRules"), list) else [])[:6]
            if str(item).strip()
        ],
    }
    return {key: value for key, value in compact.items() if value not in ("", None, [])}


def _compact_entity_locks_summary(entity_locks: dict[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for role in COMFY_REF_ROLES:
        lock = entity_locks.get(role) if isinstance(entity_locks, dict) else None
        if not isinstance(lock, dict):
            continue
        visual_profile = lock.get("visualProfile") if isinstance(lock.get("visualProfile"), dict) else {}
        compact = {
            "role": role,
            "label": str(lock.get("label") or role).strip(),
            "entityType": str(lock.get("normalizedEntityType") or lock.get("entityType") or "").strip(),
            "summary": _summarize_profile_value(visual_profile) or _summarize_profile_value(lock.get("canonicalDetails")),
            "invariants": [
                str(item).strip()
                for item in (lock.get("invariants") if isinstance(lock.get("invariants"), list) else [])[:4]
                if str(item).strip()
            ],
            "forbiddenChanges": [
                str(item).strip()
                for item in (lock.get("forbiddenChanges") if isinstance(lock.get("forbiddenChanges"), list) else [])[:4]
                if str(item).strip()
            ],
        }
        summary.append({key: value for key, value in compact.items() if value not in ("", None, [])})
    return summary


def _compact_story_context(story_context: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    semantic_hints = normalized.get("audioSemanticHints")
    compact_hints: list[str] = []
    if isinstance(semantic_hints, list):
        compact_hints = [str(item).strip() for item in semantic_hints[:6] if str(item).strip()]
    elif isinstance(semantic_hints, dict):
        compact_hints = [f"{key}: {str(value).strip()}" for key, value in list(semantic_hints.items())[:6] if str(value).strip()]
    elif isinstance(semantic_hints, str) and semantic_hints.strip():
        compact_hints = [semantic_hints.strip()]

    scene_candidates = normalized.get("sceneCandidates") if isinstance(normalized.get("sceneCandidates"), list) else []
    compact_candidates: list[str] = []
    for scene in scene_candidates[:4]:
        if not isinstance(scene, dict):
            continue
        candidate_bits = [
            str(scene.get("sceneMeaning") or "").strip(),
            str(scene.get("visualDescription") or "").strip(),
            str(scene.get("sceneAction") or "").strip(),
        ]
        candidate_text = " | ".join(bit for bit in candidate_bits if bit)
        if candidate_text:
            compact_candidates.append(candidate_text[:220])

    compact = {
        "storySource": str(story_context.get("storySource") or "").strip(),
        "narrativeSource": str(story_context.get("narrativeSource") or "").strip(),
        "timelineSource": str(story_context.get("timelineSource") or "").strip(),
        "storyMissionSummary": str(story_context.get("storyMissionSummary") or "").strip(),
        "semanticSummary": str(normalized.get("audioSemanticSummary") or "").strip(),
        "lyricsExcerpt": str(normalized.get("lyricsText") or "").strip()[:240],
        "transcriptExcerpt": str(normalized.get("transcriptText") or "").strip()[:240],
        "spokenTextHint": str(normalized.get("spokenTextHint") or "").strip()[:180],
        "textExcerpt": str(normalized.get("text") or "").strip()[:240],
        "semanticHints": compact_hints,
        "sceneCandidates": compact_candidates,
    }
    return {key: value for key, value in compact.items() if value not in ("", None, [])}


def build_gemini_planner_input(
    normalized: dict[str, Any],
    story_context: dict[str, Any] | None = None,
    world_lock: dict[str, Any] | None = None,
    entity_locks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    story = story_context if isinstance(story_context, dict) else _derive_gemini_only_story_context(normalized)
    refs_by_role = _clean_refs_by_role(normalized.get("refsByRole") if isinstance(normalized.get("refsByRole"), dict) else {})
    hard_rules = [
        "Return strict JSON only.",
        "Use only allowed roles: character_1, character_2, character_3, animal, group, location, style, props.",
        "Do not invent characters or rename roles.",
        "Genre must stay consistent across all scenes.",
        "Audio meaning and pacing drive progression when audio is primary.",
        "Escalation and anti-stagnation are required.",
        "If a transformed character remains present, keep that character in activeRoles.",
        "tensionLevel must be numeric 1-10.",
        "propFunction is required only when props are active.",
    ]
    return {
        "mode": normalized.get("mode") or "clip",
        "genre": normalized.get("genre") or "",
        "sceneCountHint": {
            "optional": True,
            "reason": "debug_only_gemini_must_decide_scene_count",
            "value": None,
        },
        "sceneBoundaryDirective": {
            "owner": "gemini",
            "rule": "Determine scene count and scene boundaries from semantics, phrase endings, pauses, energy shifts, pacing, escalation, and dramatic progression. Never optimize for equal timing buckets.",
        },
        "audioContext": {
            "durationSec": _to_float(normalized.get("audioDurationSec")),
            "moodSummary": str(normalized.get("audioSemanticSummary") or story.get("storyMissionSummary") or "").strip(),
            "progression": str(story.get("timelineSource") or normalized.get("audioStoryMode") or "").strip(),
            "audioStoryMode": str(normalized.get("audioStoryMode") or "lyrics_music").strip(),
        },
        "refsByRole": refs_by_role,
        "storyIntent": {
            "storyMissionSummary": str(story.get("storyMissionSummary") or normalized.get("storyMissionSummary") or "").strip(),
            "storySource": str(story.get("storySource") or "").strip(),
            "narrativeSource": str(story.get("narrativeSource") or "").strip(),
            "timelineSource": str(story.get("timelineSource") or normalized.get("timelineSource") or "").strip(),
            "textRole": "fallback_only" if normalized.get("audioStoryMode") == "speech_narrative" and story.get("storySource") == "audio" else ("primary" if story.get("storySource") == "text" else "support"),
        },
        "roleTypeByRole": normalized.get("roleTypeByRole") if isinstance(normalized.get("roleTypeByRole"), dict) else {},
        "roleSelectionSourceByRole": normalized.get("roleSelectionSourceByRole") if isinstance(normalized.get("roleSelectionSourceByRole"), dict) else {},
        "roleMode": str(normalized.get("roleMode") or "auto").strip() or "auto",
        "roleModeReason": str(normalized.get("roleModeReason") or "").strip(),
        "roleDominanceMode": str(normalized.get("roleDominanceMode") or "off").strip() or "off",
        "roleDominanceModeReason": str(normalized.get("roleDominanceModeReason") or "").strip(),
        "roleDominanceApplied": bool(normalized.get("roleDominanceApplied")),
        "hardRules": hard_rules,
        "compactWorldLock": _compact_world_lock(world_lock or {}),
        "compactEntityLocksSummary": _compact_entity_locks_summary(entity_locks or {}),
        "compactStoryContext": _compact_story_context(story, normalized),
        "genreDirective": _genre_scene_directive(normalized.get("genre"), language="en"),
        "sceneChangeRequirement": "Each scene must introduce at least one meaningful change: action, threat, information, relation, spatial condition, emotional state, or world-state escalation.",
        "optionalAudioCues": _build_optional_audio_cues(normalized),
    }


def build_gemini_planner_request_text(planner_input: dict[str, Any]) -> str:
    return (
        "Planner input JSON follows. Use it as the only dynamic request payload.\n"
        "Return one JSON object matching the planner contract.\n"
        "Required top-level fields: genre, sceneCount, scenes.\n"
        "Required per-scene fields: sceneId, startSec, endSec, durationSec, tensionLevel, activeRoles, focalRole, continuityRule, visualIdea.\n"
        "You must decide scene count yourself. Scene count is your decision, not a hard client instruction.\n"
        "Derive scene boundaries from audio semantics, vocal phrases, pauses, energy shifts, pacing, escalation, repeated hooks, and dramatic progression.\n"
        "Do not follow equal timing buckets or optimize for evenly sized chunks unless the source genuinely supports it.\n"
        "Uneven scene durations are allowed and expected when justified.\n"
        "Each scene must be a story event and introduce at least one meaningful change. Do not generate filler scenes with cosmetic variation only.\n"
        "Genre must materially shape sceneMeaning, visualIdea, newThreatOrChange, imagePrompt, and videoPrompt.\n"
        "propFunction is required only when props are active.\n"
        f"{json.dumps(planner_input, ensure_ascii=False, indent=2)}"
    )


def _build_gemini_only_model_candidates(requested_model: str) -> list[str]:
    candidates: list[str] = []
    for model in [requested_model, *GEMINI_ONLY_PLANNER_MODEL_FALLBACKS]:
        clean = str(model or "").strip()
        if clean and clean not in candidates:
            candidates.append(clean)
    return candidates


def _normalize_story_sources(story_source: Any, narrative_source: Any) -> tuple[str, str]:
    normalized_story = str(story_source or "").strip().lower()
    if normalized_story not in {"audio", "text", "none"}:
        normalized_story = "none"

    normalized_narrative = str(narrative_source or "").strip().lower()
    if normalized_story == "audio":
        if normalized_narrative not in {"audio", "audio_primary"}:
            normalized_narrative = "audio"
        else:
            normalized_narrative = "audio"
    elif normalized_story == "text":
        if normalized_narrative not in {"text", "text_primary"}:
            normalized_narrative = "text"
        else:
            normalized_narrative = "text"
    else:
        normalized_narrative = "none"

    return normalized_story, normalized_narrative


def _humanize_storyboard_error(error_code: Any) -> str:
    code = str(error_code or "").strip()
    if not code:
        return ""
    if code == "no_story_source":
        return "No audio or text source for storyboard planning"
    if code == "gemini_model_not_supported":
        return "Gemini model is not supported for generateContent"
    if code == "gemini_invalid_json":
        return "Gemini returned invalid JSON"
    if code == "gemini_request_failed":
        return "Gemini request failed"
    if code == "gemini_api_key_missing":
        return "GEMINI_API_KEY is missing"
    if code.startswith("gemini_http_error:"):
        status_code = code.split(":", 1)[1].strip() or "unknown"
        return f"Gemini request failed with HTTP {status_code}"
    return code.replace("_", " ")


def _sanitize_gemini_error(diagnostics: dict[str, Any], resp: dict[str, Any] | None = None) -> tuple[str, str]:
    http_status = diagnostics.get("httpStatus")
    error_text = str((resp or {}).get("text") or diagnostics.get("errorText") or "").strip()
    error_text_l = error_text.lower()
    unsupported_markers = ["not supported", "unsupported", "not found", "generatecontent"]
    looks_unsupported = (
        any(marker in error_text_l for marker in unsupported_markers)
        and "model" in error_text_l
    ) or "model is not supported" in error_text_l or "not supported for generatecontent" in error_text_l or "unsupported for generatecontent" in error_text_l

    if http_status in {400, 404} and looks_unsupported:
        return "gemini_model_not_supported", "Gemini model is not supported for generateContent"
    if http_status:
        return f"gemini_http_error:{http_status}", f"Gemini request failed with HTTP {http_status}"
    if isinstance(resp, dict) and resp.get("errors") == ["gemini_invalid_json"]:
        return "gemini_invalid_json", "Gemini returned invalid JSON"
    return "gemini_request_failed", "Gemini request failed"


def _should_fallback_gemini_model(resp: dict[str, Any] | None, diagnostics: dict[str, Any]) -> bool:
    if not isinstance(resp, dict):
        return False
    http_status = diagnostics.get("httpStatus")
    error_text = str(resp.get("text") or diagnostics.get("errorText") or "").lower()
    if http_status not in {400, 404}:
        return False

    unsupported_markers = [
        "not supported",
        "unsupported",
        "not found",
        "generatecontent",
    ]
    has_unsupported_marker = any(marker in error_text for marker in unsupported_markers)
    model_hint = "model" in error_text or "models/" in error_text

    if http_status == 404:
        return has_unsupported_marker
    if http_status == 400:
        return has_unsupported_marker and model_hint
    return False


def _supports_system_instruction_error(diagnostics: dict[str, Any]) -> bool:
    if int(diagnostics.get("httpStatus") or 0) != 400:
        return False
    error_text = str(diagnostics.get("errorText") or "").lower()
    markers = ["systeminstruction", "unknown name", "unknown field", "cannot find field"]
    return "system" in error_text and any(marker in error_text for marker in markers)


def _build_world_lock(payload: dict[str, Any], reference_profiles: dict[str, Any]) -> dict[str, Any]:
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    location_profile = reference_profiles.get("location") if isinstance(reference_profiles.get("location"), dict) else {}
    style_profile = reference_profiles.get("style") if isinstance(reference_profiles.get("style"), dict) else {}
    location_refs = refs_by_role.get("location") if isinstance(refs_by_role.get("location"), list) else []
    style_refs = refs_by_role.get("style") if isinstance(refs_by_role.get("style"), list) else []
    visual_style = str(payload.get("stylePreset") or "realism").strip() or "realism"
    signal_text = _collect_world_signal_text(payload)
    location_name = str(((location_refs[0] or {}).get("name")) if location_refs else "").strip() or "anchored_main_location"
    location_summary = _summarize_profile_value(location_profile.get("visualProfile") or location_profile.get("summary")) or location_name
    style_summary = _summarize_profile_value(style_profile.get("visualProfile") or style_profile.get("summary")) or visual_style
    location_visual = location_profile.get("visualProfile") if isinstance(location_profile.get("visualProfile"), dict) else {}
    style_visual = style_profile.get("visualProfile") if isinstance(style_profile.get("visualProfile"), dict) else {}

    environment_type = _summarize_profile_value(location_visual.get("environmentType")) or _infer_world_detail(
        signal_text,
        {
            "desert": ["desert", "dune", "dunes", "sandstorm", "arid"],
            "bunker": ["bunker", "blast door", "underground base", "missile silo", "tunnel"],
            "forest": ["forest", "woods", "woodland", "oak", "pine", "jungle"],
            "city": ["city", "street", "downtown", "urban", "skyscraper"],
            "industrial": ["industrial", "factory", "warehouse", "plant", "concrete complex"],
        },
        location_name or "anchored_main_location",
    )
    environment_subtype = _summarize_profile_value(location_profile.get("subtype")) or _infer_world_detail(
        signal_text,
        {
            "oak forest": ["oak forest", "oak woods", "oak grove"],
            "pine forest": ["pine forest", "conifer forest", "taiga"],
            "concrete bunker": ["concrete bunker", "brutalist bunker", "reinforced bunker"],
            "sand dunes": ["sand dunes", "dunes", "erg"],
            "industrial city": ["industrial city", "factory district", "port city"],
        },
        _summarize_profile_value(location_profile.get("entityType")) or "single_continuous_world",
    )
    time_of_day = _summarize_profile_value(style_profile.get("timeOfDay")) or _infer_world_detail(
        signal_text,
        {
            "sunset": ["sunset", "golden hour", "dusk"],
            "night": ["night", "moonlight", "midnight", "after dark"],
            "artificial light": ["artificial light", "fluorescent", "flashlight", "emergency light", "neon"],
            "day": ["day", "daylight", "morning", "noon", "afternoon"],
        },
        "locked_from_input",
    )
    lighting_model = _summarize_profile_value(style_profile.get("lightingLogic") or style_profile.get("lighting")) or _infer_world_detail(
        signal_text,
        {
            "natural sunlight": ["sunlight", "natural light", "daylight", "sunlit"],
            "flashlight": ["flashlight", "torch beam", "searchlight"],
            "industrial": ["industrial light", "fluorescent", "sodium vapor", "warehouse lighting"],
            "firelight": ["firelight", "torchlight", "ember glow"],
        },
        f"{visual_style} continuity lighting",
    )
    atmosphere = _summarize_profile_value(style_profile.get("atmosphere")) or _infer_world_detail(
        signal_text,
        {
            "dusty": ["dusty", "dust", "grit", "sand haze"],
            "humid": ["humid", "wet air", "sweaty", "tropical"],
            "fog": ["fog", "mist", "haze"],
            "dry heat": ["dry heat", "arid heat", "heat shimmer"],
            "sterile industrial air": ["sterile", "clinical", "recycled air"],
        },
        f"{visual_style} atmosphere continuity",
    )
    material_language = _summarize_profile_value(location_profile.get("materials") or location_visual.get("surfaceState") or location_profile.get("visualProfile")) or _infer_world_detail(
        signal_text,
        {
            "sand": ["sand", "dune", "dust"],
            "concrete": ["concrete", "cement", "reinforced"],
            "metal": ["metal", "steel", "iron", "aluminum"],
            "wood": ["wood", "timber", "oak", "pine"],
            "stone": ["stone", "rock", "basalt", "granite"],
        },
        "preserve dominant material language",
    )
    color_palette = _summarize_profile_value(style_profile.get("palette") or style_profile.get("visualProfile")) or _infer_world_detail(
        signal_text,
        {
            "warm desert": ["warm desert", "amber sand", "sun-baked", "ochre"],
            "cold industrial": ["cold industrial", "steel blue", "cyan gray", "fluorescent gray"],
            "earthy forest": ["earthy forest", "moss green", "brown bark", "green canopy"],
            "neon urban": ["neon", "magenta", "electric blue", "city glow"],
        },
        style_summary,
    )
    continuity_rules = [
        "Keep one continuous world unless narration explicitly transitions elsewhere.",
        "Do not change location family, time of day, lighting logic, or material language without a story cue.",
        "References constrain world identity; audio/text drive semantic scene selection inside that world.",
    ]
    world_continuity_rules = [
        "environment must remain consistent unless explicit transition",
        "lighting must remain physically consistent",
        "materials must not change randomly",
        "vegetation type must not change (oak ≠ pine)",
        "architecture style must remain stable",
    ]
    forbidden_world_changes = [
        "changing forest type without transition",
        "changing desert type or sand color dramatically",
        "switching from natural light to artificial without cause",
        "changing architecture language (brutalist → sci-fi)",
    ]
    return {
        "worldType": location_summary or location_name,
        "locationType": location_name,
        "locationSubtype": environment_subtype,
        "environmentType": environment_type,
        "environmentSubtype": environment_subtype,
        "timeOfDay": time_of_day,
        "time_of_day": time_of_day,
        "lighting": lighting_model,
        "lighting_model": lighting_model,
        "shadows": _summarize_profile_value(style_profile.get("shadowLogic")) or "keep shadow logic stable scene to scene",
        "weather": _summarize_profile_value(style_profile.get("weather")) or "hold stable unless story explicitly transitions",
        "materials": material_language,
        "material_language": material_language,
        "architecture": _summarize_profile_value(location_profile.get("architecture") or location_profile.get("visualProfile")) or "preserve architecture language",
        "vegetation": _summarize_profile_value(location_profile.get("vegetation")) or "do not drift vegetation family without transition",
        "spacePhysics": _summarize_profile_value(location_profile.get("spacePhysics")) or "consistent spatial physics and scale",
        "palette": color_palette,
        "color_palette": color_palette,
        "atmosphere": atmosphere,
        "continuityRules": _append_unique_strings(continuity_rules, world_continuity_rules),
        "world_continuity_rules": world_continuity_rules,
        "forbiddenWorldDrift": _append_unique_strings(
            [
                "No abrupt biome swap without transition.",
                "No unexplained day/night jump.",
                "No random architecture or weather reset between adjacent scenes.",
            ],
            forbidden_world_changes,
        ),
        "forbidden_world_changes": forbidden_world_changes,
        "signalSources": {
            "textInput": bool(str(payload.get("text") or "").strip()),
            "audioMeaning": bool(str(payload.get("transcriptText") or payload.get("audioSemanticSummary") or payload.get("spokenTextHint") or "").strip()),
            "firstScenes": bool(payload.get("sceneCandidates")),
        },
        "sourceRefs": {
            "location": [str(item.get("url") or "").strip() for item in location_refs if str(item.get("url") or "").strip()],
            "style": [str(item.get("url") or "").strip() for item in style_refs if str(item.get("url") or "").strip()],
        },
    }


def _build_entity_locks(payload: dict[str, Any], reference_profiles: dict[str, Any]) -> dict[str, Any]:
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    entity_locks: dict[str, Any] = {}
    for role in COMFY_REF_ROLES:
        profile = reference_profiles.get(role) if isinstance(reference_profiles.get(role), dict) else None
        refs = refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []
        if not profile and not refs:
            continue
        visual_profile = profile.get("visualProfile") if isinstance((profile or {}).get("visualProfile"), dict) else {}
        raw_entity_type = str((profile or {}).get("entityType") or role).strip() or role
        normalized_entity_type = normalize_entity_type(raw_entity_type or role)
        canonical_details: dict[str, Any] = {}
        if normalized_entity_type == "human":
            canonical_details = {
                "gender_presentation": _summarize_profile_value(visual_profile.get("genderPresentation")) or "locked_from_reference",
                "body_type": _summarize_profile_value(visual_profile.get("bodyType")) or "locked_from_reference",
                "hair": _summarize_profile_value(visual_profile.get("hair")) or "locked_from_reference",
                "outfit": {
                    "top": _summarize_profile_value(visual_profile.get("outfitTop") or visual_profile.get("outfit")) or "locked_from_reference",
                    "bottom": _summarize_profile_value(visual_profile.get("outfitBottom") or visual_profile.get("outfit")) or "locked_from_reference",
                    "shoes": _summarize_profile_value(visual_profile.get("shoes") or visual_profile.get("footwear")) or "locked_from_reference",
                },
                "silhouette": _summarize_profile_value(visual_profile.get("silhouette") or visual_profile.get("bodyType")) or "locked_from_reference",
                "accessories": _summarize_profile_value(visual_profile.get("accessories")) or "locked_from_reference",
            }
        elif normalized_entity_type == "animal":
            canonical_details = {
                "species": _summarize_profile_value(visual_profile.get("species") or visual_profile.get("speciesLock")) or "locked_from_reference",
                "breed_type": _summarize_profile_value(visual_profile.get("breedLikeAppearance")) or "locked_from_reference",
                "fur_pattern": _summarize_profile_value(visual_profile.get("furPattern") or visual_profile.get("coat")) or "locked_from_reference",
                "color": _summarize_profile_value(visual_profile.get("dominantColors") or visual_profile.get("coat")) or "locked_from_reference",
                "proportions": _summarize_profile_value(visual_profile.get("bodyType") or visual_profile.get("morphology") or visual_profile.get("bodyBuild")) or "locked_from_reference",
            }
        elif normalized_entity_type == "object":
            canonical_details = {
                "object_type": _summarize_profile_value(visual_profile.get("objectCategory")) or "locked_from_reference",
                "shape": _summarize_profile_value(visual_profile.get("silhouette")) or "locked_from_reference",
                "material": _summarize_profile_value(visual_profile.get("material")) or "locked_from_reference",
                "color": _summarize_profile_value(visual_profile.get("dominantColors")) or "locked_from_reference",
                "scale_class": _summarize_profile_value(visual_profile.get("scaleClass")) or "locked_from_reference",
            }
        forbidden_changes = (profile or {}).get("forbiddenChanges") if isinstance((profile or {}).get("forbiddenChanges"), list) else []
        forbidden_changes = _append_unique_strings(
            [str(item).strip() for item in forbidden_changes if str(item).strip()],
            [
                "do not change outfit",
                "do not change hair",
                "do not change body type",
                "do not replace object",
                "do not change material",
            ],
        )
        entity_locks[role] = {
            "refId": role,
            "role": role,
            "label": str(((refs[0] or {}).get("name")) if refs else "").strip() or role,
            "entityType": normalized_entity_type,
            "rawEntityType": raw_entity_type,
            "normalizedEntityType": normalized_entity_type,
            "visualProfile": visual_profile,
            "canonicalDetails": canonical_details,
            "invariants": (profile or {}).get("invariants") if isinstance((profile or {}).get("invariants"), list) else [],
            "forbiddenChanges": forbidden_changes or [
                "Do not swap identity with a different entity.",
                "Do not mutate outfit/material/species without explicit story cause.",
            ],
            "forbidden_changes": forbidden_changes or [
                "do not change outfit",
                "do not change hair",
                "do not change body type",
                "do not replace object",
                "do not change material",
            ],
            "sourceRefUrls": [str(item.get("url") or "").strip() for item in refs if str(item.get("url") or "").strip()],
        }
    return entity_locks


def _build_gemini_planner_payload(payload: dict[str, Any], world_lock: dict[str, Any], entity_locks: dict[str, Any]) -> dict[str, Any]:
    story_context = _derive_gemini_only_story_context(payload)
    return build_gemini_planner_input(payload, story_context, world_lock, entity_locks)


def _build_gemini_only_planner_prompt(gemini_payload: dict[str, Any]) -> str:
    return build_gemini_planner_request_text(gemini_payload)


def _normalize_scene_timeline(scenes: list[dict[str, Any]], audio_duration_sec: float | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    safe_audio_duration = _to_float(audio_duration_sec)
    if safe_audio_duration is None or safe_audio_duration <= 0:
        total_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes)
        timeline_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in scenes) if scenes else 0.0
        return scenes, {
            "audioDurationSec": None,
            "timelineDurationSec": _round_sec(timeline_end),
            "sceneDurationTotalSec": _round_sec(total_sum),
            "sceneCount": len(scenes),
            "normalizationApplied": False,
            "normalizationReason": None,
            "timelineScale": 1.0,
        }

    original_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in scenes) if scenes else 0.0
    original_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes)
    needs_fix = original_end > (safe_audio_duration + 0.25)
    scale = 1.0
    reason = None
    if needs_fix and original_end > 0:
        scale = safe_audio_duration / original_end
        reason = f"timeline_scaled_to_audio:{_round_sec(original_end)}->{_round_sec(safe_audio_duration)}"

    normalized: list[dict[str, Any]] = []
    cursor = 0.0
    for idx, scene in enumerate(scenes):
        start = _to_float(scene.get("startSec"))
        end = _to_float(scene.get("endSec"))
        duration = _to_float(scene.get("durationSec"))

        if start is not None and end is not None and end >= start:
            next_start = start * scale if needs_fix else start
            next_end = end * scale if needs_fix else end
        else:
            guessed_duration = duration if duration is not None and duration > 0 else 0.0
            if guessed_duration <= 0 and safe_audio_duration > 0:
                guessed_duration = safe_audio_duration / max(1, len(scenes))
            next_start = cursor
            next_end = cursor + guessed_duration

        next_start = max(0.0, min(safe_audio_duration, next_start))
        next_end = max(next_start, min(safe_audio_duration, next_end))

        if idx == len(scenes) - 1:
            next_end = safe_audio_duration
            next_start = min(next_start, next_end)

        cursor = next_end
        normalized.append(
            {
                **scene,
                "startSec": _round_sec(next_start),
                "endSec": _round_sec(next_end),
                "durationSec": _round_sec(max(0.0, next_end - next_start)),
            }
        )

    normalized_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in normalized) if normalized else 0.0
    normalized_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in normalized)
    return normalized, {
        "audioDurationSec": _round_sec(safe_audio_duration),
        "timelineDurationSec": _round_sec(normalized_end),
        "sceneDurationTotalSec": _round_sec(normalized_sum),
        "sceneCount": len(normalized),
        "normalizationApplied": bool(reason),
        "normalizationReason": reason,
        "timelineScale": _round_sec(scale),
        "originalTimelineDurationSec": _round_sec(original_end),
        "originalSceneDurationTotalSec": _round_sec(original_sum),
    }


def _apply_final_scene_renumber_pass(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    renumbered: list[dict[str, Any]] = []
    for idx, scene in enumerate(scenes):
        src = scene if isinstance(scene, dict) else {}
        next_id = f"S{idx + 1}"
        patched = dict(src)
        patched["sceneId"] = next_id
        if "scene_id" in patched:
            patched["scene_id"] = next_id
        if "id" in patched and isinstance(patched.get("id"), str):
            raw_id = str(patched.get("id") or "").strip()
            if re.fullmatch(r"(?i)s\d+", raw_id) or re.fullmatch(r"(?i)scene[-_]\d+", raw_id):
                patched["id"] = next_id
        title_value = str(patched.get("title") or "").strip()
        if title_value:
            title_value = re.sub(r"\bS\d+\b", next_id, title_value)
            title_value = re.sub(r"\bScene\s+\d+\b", f"Scene {idx + 1}", title_value, flags=re.IGNORECASE)
            patched["title"] = title_value
        renumbered.append(patched)
    return renumbered


def _split_speech_text_chunks(text: str, pieces: int) -> list[str]:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if pieces <= 1 or not clean:
        return [clean] if clean else []

    sentences = [chunk.strip(" -—–") for chunk in re.split(r"(?<=[.!?…])\s+|\s*[;:]\s*", clean) if chunk.strip(" -—–")]
    source_parts = sentences if len(sentences) >= pieces else [clean]
    if len(source_parts) >= pieces:
        per_chunk = max(1, len(source_parts) // pieces)
        chunks: list[str] = []
        cursor = 0
        for idx in range(pieces):
            remaining_parts = len(source_parts) - cursor
            remaining_slots = pieces - idx
            take = max(1, round(remaining_parts / remaining_slots))
            chunk = " ".join(source_parts[cursor:cursor + take]).strip()
            if chunk:
                chunks.append(chunk)
            cursor += take
        return chunks[:pieces]

    words = clean.split(" ")
    approx = max(4, round(len(words) / pieces))
    chunks = []
    for idx in range(pieces):
        start = idx * approx
        end = len(words) if idx == pieces - 1 else min(len(words), (idx + 1) * approx)
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks[:pieces] or [clean]


def _build_speech_split_candidates(start_sec: float, end_sec: float, analysis: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    window = max(0.0, end_sec - start_sec)
    margin = min(0.75, max(0.35, window * 0.08))

    def add_candidate(raw: Any, reason: str, priority: int) -> None:
        point = _to_float(raw)
        if point is None:
            return
        if point <= (start_sec + margin) or point >= (end_sec - margin):
            return
        candidates.append({"time": _round_sec(point), "reason": reason, "priority": priority})

    for phrase in analysis.get("vocalPhrases") or []:
        if not isinstance(phrase, dict):
            continue
        add_candidate(phrase.get("end"), "sentence_endings", 0)
    for pause in analysis.get("pausePoints") or []:
        add_candidate(pause, "spoken_pauses", 1)
    for boundary in analysis.get("phraseBoundaries") or []:
        add_candidate(boundary, "semantic_breakpoints", 2)

    deduped: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (item["time"], item["priority"])):
        if deduped and abs(float(candidate["time"]) - float(deduped[-1]["time"])) < 0.45:
            if int(candidate["priority"]) < int(deduped[-1]["priority"]):
                deduped[-1] = candidate
            continue
        deduped.append(candidate)
    return deduped


def _pick_speech_split_points(start_sec: float, end_sec: float, analysis: dict[str, Any], pieces: int) -> tuple[list[float], list[str]]:
    duration = max(0.0, end_sec - start_sec)
    if pieces <= 1 or duration <= 8.0:
        return [], []

    candidates = _build_speech_split_candidates(start_sec, end_sec, analysis)
    targets = [start_sec + (duration * idx / pieces) for idx in range(1, pieces)]
    chosen: list[dict[str, Any]] = []
    min_gap = max(2.0, min(6.5, duration / (pieces + 0.2) * 0.68))

    for target in targets:
        best = None
        for candidate in candidates:
            point = float(candidate["time"])
            if any(abs(point - float(existing["time"])) < min_gap for existing in chosen):
                continue
            if point <= start_sec or point >= end_sec:
                continue
            score = (int(candidate["priority"]), abs(point - target), point)
            if best is None or score < best[0]:
                best = (score, candidate)
        if best is not None:
            chosen.append(best[1])

    fallback_counter = 0
    while len(chosen) < pieces - 1:
        fallback_counter += 1
        midpoint = start_sec + (duration * len(chosen) / pieces) + (duration / pieces / 2.0)
        midpoint = max(start_sec + 0.8, min(end_sec - 0.8, midpoint))
        if any(abs(midpoint - float(existing["time"])) < 1.2 for existing in chosen):
            midpoint += 0.6 * fallback_counter
            midpoint = max(start_sec + 0.8, min(end_sec - 0.8, midpoint))
        chosen.append({"time": _round_sec(midpoint), "reason": "approximate_midpoint", "priority": 9})

    chosen = sorted(chosen[:pieces - 1], key=lambda item: float(item["time"]))
    return [float(item["time"]) for item in chosen], [str(item["reason"]) for item in chosen]


def _split_oversized_speech_scenes(
    scenes: list[dict[str, Any]],
    normalized: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    audio_story_mode = str(normalized.get("audioStoryMode") or "").strip().lower()
    debug = {
        "oversizedSpeechScenesDetected": 0,
        "oversizedSpeechScenesSplitCount": 0,
        "speechSplitReasons": [],
    }
    if audio_story_mode != "speech_narrative" or not scenes:
        return scenes, debug, []

    analysis, analysis_debug = _load_audio_analysis(str(normalized.get("audioUrl") or ""), _to_float(normalized.get("audioDurationSec")))
    split_scenes: list[dict[str, Any]] = []
    warnings: list[str] = []

    for scene in scenes:
        duration = max(0.0, _to_float(scene.get("durationSec")) or 0.0)
        start_sec = _to_float(scene.get("startSec")) or 0.0
        end_sec = _to_float(scene.get("endSec")) or start_sec
        if duration <= 8.0 or end_sec <= start_sec:
            split_scenes.append(scene)
            continue

        debug["oversizedSpeechScenesDetected"] += 1
        pieces = max(2, int(duration // 7.2) + (1 if (duration % 7.2) > 0.2 else 0))
        pieces = min(pieces, max(2, int(duration // 2.6)))
        split_points, split_reasons = _pick_speech_split_points(start_sec, end_sec, analysis, pieces)
        boundaries = [start_sec, *split_points, end_sec]
        if len(boundaries) < 3:
            warnings.append(f"speech_scene_split_failed:{scene.get('sceneId')}")
            split_scenes.append(scene)
            continue

        text_chunks = _split_speech_text_chunks(str(scene.get("spokenText") or scene.get("sceneText") or ""), len(boundaries) - 1)
        meaning_chunks = _split_speech_text_chunks(str(scene.get("sceneMeaning") or ""), len(boundaries) - 1)
        for idx in range(len(boundaries) - 1):
            part_start = boundaries[idx]
            part_end = boundaries[idx + 1]
            part_duration = max(0.0, part_end - part_start)
            if part_duration < 1.2:
                continue
            part_scene = dict(scene)
            part_scene["sceneId"] = f"{scene.get('sceneId') or 'scene'}-s{idx + 1}"
            part_scene["title"] = str(scene.get("title") or f"Scene {scene.get('sceneId') or ''}").strip()
            part_scene["startSec"] = _round_sec(part_start)
            part_scene["endSec"] = _round_sec(part_end)
            part_scene["durationSec"] = _round_sec(part_duration)
            if idx < len(text_chunks):
                part_scene["spokenText"] = text_chunks[idx]
                if not str(part_scene.get("sceneText") or "").strip():
                    part_scene["sceneText"] = text_chunks[idx]
            if idx < len(meaning_chunks) and meaning_chunks[idx]:
                part_scene["sceneMeaning"] = meaning_chunks[idx]
            part_scene["continuity"] = "; ".join(
                item for item in [
                    str(scene.get("continuity") or "").strip(),
                    f"speech beat {idx + 1}/{len(boundaries) - 1}",
                ]
                if item
            )
            split_scenes.append(part_scene)
        debug["oversizedSpeechScenesSplitCount"] += max(0, len(boundaries) - 2)
        debug["speechSplitReasons"].append({
            "sceneId": str(scene.get("sceneId") or ""),
            "durationSec": _round_sec(duration),
            "analysisSource": analysis_debug.get("source") or "none",
            "splitReasons": split_reasons or ["approximate_midpoint"],
            "resultSceneCount": len(boundaries) - 1,
        })

    return split_scenes, debug, warnings


def build_comfy_planner_prompt(payload: dict[str, Any]) -> str:
    audio_story_mode = str(payload.get("audioStoryMode") or "lyrics_music").strip().lower()
    if audio_story_mode not in {"lyrics_music", "music_only", "music_plus_text", "speech_narrative"}:
        audio_story_mode = "lyrics_music"
    role_mode = str(payload.get("roleMode") or "auto").strip().lower() or "auto"
    role_dominance_mode = str(payload.get("roleDominanceMode") or "off").strip().lower()
    if role_dominance_mode not in COMFY_ROLE_DOMINANCE_MODES:
        role_dominance_mode = "off"

    # DEBUG VALIDATION CHECKLIST (manual):
    # 1) lyrics_music -> same song with lyrics should produce story beats that follow lyrical meaning.
    # 2) music_only -> same song should avoid lyric-derived plot; beats follow rhythm/energy only.
    # 3) music_plus_text -> same song + separate TEXT storyline should follow TEXT storyline; audio drives pace/energy.
    # 4) speech_narrative -> spoken meaning should drive scene-by-scene documentary/story planning.
    audio_story_rules = (
        "AUDIO STORY MODE RULES (STRICT):\n"
        "- lyrics_music: lyrics semantics are explicitly allowed and should be used as a narrative driver when vocals exist. You may use lyrical meaning, verse/chorus structure, emotional lyrical phrases, and explicit lyrical motifs to shape scene goals and transitions. Build scenes from lyrics+music together, not from music alone.\n"
        "- music_only: ignore lyrical semantics completely. Do not derive plot, events, world, objects, characters, or story beats from sung words. Do not build storyline from vocal text and do not substitute musical analysis with lyric interpretation. Use only rhythm, tempo, energy, dynamics, pacing, and emotional contour. If vocals exist, treat vocals as musical texture/emotional signal, never as narrative source.\n"
        "- music_plus_text: lyrics semantics must be ignored completely. TEXT node is the narrative driver for plot/events/world/objects/characters/story beats. AUDIO controls pacing, scene timing, montage rhythm, energy and emotional modulation. If lyrics conflict with TEXT, ignore lyrics semantics and follow TEXT. If TEXT is empty, fall back to a neutral music-driven storyboard without lyrics meaning.\n"
        "- speech_narrative: spoken meaning is the primary narrative driver. transcriptText, spokenTextHint, and audioSemanticSummary must drive scene planning scene-by-scene. Audio is semantic content, not only rhythm/emotion. TEXT node only supplements and clarifies the spoken meaning. If TEXT conflicts with the spoken meaning, the spoken meaning wins. Do not drift into generic cinematic mood unrelated to the speech content. If the speech topic is military, bunker, underground base, infrastructure, archival, documentary, or surveillance, stay inside that topic. Never invent romance, sunset, lifestyle, fashion, or music-video scenes unless the speech explicitly requires them.\n"
        "- speech_narrative hard rule: for every scene first build a human-readable scene visual brief containing sceneText, sceneMeaning, visualDescription, cameraPlan, motionPlan, and sfxPlan before writing prompts. imagePromptRu must directly depict the spoken meaning; videoPromptRu must describe meaningful motion in that exact scene. Abstract style-only prompts are forbidden unless the spoken segment itself is abstract. If the speech mentions desert, bunker, tunnel, blast door, missile, satellite, map, entrance, or underground facility, those objects/environments must appear in visualDescription, imagePromptRu, and videoPromptRu.\n"
        "- Non-compliance is an error: for music_only and music_plus_text never claim lyric semantics drove the story; for speech_narrative never ignore explicit spoken meaning."
    )
    segmentation_rules = (
        "SCENE SEGMENTATION RULES (HIGHEST PRIORITY):\n"
        "- Scene boundaries must follow meaningful phrase endings and real transition points.\n"
        "- Prefer cuts at: (1) vocal/semantic phrase ending, (2) musical phrase ending, (3) clear energy/rhythm/arrangement transition, (4) end of a visual micro-action, (5) emotional intention change.\n"
        "- Never split into equal-sized time blocks (forbidden: mechanical 5s, 10s, or evenly spaced chunks).\n"
        "- Duration is only a guardrail, not the main segmentation driver.\n"
        "- Guardrails: avoid <2.0s unless there is a strong accent cut; avoid >8.0s unless one continuous meaningful phrase/action justifies it.\n"
        "- If a scene exceeds 8.0s, include an explicit justification in sceneNarrativeStep or continuity.\n"
        "- Boundaries should feel cinematic and natural, not grid-based.\n"
    )
    audio_mode_segmentation = (
        "AUDIO MODE SEGMENTATION FOCUS:\n"
        "- lyrics_music: boundaries can follow lyric/sentence/sung-line endings plus music transitions.\n"
        "- music_only: ignore lyrics meaning; boundaries follow musical phrasing, energy shifts, rhythmic transitions, and structure only.\n"
        "- music_plus_text: ignore lyrics meaning; boundaries follow musical phrasing + meaningful TEXT chunks, synced to transition points.\n"
        "- speech_narrative: boundaries must follow spoken pauses, sentence endings, topic shifts, and meaningful semantic beats. Do not segment by equal chunks. Do not use music rhythm unless spoken structure is absent.\n"
    )
    role_logic_rules = (
        "CHARACTER ROLE LOGIC:\n"
        f"- roleMode={role_mode}. roleTypeByRole is provided in INPUT.roleTypeByRole.\n"
        f"- roleDominanceMode={role_dominance_mode}. roleDominanceApplied={bool(payload.get('roleDominanceApplied'))}.\n"
        "- roleSelectionSourceByRole is provided in INPUT.roleSelectionSourceByRole so you can distinguish explicit user role choices from fallback/default assignments.\n"
        "- If roleMode='locked': hero is the main narrative subject, antagonist is the source of conflict, tension, or opposition, and support assists, reacts, or accompanies.\n"
        "- If roleMode='locked': build scenes around hero perspective, introduce interaction between hero and antagonist, and maintain role consistency across scenes.\n"
        "- If roleMode='locked' and a hero exists, the audience perspective should primarily follow hero stakes, and the planner must not replace hero with antagonist as the main narrative subject.\n"
        "- If roleMode='locked' and a hero exists, hero must appear in the majority of scenes unless the user story explicitly requires a temporary hero absence.\n"
        "- If roleMode='locked' and an antagonist exists, the planner must not silently drop antagonist from the whole story.\n"
        "- If roleMode='locked' and an antagonist exists, at least one scene must show direct or indirect opposition, pressure, control, threat, pursuit, confrontation, or narrative interference caused by the antagonist.\n"
        "- If roleMode='locked' and support exists, support should appear in context, reaction, or interaction scenes when relevant.\n"
        "- If roleMode='auto': you may assign roles dynamically, but do not invent extreme conflict without justification.\n"
        "ROLE CONSISTENCY RULE:\n"
        "- Planner must not swap roles.\n"
        "- Hero must remain hero in all scenes.\n"
        "- Antagonist must not become hero.\n"
        "- Support must not override hero as the main subject.\n"
        "SCENE ROLE DISTRIBUTION:\n"
        "- Hero appears in the majority of scenes.\n"
        "- Antagonist appears in key tension scenes.\n"
        "- Support appears in context or interaction scenes.\n"
        "ROLE DOMINANCE CONTROL:\n"
        "- If roleDominanceMode='off': Gemini may decide scene dominance freely.\n"
        "- If roleDominanceMode='soft': prefer hero as primary narrative driver, prefer antagonist as source of pressure/conflict, prefer support as secondary reacting or assisting presence, but do not enforce this rigidly if scene logic clearly needs variation.\n"
        "- If roleDominanceMode='strict': each scene MUST have a dominant role.\n"
        "- If roleDominanceMode='strict': each scene MUST clearly indicate which role drives the scene (hero, antagonist, or support).\n"
        "- If roleDominanceMode='strict': the dominant role must actively influence the scene outcome, not just be present.\n"
        "- If roleDominanceMode='strict': hero MUST dominate most scenes unless explicitly justified by story structure.\n"
        "- If roleDominanceMode='strict': antagonist MUST dominate at least one scene where conflict, pressure, or opposition is clearly expressed.\n"
        "- If roleDominanceMode='strict': support should dominate only when the scene is specifically about assistance, reaction, witness perspective, or emotional support.\n"
        "- If roleDominanceMode='strict': avoid scenes where all roles are equally passive.\n"
        "- If roleDominanceMode='strict': avoid scenes where hero is present but not narratively central.\n"
        "- If roleDominanceMode='strict': avoid antagonist being decorative.\n"
        "- If roleDominanceMode='strict': locked role hierarchy must be respected across scene progression.\n"
        "SCENE INTENT LOGIC:\n"
        f"- Each scene should have a clear purpose (intent). Use intents such as: {', '.join(SCENE_INTENTS)}.\n"
        "- STRICT RULE: each scene MUST have a strong narrative intent; avoid generic intent labels without evidence.\n"
        "- Do NOT leave scenes purpose-less.\n"
        "- If repeated intents appear in adjacent or nearby scenes, vary intents to keep progression explicit.\n"
        "- Prefer clear progression across scenes: setup -> tension -> escalation -> outcome.\n"
        "ROLE + INTENT ALIGNMENT:\n"
        "- Hero-driven scenes should preferentially use: escape, confrontation, support (plus reveal/dialogue when justified).\n"
        "- Antagonist-driven scenes should preferentially use: threat, pursuit, control/confrontation pressure.\n"
        "- Secondary/support-driven scenes are flexible: support, observation, interaction, or dialogue.\n"
        "STRICT MODE ADDITION:\n"
        "- If roleDominanceMode='strict': each scene MUST have a clear intent.\n"
        "- If roleDominanceMode='strict': dominant role should align with scene intent.\n"
        "- If roleDominanceMode='strict': avoid unclear or neutral intent scenes.\n"
    )

    return (
        "You are COMFY storyboard planner. Return strict JSON only.\n"
        "Fields: ok, planMeta, globalContinuity, scenes, warnings, errors, debug.\n"
        f"Selected audioStoryMode={audio_story_mode}.\n"
        f"{audio_story_rules}\n"
        f"{segmentation_rules}\n"
        f"{audio_mode_segmentation}\n"
        f"{role_logic_rules}\n"
        "AUDIO is primary source for rhythm, emotional contour, dramatic shifts and timing.\n"
        "If INPUT.audioDurationSec is provided and > 0, scene timeline MUST stay inside [0, audioDurationSec].\n"
        "TEXT is optional support that clarifies intent.\n"
        "REFS are optional anchors for character/location/style/props continuity.\n"
        "REFERENCE CAST CONTRACT (STRICT):\n"
        "- All reference inputs are globally visible to you.\n"
        "- Treat references as cast members and world anchors.\n"
        "- For every scene explicitly decide which roles appear and which roles do not appear.\n"
        "- If a role has reference images, do not reinterpret that entity freely.\n"
        "- Human references must preserve identity, hair, face, outfit and body signature.\n"
        "- Never substitute selected human refs with a generic human.\n"
        "- Animal references must preserve species, breed-like appearance, coat color/pattern and body type.\n"
        "- Never substitute selected animal refs with different species/breed/coat identity.\n"
        "- Object references must preserve object category, silhouette, material, dominant colors and distinctive parts.\n"
        "- Never substitute selected props refs with another object type, geometry, or material family.\n"
        "- A scene may use one actor, multiple actors, only props, only environment, or any justified subset.\n"
        "- Never include unselected actors in a scene.\n"
        "- If a role is not selected for the scene, do not bring it into frame.\n"
        "- Never replace a selected actor with a generic invented version.\n"
        "- TWO-CHARACTER CONTRACT: if mustAppear contains both character_1 and character_2, sceneText/summary, sceneGoal, sceneMeaning, visualDescription, imagePromptRu/En, and videoPromptRu/En must explicitly describe their interaction in one frame (action + reaction partner). Avoid one-actor wording in such scenes.\n"
        "- HARD NO-CHARACTERS RULE: if there are no character refs and the transcript/text does not explicitly require people, charactersAllowed=false and you must not invent humans, women, men, crowds, portraits, or lifestyle extras. Use environment-only, infrastructure-only, archive-only, map-only, machinery-only, or object-only visuals instead.\n"
        "- Style references define visual language only and cannot cancel identity contracts.\n"
        "- Location references define world/environment identity anchors for the scene.\n"
        "- If a role is chosen as hero, that role must dominate shot semantics.\n"
        "Each scene must include: sceneId,title,startSec,endSec,durationSec,sceneNarrativeStep,sceneGoal,storyMission,"
        "sceneOutputRule,primaryRole,secondaryRoles,continuity,imagePromptRu,imagePromptEn,videoPromptRu,videoPromptEn,refsUsed,refDirectives,sceneSemanticSource,focalSubject,sceneAction,visualClue,cameraIntent,environmentMotion,forbiddenInsertions,"
        "heroEntityId,supportEntityIds,mustAppear,mustNotAppear,environmentLock,styleLock,identityLock,roleSelectionReason,intent.\n"
        "For speech_narrative scenes also include: sceneText,sceneMeaning,visualDescription,cameraPlan,motionPlan,sfxPlan.\n"
        "LANGUAGE CONTRACT (MANDATORY): imagePromptRu MUST be Russian; imagePromptEn MUST be English; videoPromptRu MUST be Russian; videoPromptEn MUST be English. Non-compliance is an error.\n"
        "VIDEO PROMPT CONTRACT (MANDATORY): videoPromptRu/videoPromptEn must be temporal and cannot be a copy of image prompts. They must explicitly include beginning, middle, and end progression; camera motion progression; micro-actions over time; and continuity from previous moment. When two characters must appear, include second-character reaction.\n"
        "Treat every scene as a narrative beat, not a generic landscape description. Specify the focal subject, the exact action/event happening now, the visual clue that carries narration meaning, and the camera intent.\n"
        "Avoid generic establishing-shot filler unless the scene is explicitly an establishing scene.\n"
        "Do not invent dominant unexplained foreground props. Do not introduce oversized machines, devices, or artifacts unless the narration meaning or explicit refs require them. If no prop is required, keep the frame clean and semantically grounded.\n"
        "Do NOT include runtime render-state fields in planner output (for example imageUrl, videoUrl, audioSliceUrl).\n"
        "Scenes should feel cinematic and watchable; avoid dry static actions unless story requires it.\n"
        "In debug include segmentationMode and segmentationReason briefly explaining why boundaries were selected, plus audioStoryMode, roleMode, roleModeReason, roleDominanceMode, roleDominanceModeReason, roleDominanceApplied, roleTypeByRole, roleSelectionSourceByRole, roleUsageByScene, dominantRoleByScene, dominantRoleTypeByScene, roleDominanceWarnings, roleValidationWarnings, roleValidationStatus, sceneIntentByScene, sceneIntentConfidence, sceneIntentWarnings, sceneIntentDiagnostics, textSource, transcriptAvailable, spokenMeaningPrimary, charactersAllowed, sceneSemanticSource per scene, peopleAutoAddedCount, oversizedSpeechScenesDetected, oversizedSpeechScenesSplitCount, speechSplitReasons, promptLanguageStatus, ruPromptMissing, enPromptPresent, and objectHallucinationRisk when available.\n"
        f"INPUT={json.dumps(payload, ensure_ascii=False)}"
    )


def build_comfy_planner_refinement_prompt(payload: dict[str, Any], previous_scenes: list[dict[str, Any]], refinement_reason: str) -> str:
    base_prompt = build_comfy_planner_prompt(payload)
    compact_scene_map = [
        {
            "sceneId": str(scene.get("sceneId") or ""),
            "startSec": _round_sec(_to_float(scene.get("startSec"))),
            "endSec": _round_sec(_to_float(scene.get("endSec"))),
            "durationSec": _round_sec(_to_float(scene.get("durationSec"))),
            "title": str(scene.get("title") or ""),
            "sceneNarrativeStep": str(scene.get("sceneNarrativeStep") or ""),
        }
        for scene in previous_scenes
    ]
    refinement_rules = (
        "SECOND PASS REFINEMENT (SEGMENTATION ONLY):\n"
        "- Your previous segmentation was too coarse/mechanical and needs refinement.\n"
        "- Keep the same story direction and continuity. This is NOT a totally new story.\n"
        "- Refine scene boundaries around meaningful phrase endings and real transition points.\n"
        "- If a scene is too long, split it into smaller phrase-complete scenes.\n"
        "- Avoid large generic blocks and avoid equal-duration chunking.\n"
        "- Prefer shorter meaningful scenes over broad time chunks when in doubt.\n"
        "- Keep boundaries natural, cinematic, and motivated by transitions.\n"
        "- Respect audioDurationSec and do not exceed the total audio timeline.\n"
        "- Preserve narrative continuity while improving segmentation granularity.\n"
        "SCENE INTENT RULES:\n"
        "- Each scene must have a clear intent.\n"
        "- Do NOT create neutral or empty scenes.\n"
        "- Align role behavior with scene intent.\n"
        "- Improve weak scenes by clarifying intent, not by rewriting entire structure.\n"
        "- Keep audioStoryMode logic strict:\n"
        "  * lyrics_music: lyric phrase endings + music transitions.\n"
        "  * music_only: ignore lyrics semantics, use musical phrasing/energy/structure transitions only.\n"
        "  * music_plus_text: ignore lyrics semantics, follow TEXT chunks + music transitions.\n"
        "  * speech_narrative: spoken pauses, sentence endings, topic shifts, and semantic beats. Never equal chunks.\n"
    )
    return (
        f"{base_prompt}\n\n"
        f"Refinement trigger reason: {refinement_reason}.\n"
        f"Previous coarse segmentation snapshot={json.dumps(compact_scene_map, ensure_ascii=False)}\n"
        f"{refinement_rules}"
    )


def _extract_text(resp: dict[str, Any]) -> str:
    try:
        parts = (((resp or {}).get("candidates") or [])[0].get("content") or {}).get("parts") or []
        text_parts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
        return "\n".join([x for x in text_parts if x])
    except Exception:
        return ""


def _extract_json(raw: str) -> dict[str, Any] | None:
    s = str(raw or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(s[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _call_gemini_plan(api_key: str, model: str, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    logger.info("[COMFY PLAN] gemini request start model=%s", model)
    resp = post_generate_content(api_key, model, body, timeout=120)
    raw = _extract_text(resp if isinstance(resp, dict) else {})
    raw_preview = raw[:3000] if raw else str((resp or {}).get("text") or "")[:3000]
    http_status = int(resp.get("status") or 0) if isinstance(resp, dict) and resp.get("__http_error__") else None
    diagnostics = {
        "requestedModel": model,
        "effectiveModel": model,
        "httpStatus": http_status,
        "rawPreview": raw_preview,
        "errorText": str((resp or {}).get("text") or "")[:3000] if isinstance(resp, dict) else "",
        "fallbackFrom": None,
        "fallbackTo": None,
        "sanitizedError": "",
    }
    if isinstance(resp, dict) and resp.get("__http_error__"):
        error_code, sanitized_error = _sanitize_gemini_error(diagnostics, resp)
        diagnostics["sanitizedError"] = sanitized_error
        logger.warning("[COMFY PLAN] gemini http error model=%s status=%s code=%s", model, resp.get("status"), error_code)
        return {"errors": [error_code], "debug": {"httpStatus": http_status, "sanitizedError": sanitized_error}}, diagnostics

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        diagnostics["sanitizedError"] = _humanize_storyboard_error("gemini_invalid_json")
        logger.warning("[COMFY PLAN] gemini invalid json model=%s", model)
        return {"errors": ["gemini_invalid_json"]}, diagnostics

    return parsed, diagnostics


def _call_gemini_plan_with_model_fallback(api_key: str, requested_model: str, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    model_candidates = _build_gemini_only_model_candidates(requested_model)
    last_parsed: dict[str, Any] = {"errors": ["gemini_http_error"]}
    last_diagnostics: dict[str, Any] = {
        "requestedModel": requested_model,
        "effectiveModel": requested_model,
        "httpStatus": None,
        "rawPreview": "",
        "errorText": "",
        "fallbackFrom": None,
        "fallbackTo": None,
        "modelCandidates": model_candidates,
        "sanitizedError": "",
    }

    for idx, candidate_model in enumerate(model_candidates):
        parsed, diagnostics = _call_gemini_plan(api_key, candidate_model, body)
        diagnostics["requestedModel"] = requested_model
        diagnostics["modelCandidates"] = model_candidates
        next_candidate = model_candidates[idx + 1] if idx + 1 < len(model_candidates) else None
        if idx > 0:
            diagnostics["fallbackFrom"] = model_candidates[idx - 1]
            diagnostics["fallbackTo"] = candidate_model
        if not _should_fallback_gemini_model(parsed, diagnostics):
            return parsed, diagnostics
        last_parsed = parsed
        last_diagnostics = diagnostics
        logger.warning(
            "[COMFY PLAN] gemini_only model fallback requested=%s fallback_from=%s fallback_to=%s status=%s",
            requested_model,
            candidate_model,
            next_candidate,
            diagnostics.get("httpStatus"),
        )

    return last_parsed, last_diagnostics


def _normalize_allowed_role(value: Any) -> str:
    role = str(value or "").strip()
    return role if role in COMFY_REF_ROLES else ""


def _map_tension_to_number(value: Any) -> tuple[int | None, str | None]:
    numeric = _to_float(value)
    if numeric is not None:
        return max(1, min(10, int(round(numeric)))), None

    text = str(value or "").strip().lower()
    if not text:
        return None, None

    mapping = {
        "low": 3,
        "moderate": 5,
        "medium": 5,
        "high": 7,
        "very high": 9,
        "very_high": 9,
        "maximum": 10,
        "max": 10,
    }
    for key, mapped in mapping.items():
        if key in text:
            return mapped, f"tension_mapped:{text}->{mapped}"
    return None, f"tension_unmapped:{text}"


def _scene_text_blob(scene: dict[str, Any]) -> str:
    return " ".join(
        str(scene.get(key) or "").strip().lower()
        for key in ["visualIdea", "newThreatOrChange", "sceneMeaning", "imagePrompt", "videoPrompt", "visualDescription"]
    ).strip()


def _extract_character_roles_from_text(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\bcharacter_[123]\b", text or "")))


def _text_suggests_prop_presence(text: str) -> bool:
    tokens = ["prop", "props", "object", "item", "weapon", "phone", "camera", "car", "motorcycle", "bike", "mask", "artifact", "tool"]
    return any(token in (text or "") for token in tokens)


def _text_suggests_transformation(text: str) -> bool:
    tokens = [
        "absorbed",
        "merged",
        "petrified",
        "turned into wall",
        "hidden inside location",
        "fused into",
        "sealed in",
        "embedded in",
        "inside the wall",
        "swallowed by",
    ]
    return any(token in (text or "") for token in tokens)


def _scene_supports_tension(scene: dict[str, Any]) -> bool:
    tension_level, _ = _map_tension_to_number(scene.get("tensionLevel"))
    if tension_level is not None and tension_level >= 6:
        return True
    scene_blob = _scene_text_blob(scene)
    tension_tokens = [
        "conflict",
        "opposition",
        "pressure",
        "threat",
        "threaten",
        "pursuit",
        "pursue",
        "confront",
        "confrontation",
        "interference",
        "control",
        "chase",
        "danger",
        "hunt",
    ]
    return any(token in scene_blob for token in tension_tokens)


def _estimate_scene_role_function(
    scene: dict[str, Any],
    *,
    primary_role_type: str,
    has_hero: bool,
    has_antagonist: bool,
    has_support: bool,
) -> str:
    if has_antagonist and _scene_supports_tension(scene):
        return "antagonist_pressure"
    if has_hero and has_support:
        return "shared_tension" if _scene_supports_tension(scene) else "support_reaction"
    if primary_role_type == "hero" or has_hero:
        return "hero_focus"
    if has_support:
        return "support_reaction"
    return "unknown"


def _estimate_scene_role_dominance(
    scene: dict[str, Any],
    *,
    role_type_by_role: dict[str, str] | None = None,
) -> dict[str, Any]:
    role_types = role_type_by_role if isinstance(role_type_by_role, dict) else {}
    scene_blob = _scene_text_blob(scene)
    active_roles = [
        str(role or "").strip()
        for role in (
            scene.get("activeRefs")
            if isinstance(scene.get("activeRefs"), list)
            else (scene.get("activeRoles") if isinstance(scene.get("activeRoles"), list) else [])
        )
        if str(role or "").strip()
    ]
    primary_role = str(scene.get("primaryRole") or "").strip()
    secondary_roles = [
        str(role or "").strip()
        for role in (scene.get("secondaryRoles") if isinstance(scene.get("secondaryRoles"), list) else [])
        if str(role or "").strip()
    ]
    ordered_roles: list[str] = []
    for role in [primary_role, *active_roles, *secondary_roles]:
        if role and role not in ordered_roles:
            ordered_roles.append(role)
    scored_roles = []
    for role in ordered_roles:
        score = 0
        role_type = str(role_types.get(role) or "unknown").strip().lower() or "unknown"
        role_lc = role.lower()
        explicit_mentions = scene_blob.count(role_lc) if role_lc else 0
        if role == primary_role:
            score += 3
        if role in active_roles:
            score += 2
        if role in secondary_roles:
            score += 1
        if explicit_mentions > 0:
            score += explicit_mentions
        if role_type == "hero":
            score += 1
            if any(token in scene_blob for token in ["perspective", "decide", "decision", "acts", "action", "lead", "drives"]):
                score += 1
        if role_type == "antagonist" and _scene_supports_tension(scene):
            score += 2
        if role_type == "support" and any(token in scene_blob for token in ["assist", "support", "react", "witness", "comfort"]):
            score += 1
        scored_roles.append((role, role_type, score))
    scored_roles.sort(key=lambda item: item[2], reverse=True)
    dominant_role = scored_roles[0][0] if scored_roles and scored_roles[0][2] > 0 else ""
    dominant_role_type = scored_roles[0][1] if scored_roles and scored_roles[0][2] > 0 else "unknown"
    dominant_score = scored_roles[0][2] if scored_roles else 0
    runner_up_score = scored_roles[1][2] if len(scored_roles) > 1 else 0
    confidence_delta = max(0, dominant_score - runner_up_score)
    if dominant_role_type == "antagonist" and _scene_supports_tension(scene):
        scene_role_function_estimate = "antagonist_pressure"
    elif dominant_role_type == "support":
        scene_role_function_estimate = "support_reaction"
    elif dominant_role_type == "hero":
        scene_role_function_estimate = "hero_focus"
    elif _scene_supports_tension(scene) and len(scored_roles) > 1:
        scene_role_function_estimate = "shared_tension"
    else:
        scene_role_function_estimate = "unknown"
    return {
        "dominantRole": dominant_role,
        "dominantRoleType": dominant_role_type,
        "dominanceConfidence": "high" if confidence_delta >= 2 else ("medium" if confidence_delta == 1 else "low"),
        "sceneRoleFunctionEstimate": scene_role_function_estimate,
    }


def _estimate_scene_intent(scene: dict[str, Any], scene_index: int) -> tuple[str, float]:
    raw_intent = str(scene.get("intent") or "").strip().lower()
    if raw_intent in SCENE_INTENTS:
        return raw_intent, 0.95
    scene_blob = " ".join(
        str(scene.get(key) or "")
        for key in [
            "title",
            "sceneText",
            "sceneMeaning",
            "visualDescription",
            "sceneNarrativeStep",
            "sceneGoal",
            "continuity",
            "cameraIntent",
            "sceneAction",
        ]
    ).lower()
    for intent, phrases, confidence in SCENE_INTENT_PHRASE_PATTERNS:
        if any(phrase in scene_blob for phrase in phrases):
            return intent, confidence
    for intent, keywords in SCENE_INTENT_KEYWORD_MAP:
        if any(keyword in scene_blob for keyword in keywords):
            return intent, 0.82
    if scene_index == 0:
        return "setup", 0.58
    return "transition", 0.5


def _extract_scene_intent_keywords(scene: dict[str, Any], intent: str) -> list[str]:
    scene_blob = " ".join(
        str(scene.get(key) or "")
        for key in [
            "title",
            "sceneText",
            "sceneMeaning",
            "visualDescription",
            "sceneNarrativeStep",
            "sceneGoal",
            "continuity",
            "cameraIntent",
            "sceneAction",
            "intent",
        ]
    ).lower()
    matches: list[str] = []
    for mapped_intent, phrases, _ in SCENE_INTENT_PHRASE_PATTERNS:
        if mapped_intent == intent:
            matches.extend([phrase for phrase in phrases if phrase in scene_blob])
    for mapped_intent, keywords in SCENE_INTENT_KEYWORD_MAP:
        if mapped_intent == intent:
            matches.extend([keyword for keyword in keywords if keyword in scene_blob])
    return list(dict.fromkeys(matches))


def _validate_scene_intent(scene: dict[str, Any], role: str, intent: str) -> list[str]:
    warnings: list[str] = []
    raw_intent = str(scene.get("intent") or "").strip().lower()
    normalized_intent = str(intent or "").strip().lower()
    normalized_role = str(role or "").strip().lower()
    if not raw_intent:
        warnings.append("strict_missing_scene_intent")
    if normalized_intent in {"", "transition", "setup"} and not _extract_scene_intent_keywords(scene, normalized_intent):
        warnings.append("weak_intent_signal")
    if (
        (normalized_role == "antagonist" and normalized_intent == "support")
        or (normalized_role == "hero" and normalized_intent == "threat")
        or (normalized_role == "support" and normalized_intent in {"threat", "confrontation"})
    ):
        warnings.append("strict_intent_role_mismatch")
    return warnings


def _scene_character_roles(scene: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    for key in ("activeRefs", "activeRoles", "secondaryRoles"):
        values = scene.get(key)
        if isinstance(values, list):
            for value in values:
                role = str(value or "").strip()
                if role in {"character_1", "character_2", "character_3"} and role not in roles:
                    roles.append(role)
    primary_role = str(scene.get("primaryRole") or "").strip()
    if primary_role in {"character_1", "character_2", "character_3"} and primary_role not in roles:
        roles.append(primary_role)
    scene_blob = _scene_text_blob(scene)
    for role in _extract_character_roles_from_text(scene_blob):
        if role not in roles:
            roles.append(role)
    return roles


def _intent_family(intent: str) -> str:
    normalized = str(intent or "").strip().lower()
    if normalized in {"pursuit", "escape", "confrontation"}:
        return "action"
    if normalized in {"threat", "setup", "transition", "observation"}:
        return "tension"
    if normalized in {"support", "dialogue", "reveal"}:
        return "interaction"
    return "other"


def _has_transition_or_setup_overuse(scene_intent_by_scene: list[dict[str, str]]) -> bool:
    scene_count = len(scene_intent_by_scene)
    if scene_count <= 0:
        return False
    transition_setup_count = sum(
        1
        for item in scene_intent_by_scene
        if str(item.get("intent") or "").strip().lower() in {"transition", "setup"}
    )
    return (transition_setup_count / scene_count) > 0.4


def _enforce_scene_intent_diversity(
    scenes: list[dict[str, Any]],
    scene_intent_by_scene: list[dict[str, str]],
    scene_intent_confidence: list[dict[str, Any]],
    scene_intent_diagnostics: list[dict[str, Any]],
) -> list[str]:
    if not scenes or not scene_intent_by_scene:
        return []
    confidence_by_scene_id = {str(item.get("sceneId") or ""): item for item in scene_intent_confidence}
    diagnostics_by_scene_id = {str(item.get("sceneId") or ""): item for item in scene_intent_diagnostics}
    warnings: list[str] = []
    last_intent = ""
    for idx, intent_item in enumerate(scene_intent_by_scene):
        current_intent = str(intent_item.get("intent") or "").strip().lower()
        if current_intent and current_intent == last_intent and idx < len(scenes):
            warnings.append("scene_intent_repetition_reduced")
            scene = scenes[idx]
            if isinstance(scene, dict):
                role_hint = str(diagnostics_by_scene_id.get(str(intent_item.get("sceneId") or ""), {}).get("dominantRoleType") or "").strip().lower()
                replacement = "reveal" if current_intent != "reveal" else "observation"
                if role_hint == "hero":
                    replacement = "escape" if current_intent != "escape" else "support"
                elif role_hint == "antagonist":
                    replacement = "pursuit" if current_intent != "pursuit" else "threat"
                elif role_hint == "support":
                    replacement = "observation" if current_intent != "observation" else "support"
                intent_item["intent"] = replacement
                scene["intent"] = replacement
                scene_id = str(intent_item.get("sceneId") or "")
                if scene_id in confidence_by_scene_id:
                    confidence_by_scene_id[scene_id]["intent"] = replacement
                    confidence_by_scene_id[scene_id]["confidence"] = 0.71
                    confidence_by_scene_id[scene_id]["source"] = "estimated"
                    confidence_by_scene_id[scene_id]["intentSource"] = "estimated"
                if scene_id in diagnostics_by_scene_id:
                    diagnostics_by_scene_id[scene_id]["intent"] = replacement
                    diagnostics_by_scene_id[scene_id]["confidence"] = 0.71
                    diagnostics_by_scene_id[scene_id]["source"] = "estimated"
                    diagnostics_by_scene_id[scene_id]["intentSource"] = "estimated"
                    diagnostics_by_scene_id[scene_id]["repetitionAdjusted"] = True
                current_intent = replacement
        last_intent = current_intent
    total_scenes = len(scene_intent_by_scene)
    max_transition_setup = max(1, int(total_scenes * 0.4))
    transition_setup_indices: list[int] = []
    for idx, intent_item in enumerate(scene_intent_by_scene):
        intent = str(intent_item.get("intent") or "").strip().lower()
        if intent in {"transition", "setup"}:
            transition_setup_indices.append(idx)
    if len(transition_setup_indices) > max_transition_setup:
        warnings.append("scene_intent_transition_setup_overuse")
        for idx in transition_setup_indices[max_transition_setup:]:
            if idx >= len(scenes):
                continue
            scene = scenes[idx]
            if not isinstance(scene, dict):
                continue
            scene_id = str(scene_intent_by_scene[idx].get("sceneId") or "")
            role_hint = str(diagnostics_by_scene_id.get(scene_id, {}).get("dominantRoleType") or "").strip().lower()
            replacement = "reveal"
            if role_hint == "hero":
                replacement = "escape"
            elif role_hint == "antagonist":
                replacement = "threat"
            elif role_hint == "support":
                replacement = "support"
            scene["intent"] = replacement
            scene_intent_by_scene[idx]["intent"] = replacement
            if scene_id in confidence_by_scene_id:
                confidence_by_scene_id[scene_id]["intent"] = replacement
                confidence_by_scene_id[scene_id]["confidence"] = 0.72
                confidence_by_scene_id[scene_id]["source"] = "estimated"
                confidence_by_scene_id[scene_id]["intentSource"] = "estimated"
            if scene_id in diagnostics_by_scene_id:
                diagnostics_by_scene_id[scene_id]["intent"] = replacement
                diagnostics_by_scene_id[scene_id]["confidence"] = 0.72
                diagnostics_by_scene_id[scene_id]["source"] = "estimated"
                diagnostics_by_scene_id[scene_id]["intentSource"] = "estimated"
                diagnostics_by_scene_id[scene_id]["diversityAdjusted"] = True
    family_counts: dict[str, int] = {"action": 0, "tension": 0, "interaction": 0}
    for item in scene_intent_by_scene:
        family = _intent_family(str(item.get("intent") or ""))
        if family in family_counts:
            family_counts[family] += 1
    missing_families = [family for family, count in family_counts.items() if count == 0]
    if missing_families and total_scenes >= 3:
        warnings.append("scene_intent_family_gap")
    return warnings


def _enforce_conflict_scene_if_needed(
    scenes: list[dict[str, Any]],
    scene_intent_by_scene: list[dict[str, str]],
    scene_intent_confidence: list[dict[str, Any]],
    scene_intent_diagnostics: list[dict[str, Any]],
) -> bool:
    if not scenes or len(scenes) != len(scene_intent_by_scene):
        return False
    distinct_characters: set[str] = set()
    for scene in scenes:
        if isinstance(scene, dict):
            distinct_characters.update(_scene_character_roles(scene))
    if len(distinct_characters) < 2:
        return False
    if any(str(item.get("intent") or "").strip().lower() in {"threat", "confrontation", "pursuit"} for item in scene_intent_by_scene):
        return False
    candidate_index = max(0, min(len(scene_intent_by_scene) - 1, len(scene_intent_by_scene) // 2))
    scene = scenes[candidate_index] if candidate_index < len(scenes) else {}
    if not isinstance(scene, dict):
        return False
    scene_id = str(scene_intent_by_scene[candidate_index].get("sceneId") or "")
    scene["intent"] = "confrontation"
    scene_intent_by_scene[candidate_index]["intent"] = "confrontation"
    for confidence_item in scene_intent_confidence:
        if str(confidence_item.get("sceneId") or "") == scene_id:
            confidence_item["intent"] = "confrontation"
            confidence_item["confidence"] = 0.74
            confidence_item["source"] = "estimated"
            confidence_item["intentSource"] = "estimated"
            break
    for diag_item in scene_intent_diagnostics:
        if str(diag_item.get("sceneId") or "") == scene_id:
            diag_item["intent"] = "confrontation"
            diag_item["confidence"] = 0.74
            diag_item["source"] = "estimated"
            diag_item["intentSource"] = "estimated"
            diag_item["conflictInjected"] = True
            break
    return True


def _build_role_distribution_warnings(
    role_usage_by_scene: list[dict[str, Any]],
    *,
    role_mode: str,
    role_dominance_mode: str = "off",
    role_type_by_role: dict[str, str] | None = None,
) -> list[str]:
    if str(role_mode or "").strip().lower() != "locked" or not role_usage_by_scene:
        return []
    role_types = role_type_by_role if isinstance(role_type_by_role, dict) else {}
    warnings: list[str] = []
    total_scenes = len(role_usage_by_scene)
    hero_roles = [role for role, role_type in role_types.items() if str(role_type or "").strip().lower() == "hero"]
    antagonist_roles = [role for role, role_type in role_types.items() if str(role_type or "").strip().lower() == "antagonist"]
    support_roles = [role for role, role_type in role_types.items() if str(role_type or "").strip().lower() == "support"]

    hero_scene_count = sum(1 for item in role_usage_by_scene if bool(item.get("hasHero")))
    if hero_roles and hero_scene_count <= total_scenes / 2:
        warnings.append("locked_hero_not_in_majority")

    antagonist_tension_scene_count = sum(
        1
        for item in role_usage_by_scene
        if bool(item.get("hasAntagonist")) and str(item.get("sceneRoleFunctionEstimate") or "") in {"antagonist_pressure", "shared_tension"}
    )
    if antagonist_roles and antagonist_tension_scene_count == 0:
        warnings.append("locked_antagonist_missing_from_tension_scenes")

    support_scene_count = sum(1 for item in role_usage_by_scene if bool(item.get("hasSupport")))
    if support_roles and support_scene_count == 0:
        warnings.append("locked_support_unused")

    normalized_dominance_mode = str(role_dominance_mode or "").strip().lower() or "off"
    if normalized_dominance_mode in {"soft", "strict"}:
        dominant_types = [str(item.get("dominantRoleType") or "").strip().lower() for item in role_usage_by_scene]
        tension_items = [item for item in role_usage_by_scene if str(item.get("sceneRoleFunctionEstimate") or "").strip() in {"antagonist_pressure", "shared_tension"}]
        hero_dominant_count = sum(1 for role_type in dominant_types if role_type == "hero")
        support_overrides_hero = sum(1 for role_type in dominant_types if role_type == "support") > hero_dominant_count and hero_roles
        if normalized_dominance_mode == "soft":
            if hero_roles and hero_dominant_count == 0:
                warnings.append("soft_hero_not_dominant")
            if antagonist_roles and tension_items and not any(str(item.get("dominantRoleType") or "").strip().lower() == "antagonist" for item in tension_items):
                warnings.append("soft_antagonist_not_dominant_in_tension")
        if normalized_dominance_mode == "strict":
            minimum_hero_dominance = max(1, (total_scenes + 1) // 2)
            missing_dominant_count = sum(1 for item in role_usage_by_scene if not str(item.get("dominantRole") or "").strip())
            if missing_dominant_count > 0:
                warnings.append("strict_missing_dominant_role")
            if hero_roles and hero_dominant_count < minimum_hero_dominance:
                warnings.append("strict_hero_not_dominant_majority")
                warnings.append("strict_hero_not_dominant_enough")
            antagonist_is_dominant_any_scene = any(
                str(item.get("dominantRoleType") or "").strip().lower() == "antagonist" for item in role_usage_by_scene
            )
            if antagonist_roles and not antagonist_is_dominant_any_scene:
                warnings.append("strict_antagonist_no_dominant_scene")
            if antagonist_roles and tension_items and not any(str(item.get("dominantRoleType") or "").strip().lower() == "antagonist" for item in tension_items):
                warnings.append("strict_antagonist_not_dominant_in_tension")
            if support_overrides_hero:
                warnings.append("strict_support_overrides_hero")

    return list(dict.fromkeys(warnings))


def _validate_role_distribution(
    scenes: list[dict[str, Any]],
    *,
    role_mode: str,
    role_dominance_mode: str = "off",
    role_type_by_role: dict[str, str] | None = None,
) -> dict[str, Any]:
    role_usage_by_scene = _build_role_usage_by_scene(scenes, role_type_by_role)
    dominant_role_by_scene: dict[str, str] = {}
    dominant_role_type_by_scene: dict[str, str] = {}
    scene_intent_by_scene: list[dict[str, str]] = []
    scene_intent_confidence: list[dict[str, Any]] = []
    scene_intent_diagnostics: list[dict[str, Any]] = []
    explicit_intent_missing = 0
    transition_intent_count = 0
    conflict_intent_count = 0
    for item, scene in zip(role_usage_by_scene, scenes):
        dominance = _estimate_scene_role_dominance(scene, role_type_by_role=role_type_by_role)
        item.update(dominance)
        scene_id = str(item.get("sceneId") or "")
        intent, confidence = _estimate_scene_intent(scene if isinstance(scene, dict) else {}, len(scene_intent_by_scene))
        planner_intent_present = isinstance(scene, dict) and str(scene.get("intent") or "").strip().lower() in SCENE_INTENTS
        if isinstance(scene, dict) and not str(scene.get("intent") or "").strip():
            explicit_intent_missing += 1
            scene["intent"] = intent
        scene_intent_by_scene.append({"sceneId": scene_id, "intent": intent})
        confidence_value = round(confidence, 2)
        confidence_source = "gemini" if planner_intent_present else "estimated"
        matched_keywords = _extract_scene_intent_keywords(scene if isinstance(scene, dict) else {}, intent)
        scene_intent_confidence.append({"sceneId": scene_id, "intent": intent, "confidence": confidence_value, "source": confidence_source, "intentSource": confidence_source})
        scene_intent_diagnostics.append(
            {
                "sceneId": scene_id,
                "intent": intent,
                "confidence": confidence_value,
                "source": confidence_source,
                "intentSource": confidence_source,
                "matchedKeywords": matched_keywords,
                "suggestedRenderModes": [],
            }
        )
        if intent == "transition":
            transition_intent_count += 1
        if intent in {"confrontation", "threat"}:
            conflict_intent_count += 1
        if scene_id:
            dominant_role_by_scene[scene_id] = str(dominance.get("dominantRole") or "")
            dominant_role_type_by_scene[scene_id] = str(dominance.get("dominantRoleType") or "unknown")
    warnings = _build_role_distribution_warnings(
        role_usage_by_scene,
        role_mode=role_mode,
        role_dominance_mode=role_dominance_mode,
        role_type_by_role=role_type_by_role,
    )
    scene_intent_warnings: list[str] = []
    if str(role_dominance_mode or "").strip().lower() == "strict":
        for item in role_usage_by_scene:
            scene_id = str(item.get("sceneId") or "")
            dominant_role_type = str(item.get("dominantRoleType") or "").strip().lower()
            intent = next((str(x.get("intent") or "") for x in scene_intent_by_scene if str(x.get("sceneId") or "") == scene_id), "")
            scene_lookup = next(
                (candidate for candidate in scenes if isinstance(candidate, dict) and str(candidate.get("sceneId") or "") == scene_id),
                {},
            )
            scene_intent_warnings.extend(_validate_scene_intent(scene_lookup if isinstance(scene_lookup, dict) else {}, dominant_role_type, intent))
    if explicit_intent_missing > 0:
        scene_intent_warnings.append("strict_missing_scene_intent")
    weak_intent_count = sum(
        1
        for diag in scene_intent_diagnostics
        if str(diag.get("intent") or "").strip().lower() in {"transition", "setup"} and not diag.get("matchedKeywords")
    )
    if weak_intent_count > 0:
        scene_intent_warnings.append("weak_intent_signal")
    scene_count = len(scene_intent_by_scene)
    if scene_count and transition_intent_count > max(1, scene_count // 2):
        scene_intent_warnings.append("scene_intent_transition_overuse")
    if scene_count >= 3 and conflict_intent_count == 0:
        scene_intent_warnings.append("scene_intent_conflict_missing")
    if _enforce_conflict_scene_if_needed(scenes, scene_intent_by_scene, scene_intent_confidence, scene_intent_diagnostics):
        scene_intent_warnings.append("scene_intent_conflict_injected")
    scene_intent_warnings.extend(_enforce_scene_intent_diversity(scenes, scene_intent_by_scene, scene_intent_confidence, scene_intent_diagnostics))
    if _has_transition_or_setup_overuse(scene_intent_by_scene):
        scene_intent_warnings.append("scene_intent_transition_setup_overuse")
    if any(item in {"scene_intent_transition_overuse", "scene_intent_conflict_missing"} for item in scene_intent_warnings):
        scene_intent_warnings.append("scenes lack clear narrative intent or progression")
    warnings.extend(scene_intent_warnings)
    deduped_warnings = list(dict.fromkeys(warnings))
    return {
        "roleUsageByScene": role_usage_by_scene,
        "dominantRoleByScene": dominant_role_by_scene,
        "dominantRoleTypeByScene": dominant_role_type_by_scene,
        "sceneIntentByScene": scene_intent_by_scene,
        "sceneIntentConfidence": scene_intent_confidence,
        "sceneIntentDiagnostics": scene_intent_diagnostics,
        "sceneIntentWarnings": list(dict.fromkeys(scene_intent_warnings)),
        "roleDominanceWarnings": deduped_warnings,
        "roleValidationWarnings": deduped_warnings,
        "roleValidationStatus": "warning" if deduped_warnings else "ok",
    }


LOCKED_ROLE_REFINEMENT_WARNING_TRIGGERS = {
    "locked_hero_not_in_majority",
    "locked_antagonist_missing_from_tension_scenes",
    "locked_support_unused",
    "strict_missing_dominant_role",
    "strict_hero_not_dominant_majority",
    "strict_antagonist_no_dominant_scene",
    "strict_hero_not_dominant_enough",
    "strict_antagonist_not_dominant_in_tension",
    "strict_support_overrides_hero",
    "strict_missing_scene_intent",
    "strict_intent_role_mismatch",
    "weak_intent_signal",
    "scene_intent_transition_overuse",
    "scene_intent_conflict_missing",
    "scene_intent_conflict_injected",
    "scene_intent_transition_setup_overuse",
    "scene_intent_repetition_reduced",
    "scene_intent_family_gap",
    "scenes lack clear narrative intent or progression",
}


def _shorten_debug_preview(value: Any, limit: int = 280) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _count_storyboard_scenes(storyboard: Any) -> int:
    if isinstance(storyboard, dict):
        scenes = storyboard.get("scenes")
        if isinstance(scenes, list):
            return len(scenes)
    if isinstance(storyboard, list):
        return len(storyboard)
    return 0


def _decide_locked_role_refinement(
    *,
    role_mode: str,
    role_dominance_mode: str = "off",
    role_validation_status: str,
    role_validation_warnings: list[str] | None,
    storyboard: dict[str, Any] | list[dict[str, Any]] | Any,
    role_type_by_role: dict[str, str] | None = None,
) -> tuple[bool, str]:
    normalized_role_mode = str(role_mode or "").strip().lower() or "auto"
    normalized_role_dominance_mode = str(role_dominance_mode or "").strip().lower() or "off"
    warnings = [str(item).strip() for item in (role_validation_warnings or []) if str(item).strip()]
    warning_set = set(warnings)
    scene_count = _count_storyboard_scenes(storyboard)
    role_types = role_type_by_role if isinstance(role_type_by_role, dict) else {}
    normalized_role_types = {
        str(role_type or "").strip().lower()
        for role_type in role_types.values()
        if str(role_type or "").strip()
    }
    locked_roles_present = bool(normalized_role_types & {"hero", "support", "antagonist"})

    if normalized_role_mode != "locked":
        return False, "skipped_auto_mode"
    if scene_count == 0:
        return False, "skipped_empty_storyboard"
    if not locked_roles_present:
        return False, "skipped_no_locked_roles"
    if not warnings:
        return False, "not_needed"
    if normalized_role_dominance_mode == "off" and not any(item.startswith("locked_") for item in warning_set):
        return False, "skipped_role_dominance_off"
    if "antagonist" in normalized_role_types and "locked_antagonist_missing_from_tension_scenes" in warning_set:
        return True, "critical_antagonist_missing"
    if normalized_role_dominance_mode == "strict" and (
        "strict_antagonist_no_dominant_scene" in warning_set or "strict_antagonist_not_dominant_in_tension" in warning_set
    ):
        return True, "strict_antagonist_dominance_missing"
    if normalized_role_dominance_mode == "strict" and (
        "strict_hero_not_dominant_majority" in warning_set or "strict_hero_not_dominant_enough" in warning_set
    ):
        return True, "strict_hero_dominance_low"
    if normalized_role_dominance_mode == "strict" and "strict_missing_dominant_role" in warning_set:
        return True, "strict_missing_scene_dominance"
    if normalized_role_dominance_mode == "strict" and (
        "strict_missing_scene_intent" in warning_set or "strict_intent_role_mismatch" in warning_set
    ):
        return True, "strict_scene_intent_issue"
    if str(role_validation_status or "").strip().lower() == "ok":
        return False, "not_needed"

    trigger_warnings = [item for item in warnings if item in LOCKED_ROLE_REFINEMENT_WARNING_TRIGGERS]
    if not trigger_warnings:
        return False, "skipped_non_trigger_warnings"
    if scene_count < 2:
        return False, "skipped_trivial_storyboard"

    if scene_count < 3 and trigger_warnings == ["locked_support_unused"]:
        return False, "skipped_trivial_storyboard"
    if normalized_role_dominance_mode == "soft":
        return False, "soft_mode_warning_only"

    return True, "locked_warnings_present"


def _should_run_locked_role_refinement(
    role_mode: str,
    role_dominance_mode: str,
    role_validation_status: str,
    role_validation_warnings: list[str],
    storyboard: dict[str, Any] | Any,
) -> bool:
    should_run, _ = _decide_locked_role_refinement(
        role_mode=role_mode,
        role_dominance_mode=role_dominance_mode,
        role_validation_status=role_validation_status,
        role_validation_warnings=role_validation_warnings,
        storyboard=storyboard,
    )
    return should_run


def _build_locked_role_refinement_prompt(
    original_input_payload: dict[str, Any],
    storyboard_v1: dict[str, Any] | list[dict[str, Any]],
    role_type_by_role: dict[str, str] | None,
    role_selection_source_by_role: dict[str, str] | None,
    role_validation_warnings: list[str] | None,
) -> str:
    prompt_payload = {
        "mode": original_input_payload.get("mode"),
        "plannerMode": original_input_payload.get("plannerMode"),
        "genre": original_input_payload.get("genre"),
        "storyMissionSummary": original_input_payload.get("storyMissionSummary"),
        "storyControlMode": original_input_payload.get("storyControlMode"),
        "roleMode": "locked",
        "roleDominanceMode": original_input_payload.get("roleDominanceMode") or "off",
        "roleTypeByRole": role_type_by_role if isinstance(role_type_by_role, dict) else {},
        "roleSelectionSourceByRole": role_selection_source_by_role if isinstance(role_selection_source_by_role, dict) else {},
        "roleValidationWarnings": [str(item).strip() for item in (role_validation_warnings or []) if str(item).strip()],
        "storyboardV1": storyboard_v1,
    }
    return (
        "LOCKED ROLE REPAIR PASS.\n"
        "This is a repair pass, not a fresh generation.\n"
        "Return JSON only. Preserve the existing backend contract exactly.\n"
        "Repair only locked role distribution problems in the current storyboard.\n"
        "Preserve scene count if possible.\n"
        "Preserve scene order, timing, story progression, tone, genre, mood, pacing, and dramatic arc where possible.\n"
        "Preserve good scenes and use minimal structural changes.\n"
        "Do not rewrite everything if only role usage needs fixing.\n"
        "Do not swap role identities.\n"
        "Do not replace hero with antagonist.\n"
        "Hero must remain the main narrative subject.\n"
        "Hero should appear in the majority of scenes unless there is a strong narrative reason not to.\n"
        "If an antagonist exists, include the antagonist in at least one tension, conflict, opposition, pressure, threat, pursuit, or confrontation scene.\n"
        "If support exists, use support meaningfully when present and relevant.\n"
        "Repair role distribution with minimal structural changes.\n"
        "SCENE INTENT RULES:\n"
        "- Ensure each scene has a clear intent (setup, pursuit, confrontation, threat, escape, support, dialogue, reveal, observation, transition).\n"
        "- Avoid neutral scenes with unclear purpose.\n"
        "- Align role behavior with scene intent while preserving current structure.\n"
        "- Improve weak scenes by clarifying intent instead of rewriting the whole storyboard.\n"
        "ROLE BEHAVIOR RULES:\n"
        "Hero:\n"
        "- Must be the primary narrative driver in most scenes\n"
        "- Scenes with hero must revolve around hero actions, decisions, or perspective\n"
        "- Hero presence alone is NOT enough — hero must influence events\n"
        "Antagonist:\n"
        "- Must act as a source of conflict, pressure, threat, control, or opposition\n"
        "- Must actively affect or oppose the hero (directly or indirectly)\n"
        "- Passive or background presence is NOT enough\n"
        "- At least one scene must clearly express conflict, tension, pursuit, threat, or control from antagonist\n"
        "Support:\n"
        "- Must assist, react to, or interact with the hero\n"
        "- Should not exist as background-only presence\n"
        "- Should contribute to progression or emotional context\n"
        "ROLE DOMINANCE CONTROL:\n"
        "- If roleDominanceMode='off': keep current role presence logic and avoid forcing dominance rewrites.\n"
        "- If roleDominanceMode='soft': prefer a dominant role per scene, but keep repairs conservative and minimal.\n"
        "- If roleDominanceMode='strict': ensure each scene has a dominant role, each scene clearly indicates which role drives it (hero/antagonist/support), dominant role actively influences scene outcome (not passive presence), hero dominates most scenes unless explicitly justified by story structure, antagonist dominates at least one scene where conflict/pressure/opposition is clearly expressed, support dominates only assistance/reaction/witness/emotional-support scenes, avoid passive-everyone scenes, avoid hero-present-but-not-central scenes, and avoid decorative antagonist scenes.\n"
        "SCENE QUALITY RULES:\n"
        "- Maintain narrative progression (setup → tension → development → outcome or cliffhanger)\n"
        "- Do NOT just insert characters — integrate them into the action\n"
        "- Do NOT create empty scenes just to satisfy role presence\n"
        "- Prefer minimal changes but ensure meaningful role usage\n"
        "STRICT CONSTRAINTS:\n"
        "- Do NOT change identity of roles (hero must remain hero, etc.)\n"
        "- Do NOT swap hero and antagonist\n"
        "- Do NOT rewrite entire storyboard unless absolutely required\n"
        "- Preserve timing, order, and structure when possible\n"
        "OUTPUT:\n"
        "- Return JSON only\n"
        "- Keep the same schema\n"
        "Original locked-role setup, current storyboard, and warnings:\n"
        f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}"
    )


def _score_role_validation_status(status: str) -> int:
    normalized = str(status or "").strip().lower()
    if normalized == "ok":
        return 0
    if normalized == "warning":
        return 1
    return 2


def _classify_refined_storyboard_improvement(
    *,
    status_before: str,
    warnings_before: list[str] | None,
    status_after: str,
    warnings_after: list[str] | None,
    scenes_before: list[dict[str, Any]] | None = None,
    scenes_after: list[dict[str, Any]] | None = None,
) -> str:
    before = [str(item).strip() for item in (warnings_before or []) if str(item).strip()]
    after = [str(item).strip() for item in (warnings_after or []) if str(item).strip()]
    before_score = _score_role_validation_status(status_before)
    after_score = _score_role_validation_status(status_after)
    normalized_scenes_before = scenes_before or []
    normalized_scenes_after = scenes_after or []

    if not normalized_scenes_after:
        return "rejected"
    if normalized_scenes_before == normalized_scenes_after:
        return "no_change"
    if after_score > before_score:
        return "rejected"
    if len(after) < len(before):
        return "warnings_reduced"
    if len(after) > len(before):
        return "rejected"
    if after_score < before_score:
        return "status_improved"
    return "structure_preserved"


def _is_refined_storyboard_better(
    *,
    status_before: str,
    warnings_before: list[str] | None,
    status_after: str,
    warnings_after: list[str] | None,
    scenes_before: list[dict[str, Any]] | None = None,
    scenes_after: list[dict[str, Any]] | None = None,
) -> bool:
    return _classify_refined_storyboard_improvement(
        status_before=status_before,
        warnings_before=warnings_before,
        status_after=status_after,
        warnings_after=warnings_after,
        scenes_before=scenes_before,
        scenes_after=scenes_after,
    ) in {"warnings_reduced", "status_improved", "structure_preserved"}


def validate_gemini_planner_response(parsed: dict[str, Any], request_input: dict[str, Any]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    scenes = parsed.get("scenes") if isinstance(parsed.get("scenes"), list) else []

    if not str(parsed.get("genre") or request_input.get("genre") or "").strip():
        errors.append("planner_missing_genre")
    scene_count_value = parsed.get("sceneCount")
    scene_count_num = int(_to_float(scene_count_value) or 0)
    if scene_count_num <= 0:
        errors.append("planner_missing_scene_count")
    if not isinstance(parsed.get("scenes"), list):
        errors.append("planner_missing_scenes_array")
    elif not scenes:
        errors.append("planner_empty_scenes")

    for idx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append(f"scene_{idx + 1}_invalid_type")
            continue
        scene_id = str(scene.get("sceneId") or f"scene-{idx + 1}").strip()
        if not str(scene.get("sceneId") or "").strip():
            errors.append(f"{scene_id}:missing_scene_id")
        for field in ["startSec", "endSec", "durationSec"]:
            if _to_float(scene.get(field)) is None:
                errors.append(f"{scene_id}:invalid_{field}")
        tension_value, tension_note = _map_tension_to_number(scene.get("tensionLevel"))
        if tension_value is None:
            warnings.append(f"{scene_id}:tension_level_not_numeric")
        elif tension_note:
            warnings.append(f"{scene_id}:{tension_note}")

        active_roles_raw = scene.get("activeRoles")
        if not isinstance(active_roles_raw, list):
            errors.append(f"{scene_id}:active_roles_missing")
            active_roles_raw = []
        active_roles = [str(item).strip() for item in active_roles_raw if str(item).strip()]
        invalid_roles = [role for role in active_roles if role not in COMFY_REF_ROLES]
        if invalid_roles:
            warnings.append(f"{scene_id}:invalid_active_roles:{','.join(invalid_roles)}")

        focal_role = str(scene.get("focalRole") or "").strip()
        if not focal_role:
            errors.append(f"{scene_id}:missing_focal_role")
        elif focal_role not in COMFY_REF_ROLES:
            errors.append(f"{scene_id}:invalid_focal_role:{focal_role}")

        if not str(scene.get("continuityRule") or "").strip():
            errors.append(f"{scene_id}:missing_continuity_rule")
        if not str(scene.get("visualIdea") or "").strip():
            errors.append(f"{scene_id}:missing_visual_idea")

        prop_function = str(scene.get("propFunction") or "").strip()
        if "props" in active_roles and not prop_function:
            errors.append(f"{scene_id}:missing_prop_function_for_active_props")

        scene_blob = _scene_text_blob(scene)
        if "props" not in active_roles and _text_suggests_prop_presence(scene_blob):
            warnings.append(f"{scene_id}:props_may_be_present_but_inactive")

        if _text_suggests_transformation(scene_blob):
            for role in _extract_character_roles_from_text(scene_blob):
                if role not in active_roles:
                    warnings.append(f"{scene_id}:transformed_role_missing_from_active_roles:{role}")

    return list(dict.fromkeys(warnings)), list(dict.fromkeys(errors))


def normalize_gemini_planner_response(parsed: dict[str, Any], request_input: dict[str, Any]) -> dict[str, Any]:
    normalization_warnings: list[str] = []
    raw_scenes = parsed.get("scenes") if isinstance(parsed.get("scenes"), list) else []
    available_refs = request_input.get("refsByRole") if isinstance(request_input.get("refsByRole"), dict) else {}
    available_roles = {role for role, items in available_refs.items() if isinstance(items, list) and items}
    requested_genre = str(parsed.get("genre") or request_input.get("genre") or "").strip()
    scene_count_hint = request_input.get("sceneCountHint") if isinstance(request_input.get("sceneCountHint"), dict) else {}
    hinted_scene_count = int(_to_float(scene_count_hint.get("value")) or 0) if scene_count_hint else 0
    parsed_scene_count = int(_to_float(parsed.get("sceneCount")) or 0)
    estimated_scene_count = max(parsed_scene_count, hinted_scene_count, len(raw_scenes), 1)
    fallback_duration = _to_float(request_input.get("audioContext", {}).get("durationSec")) if isinstance(request_input.get("audioContext"), dict) else None
    if fallback_duration is not None and estimated_scene_count > 0:
        fallback_duration = fallback_duration / estimated_scene_count
    if fallback_duration is None or fallback_duration <= 0:
        fallback_duration = 4.0
    normalized_scenes: list[dict[str, Any]] = []
    normalization_applied = False

    for idx, raw_scene in enumerate(raw_scenes):
        if not isinstance(raw_scene, dict):
            normalization_applied = True
            normalization_warnings.append(f"scene_{idx + 1}:discarded_non_object_scene")
            continue

        scene_id = str(raw_scene.get("sceneId") or f"scene-{idx + 1}").strip() or f"scene-{idx + 1}"
        active_roles_raw = raw_scene.get("activeRoles") if isinstance(raw_scene.get("activeRoles"), list) else []
        active_roles = list(dict.fromkeys([role for role in [str(item).strip() for item in active_roles_raw] if role in COMFY_REF_ROLES]))
        if len(active_roles) != len(active_roles_raw):
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:filtered_invalid_active_roles")

        focal_role = _normalize_allowed_role(raw_scene.get("focalRole"))
        if not focal_role and active_roles:
            focal_role = active_roles[0]
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:focal_role_fallback_from_active_roles")
        if not focal_role:
            fallback_role = next((role for role in COMFY_FALLBACK_ROLE_PRIORITY if role in active_roles or role in available_roles), "character_1")
            focal_role = fallback_role
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:focal_role_fallback_default")

        scene_blob = _scene_text_blob(raw_scene)
        if _text_suggests_transformation(scene_blob):
            for role in _extract_character_roles_from_text(scene_blob):
                if role not in active_roles:
                    active_roles.append(role)
                    normalization_applied = True
                    normalization_warnings.append(f"{scene_id}:restored_transformed_role:{role}")

        if not active_roles:
            active_roles = [focal_role]
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:active_roles_fallback_from_focal_role")

        tension_level, tension_note = _map_tension_to_number(raw_scene.get("tensionLevel"))
        if tension_level is None:
            tension_level = 5
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:tension_defaulted_to_5")
        elif tension_note:
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:{tension_note}")

        prop_function = str(raw_scene.get("propFunction") or "").strip()
        if "props" not in active_roles and not prop_function:
            prop_function = "not active in this scene"
        elif "props" in active_roles and not prop_function:
            prop_function = "story-driving physical prop action"
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:prop_function_fallback_for_active_props")

        image_prompt = _ensure_genre_pressure(str(raw_scene.get("imagePrompt") or raw_scene.get("visualIdea") or "").strip(), requested_genre, language="en")
        video_prompt = _ensure_genre_pressure(str(raw_scene.get("videoPrompt") or image_prompt or "").strip(), requested_genre, language="en")
        scene_action = str(raw_scene.get("sceneAction") or raw_scene.get("newThreatOrChange") or raw_scene.get("sceneMeaning") or "").strip()
        scene_meaning = _ensure_genre_pressure(str(raw_scene.get("sceneMeaning") or raw_scene.get("newThreatOrChange") or raw_scene.get("visualIdea") or "").strip(), requested_genre, language="en")
        visual_idea = _ensure_genre_pressure(str(raw_scene.get("visualIdea") or raw_scene.get("imagePrompt") or "").strip(), requested_genre, language="en")
        new_threat_or_change = _ensure_genre_pressure(str(raw_scene.get("newThreatOrChange") or scene_action or scene_meaning or "").strip(), requested_genre, language="en")
        continuity_rule = str(raw_scene.get("continuityRule") or "Preserve continuity from prior scene and locked world.").strip()
        start_sec = _to_float(raw_scene.get("startSec"))
        end_sec = _to_float(raw_scene.get("endSec"))
        duration_sec = _to_float(raw_scene.get("durationSec"))

        if start_sec is None:
            if normalized_scenes:
                previous_end_sec = _to_float(normalized_scenes[-1].get("endSec"))
                start_sec = previous_end_sec if previous_end_sec is not None else 0.0
                normalization_warnings.append(f"{scene_id}:start_sec_fallback_from_previous_end")
            else:
                start_sec = 0.0
                normalization_warnings.append(f"{scene_id}:start_sec_fallback_to_zero")
            normalization_applied = True

        if duration_sec is None:
            if start_sec is not None and end_sec is not None:
                duration_sec = end_sec - start_sec
                normalization_warnings.append(f"{scene_id}:duration_sec_derived_from_range")
            else:
                duration_sec = fallback_duration
                normalization_warnings.append(f"{scene_id}:duration_sec_fallback_default")
            normalization_applied = True

        if duration_sec is None or duration_sec <= 0:
            duration_sec = 1.0
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:duration_sec_clamped_minimum")

        if end_sec is None:
            end_sec = start_sec + duration_sec
            normalization_applied = True
            normalization_warnings.append(f"{scene_id}:end_sec_fallback_from_start_plus_duration")

        normalized_scenes.append(
            {
                "sceneId": scene_id,
                "startSec": start_sec,
                "endSec": end_sec,
                "durationSec": duration_sec,
                "sceneMeaning": scene_meaning,
                "visualDescription": visual_idea,
                "visualIdea": visual_idea,
                "newThreatOrChange": new_threat_or_change,
                "continuity": continuity_rule,
                "continuityRule": continuity_rule,
                "imagePrompt": image_prompt,
                "videoPrompt": video_prompt,
                "genre": requested_genre,
                "sceneAction": scene_action,
                "cameraPlan": str(raw_scene.get("cameraPlan") or "").strip(),
                "environmentMotion": str(raw_scene.get("environmentMotion") or "").strip(),
                "activeRefs": active_roles,
                "activeRoles": active_roles,
                "refsUsed": active_roles,
                "primaryRole": focal_role,
                "focalRole": focal_role,
                "focalSubject": focal_role,
                "tensionLevel": tension_level,
                "propFunction": prop_function,
                "spokenText": str(raw_scene.get("spokenText") or "").strip(),
                "transitionType": raw_scene.get("transitionType"),
                "cameraType": raw_scene.get("cameraType"),
                "cameraMovement": raw_scene.get("cameraMovement"),
                "cameraPosition": raw_scene.get("cameraPosition"),
                "visualMode": raw_scene.get("visualMode"),
                "humanAnchorType": raw_scene.get("humanAnchorType"),
            }
        )

    declared_scene_count = int(_to_float(parsed.get("sceneCount")) or 0)
    actual_scene_count = len(normalized_scenes)
    if declared_scene_count != actual_scene_count:
        normalization_applied = True
        normalization_warnings.append(f"scene_count_corrected:{declared_scene_count}->{actual_scene_count}")

    return {
        "genre": requested_genre,
        "sceneCount": actual_scene_count,
        "scenes": normalized_scenes,
        "warnings": list(dict.fromkeys(normalization_warnings)),
        "normalizationApplied": normalization_applied,
    }


def _normalize_scene(scene: dict[str, Any], idx: int, available_refs_by_role: dict[str, list[dict[str, str]]] | None = None) -> dict[str, Any]:
    src = scene if isinstance(scene, dict) else {}
    start_sec = src.get("startSec")
    end_sec = src.get("endSec")
    duration_sec = src.get("durationSec")

    if start_sec is None and isinstance(src.get("timeRange"), dict):
        start_sec = src["timeRange"].get("startSec")
        end_sec = src["timeRange"].get("endSec")
    if start_sec is None:
        start_sec = src.get("start")
    if end_sec is None:
        end_sec = src.get("end")

    try:
        start_n = float(start_sec) if start_sec is not None else None
    except Exception:
        start_n = None
    try:
        end_n = float(end_sec) if end_sec is not None else None
    except Exception:
        end_n = None
    try:
        duration_n = float(duration_sec) if duration_sec is not None else None
    except Exception:
        duration_n = None

    if duration_n is None and start_n is not None and end_n is not None:
        duration_n = max(0.0, end_n - start_n)

    refs_used, ref_directives, primary_role, secondary_roles = _normalize_scene_ref_roles(src, available_refs_by_role)
    available_roles = {
        role for role in COMFY_REF_ROLES if isinstance((available_refs_by_role or {}).get(role), list) and len((available_refs_by_role or {}).get(role) or []) > 0
    }
    hero_entity_id = str(src.get("heroEntityId") or primary_role or "").strip() or primary_role
    support_entity_ids = [
        str(item or "").strip()
        for item in (src.get("supportEntityIds") if isinstance(src.get("supportEntityIds"), list) else secondary_roles)
        if str(item or "").strip() in COMFY_REF_ROLES and str(item or "").strip() != hero_entity_id
    ]
    support_entity_ids = list(dict.fromkeys(support_entity_ids))
    must_appear = [
        str(item or "").strip()
        for item in (src.get("mustAppear") if isinstance(src.get("mustAppear"), list) else [hero_entity_id] + support_entity_ids)
        if str(item or "").strip() in COMFY_REF_ROLES
    ]
    must_appear = list(dict.fromkeys([r for r in must_appear if r in available_roles]))
    must_not_appear = [
        str(item or "").strip()
        for item in (src.get("mustNotAppear") if isinstance(src.get("mustNotAppear"), list) else [])
        if str(item or "").strip() in COMFY_REF_ROLES
    ]
    must_not_appear = list(dict.fromkeys(must_not_appear))

    image_prompt_ru, image_prompt_en = _normalize_prompt_language_fields(
        ru_value=src.get("imagePromptRu"),
        en_value=src.get("imagePromptEn"),
        generic_value=src.get("imagePrompt"),
    )
    video_prompt_ru, video_prompt_en = _normalize_prompt_language_fields(
        ru_value=src.get("videoPromptRu"),
        en_value=src.get("videoPromptEn"),
        generic_value=src.get("videoPrompt"),
    )
    scene_genre = str(src.get("genre") or "").strip()
    image_prompt_ru = _ensure_genre_pressure(image_prompt_ru, scene_genre, language="ru")
    image_prompt_en = _ensure_genre_pressure(image_prompt_en, scene_genre, language="en")
    video_prompt_ru = _ensure_genre_pressure(video_prompt_ru, scene_genre, language="ru")
    video_prompt_en = _ensure_genre_pressure(video_prompt_en, scene_genre, language="en")
    active_refs = src.get("activeRefs") if isinstance(src.get("activeRefs"), list) else refs_used
    active_refs = [str(role).strip() for role in active_refs if str(role).strip() in COMFY_REF_ROLES]
    if not active_refs:
        active_refs = refs_used
    scene_action = str(src.get("sceneAction") or src.get("visualAction") or src.get("sceneNarrativeStep") or "").strip()
    environment_motion = str(src.get("environmentMotion") or src.get("motionPlan") or "").strip()
    camera_plan = str(src.get("cameraPlan") or src.get("cameraIntent") or "").strip()
    transition_type = _normalize_transition_type(src.get("transitionType"), idx)
    camera_type = str(src.get("cameraType") or "").strip() or _infer_camera_type(camera_plan)
    camera_movement = str(src.get("cameraMovement") or src.get("cameraMove") or environment_motion or "").strip()
    camera_position = str(src.get("cameraPosition") or src.get("cameraPlacement") or "").strip()
    visual_mode = str(src.get("visualMode") or GEMINI_ONLY_VISUAL_MODE_DEFAULT).strip() or GEMINI_ONLY_VISUAL_MODE_DEFAULT
    ref_usage_reason = str(src.get("refUsageReason") or src.get("roleSelectionReason") or "").strip()
    continuity_locks_used = src.get("continuityLocksUsed") if isinstance(src.get("continuityLocksUsed"), list) else []
    continuity_locks_used = [str(item).strip() for item in continuity_locks_used if str(item).strip()]
    if not scene_action and not environment_motion:
        scene_action = "character slightly shifts position, breathes, interacts subtly with environment"
    role_logic_action = scene_action or environment_motion or "supports the scene beat"
    character_role_logic = src.get("characterRoleLogic") if isinstance(src.get("characterRoleLogic"), list) else []
    if not character_role_logic:
        character_role_logic = [
            {
                "refId": role,
                "roleInScene": "actor" if role == primary_role else "background",
                "action": role_logic_action,
                "reason": ref_usage_reason or "selected because this entity is visually relevant to the scene meaning",
            }
            for role in [primary_role, *secondary_roles]
            if role in COMFY_REF_ROLES
        ]
    else:
        normalized_role_logic: list[dict[str, Any]] = []
        for item in character_role_logic:
            if not isinstance(item, dict):
                continue
            ref_id = str(item.get("refId") or item.get("role") or "").strip()
            if ref_id not in COMFY_REF_ROLES:
                continue
            role_in_scene = str(item.get("roleInScene") or "").strip().lower()
            if role_in_scene not in {"observer", "actor", "background"}:
                role_in_scene = "actor" if ref_id == primary_role else "background"
            normalized_role_logic.append(
                {
                    "refId": ref_id,
                    "roleInScene": role_in_scene,
                    "action": str(item.get("action") or role_logic_action).strip(),
                    "reason": str(item.get("reason") or ref_usage_reason or "selected because this entity is relevant to the scene meaning").strip(),
                }
            )
        character_role_logic = normalized_role_logic

    dynamic_score = 0
    dynamic_score += 2 if scene_action else 0
    dynamic_score += 2 if (environment_motion or str(src.get("motionPlan") or "").strip()) else 0
    dynamic_score += 1 if camera_plan else 0
    dynamic_score += 1 if str(src.get("visualDescription") or src.get("visualClue") or "").strip() else 0
    dynamic_score += 1 if transition_type in {"enter_transition", "justified_cut", "perspective_shift", "match_cut"} else 0
    dynamic_score += 1 if len(active_refs) >= 2 or len(support_entity_ids) >= 1 else 0
    dynamic_score += 1 if str(src.get("sceneMeaning") or "").strip() else 0
    weak_scene = dynamic_score < 3
    hallucination_text = " ".join([image_prompt_en, video_prompt_en, str(src.get("visualDescription") or "")]).lower()
    object_hallucination_risk = "high" if ("props" not in refs_used and any(token in hallucination_text for token in ["giant", "massive", "oversized", "huge machine", "device", "artifact", "monolith", "foreground object"])) else "low"
    human_anchor_type = _normalize_human_anchor_type(src.get("humanAnchorType"), active_refs, src)
    continuity_parts = [str(src.get("continuity") or "").strip()]
    if scene_action or environment_motion:
        continuity_parts.append("scene contains active motion")
    if transition_type == "continuation":
        continuity_parts.append("prefer continued camera movement from previous scene")
    elif transition_type == "enter_transition":
        continuity_parts.append("camera physically enters the next space")
    elif transition_type in {"justified_cut", "perspective_shift", "match_cut"}:
        continuity_parts.append(f"{transition_type} must be narratively justified")
    continuity_text = "; ".join([part for part in continuity_parts if part]).strip("; ")
    requires_dual_character_interaction = "character_1" in must_appear and "character_2" in must_appear
    if requires_dual_character_interaction:
        scene_text_value = _enforce_two_character_interaction_text(str(src.get("sceneText") or ""), is_ru=False)
        scene_goal_value = _enforce_two_character_interaction_text(str(src.get("sceneGoal") or ""), is_ru=False)
        scene_meaning_value = _ensure_genre_pressure(
            _enforce_two_character_interaction_text(str(src.get("sceneMeaning") or ""), is_ru=False),
            scene_genre,
            language="en",
        )
        visual_description_value = _ensure_genre_pressure(
            _enforce_two_character_interaction_text(str(src.get("visualDescription") or ""), is_ru=False),
            scene_genre,
            language="en",
        )
    else:
        scene_text_value = str(src.get("sceneText") or "")
        scene_goal_value = str(src.get("sceneGoal") or "")
        scene_meaning_value = _ensure_genre_pressure(str(src.get("sceneMeaning") or ""), scene_genre, language="en")
        visual_description_value = _ensure_genre_pressure(str(src.get("visualDescription") or ""), scene_genre, language="en")

    if _looks_like_prompt_copy(image_prompt_en, video_prompt_en) or _is_temporal_video_prompt_weak(video_prompt_en):
        video_prompt_en = _build_temporal_video_prompt(
            base_prompt=video_prompt_en,
            image_prompt=image_prompt_en,
            camera_plan=camera_plan,
            scene_action=scene_action,
            continuity=continuity_text,
            requires_dual_character_interaction=requires_dual_character_interaction,
            is_ru=False,
        )
    if _looks_like_prompt_copy(image_prompt_ru, video_prompt_ru) or _is_temporal_video_prompt_weak(video_prompt_ru):
        video_prompt_ru = _build_temporal_video_prompt(
            base_prompt=video_prompt_ru,
            image_prompt=image_prompt_ru,
            camera_plan=camera_plan,
            scene_action=scene_action,
            continuity=continuity_text,
            requires_dual_character_interaction=requires_dual_character_interaction,
            is_ru=True,
        )

    image_missing_langs: list[str] = []
    video_missing_langs: list[str] = []

    if image_prompt_ru and image_prompt_en:
        image_sync_status = PROMPT_SYNC_STATUS_SYNCED
    elif image_prompt_ru or image_prompt_en:
        image_sync_status = PROMPT_SYNC_STATUS_NEEDS_SYNC
        if not image_prompt_ru:
            image_missing_langs.append("ru")
        if not image_prompt_en:
            image_missing_langs.append("en")
    else:
        image_sync_status = PROMPT_SYNC_STATUS_NEEDS_SYNC
        image_missing_langs.extend(["ru", "en"])

    if video_prompt_ru and video_prompt_en:
        video_sync_status = PROMPT_SYNC_STATUS_SYNCED
    elif video_prompt_ru or video_prompt_en:
        video_sync_status = PROMPT_SYNC_STATUS_NEEDS_SYNC
        if not video_prompt_ru:
            video_missing_langs.append("ru")
        if not video_prompt_en:
            video_missing_langs.append("en")
    else:
        video_sync_status = PROMPT_SYNC_STATUS_NEEDS_SYNC
        video_missing_langs.extend(["ru", "en"])

    prompt_language_status = {
        "image": "ru_en_present" if image_prompt_ru and image_prompt_en else ("ru_missing_en_fallback" if image_prompt_en else ("en_missing_ru_only" if image_prompt_ru else "missing_both")),
        "video": "ru_en_present" if video_prompt_ru and video_prompt_en else ("ru_missing_en_fallback" if video_prompt_en else ("en_missing_ru_only" if video_prompt_ru else "missing_both")),
    }
    image_prompt_editor_value = str(src.get("imagePromptEditorValue") or image_prompt_ru or image_prompt_en or "").strip()
    video_prompt_editor_value = str(src.get("videoPromptEditorValue") or video_prompt_ru or video_prompt_en or "").strip()
    image_prompt_editor_lang = str(src.get("imagePromptEditorLang") or ("ru" if image_prompt_ru else ("en_fallback" if image_prompt_en else "missing"))).strip()
    video_prompt_editor_lang = str(src.get("videoPromptEditorLang") or ("ru" if video_prompt_ru else ("en_fallback" if video_prompt_en else "missing"))).strip()

    return {
        "sceneId": str(src.get("sceneId") or f"scene-{idx + 1}"),
        "title": str(src.get("title") or f"Scene {idx + 1}"),
        "startSec": start_n,
        "endSec": end_n,
        "durationSec": duration_n,
        "sceneText": scene_text_value,
        "sceneMeaning": scene_meaning_value,
        "visualDescription": visual_description_value,
        "cameraPlan": camera_plan,
        "cameraType": camera_type,
        "cameraMovement": camera_movement,
        "cameraPosition": camera_position,
        "motionPlan": str(src.get("motionPlan") or ""),
        "sfxPlan": str(src.get("sfxPlan") or ""),
        "sceneAction": scene_action,
        "focalSubject": str(src.get("focalSubject") or src.get("primarySubject") or primary_role or "").strip(),
        "visualClue": str(src.get("visualClue") or src.get("visualEvidence") or src.get("visualDescription") or "").strip(),
        "cameraIntent": str(src.get("cameraIntent") or camera_plan or "").strip(),
        "transitionType": transition_type,
        "visualMode": visual_mode,
        "humanAnchorType": human_anchor_type,
        "forbiddenInsertions": [str(item).strip() for item in (src.get("forbiddenInsertions") if isinstance(src.get("forbiddenInsertions"), list) else []) if str(item).strip()],
        "environmentMotion": environment_motion,
        "sfxSuggestion": str(src.get("sfxSuggestion") or src.get("sfxPlan") or "").strip(),
        "sceneNarrativeStep": str(src.get("sceneNarrativeStep") or ""),
        "sceneGoal": scene_goal_value,
        "storyMission": str(src.get("storyMission") or ""),
        "sceneOutputRule": str(src.get("sceneOutputRule") or "scene image first"),
        "primaryRole": primary_role,
        "secondaryRoles": secondary_roles,
        "continuity": continuity_text,
        "continuityLocksUsed": continuity_locks_used,
        "imagePrompt": image_prompt_en,
        "videoPrompt": video_prompt_en,
        "imagePromptRu": image_prompt_ru,
        "imagePromptEn": image_prompt_en,
        "videoPromptRu": video_prompt_ru,
        "videoPromptEn": video_prompt_en,
        "imagePromptSyncStatus": image_sync_status,
        "videoPromptSyncStatus": video_sync_status,
        "promptMissingLangs": {
            "image": image_missing_langs,
            "video": video_missing_langs,
        },
        "promptLanguageStatus": prompt_language_status,
        "ruPromptMissing": {"image": not bool(image_prompt_ru), "video": not bool(video_prompt_ru)},
        "enPromptPresent": {"image": bool(image_prompt_en), "video": bool(video_prompt_en)},
        "imagePromptEditorValue": image_prompt_editor_value,
        "videoPromptEditorValue": video_prompt_editor_value,
        "imagePromptEditorLang": image_prompt_editor_lang,
        "videoPromptEditorLang": video_prompt_editor_lang,
        "refsUsed": refs_used,
        "activeRefs": active_refs,
        "refUsageReason": ref_usage_reason,
        "characterRoleLogic": character_role_logic,
        "sceneDynamicScore": dynamic_score,
        "weakScene": weak_scene,
        "objectHallucinationRisk": object_hallucination_risk,
        "refDirectives": ref_directives,
        "heroEntityId": hero_entity_id,
        "supportEntityIds": support_entity_ids,
        "mustAppear": must_appear,
        "mustNotAppear": must_not_appear,
        "environmentLock": bool(src.get("environmentLock", "location" in must_appear or ref_directives.get("location") == "environment_required")),
        "styleLock": bool(src.get("styleLock", "style" in refs_used or ref_directives.get("style") in {"required", "optional"})),
        "identityLock": bool(src.get("identityLock", any(role in refs_used for role in ["character_1", "character_2", "character_3", "group", "animal", "props"]))),
        "spokenText": str(src.get("spokenText") or ""),
        "confidence": _to_float(src.get("confidence")) or 0.0,
        "roleSelectionReason": str(src.get("roleSelectionReason") or "").strip(),
        # Runtime render-state fields are intentionally initialized outside planner contract.
        "imageUrl": "",
        "videoUrl": "",
    }


def _normalize_gemini_scenes(
    scenes: list[dict[str, Any]],
    available_refs_by_role: dict[str, list[dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    return [_normalize_scene(scene, idx, available_refs_by_role) for idx, scene in enumerate(scenes)]


def _build_director_debug(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    if not scenes:
        return {
            "cameraContinuityScore": 0.0,
            "transitionTypesByScene": {},
            "humanAnchorCoverage": 0.0,
            "scenesWithHumanAnchor": [],
            "visualModesByScene": {},
            "cameraTypesByScene": {},
            "continuationChainCount": 0,
            "randomCutRisk": "unknown",
        }

    transition_types_by_scene = {str(scene.get("sceneId") or f"scene-{idx + 1}"): str(scene.get("transitionType") or "") for idx, scene in enumerate(scenes)}
    visual_modes_by_scene = {str(scene.get("sceneId") or f"scene-{idx + 1}"): str(scene.get("visualMode") or "") for idx, scene in enumerate(scenes)}
    camera_types_by_scene = {str(scene.get("sceneId") or f"scene-{idx + 1}"): str(scene.get("cameraType") or "") for idx, scene in enumerate(scenes)}
    scenes_with_human_anchor = [
        str(scene.get("sceneId") or f"scene-{idx + 1}")
        for idx, scene in enumerate(scenes)
        if str(scene.get("humanAnchorType") or "none") != "none"
    ]
    human_anchor_coverage = len(scenes_with_human_anchor) / max(1, len(scenes))

    continuation_chain_count = 0
    continuity_points = 0
    possible_points = max(0, len(scenes) - 1)
    for idx in range(1, len(scenes)):
        prev = scenes[idx - 1]
        cur = scenes[idx]
        transition_type = str(cur.get("transitionType") or "")
        prev_camera = str(prev.get("cameraType") or "")
        cur_camera = str(cur.get("cameraType") or "")
        if transition_type in {"continuation", "enter_transition"}:
            continuation_chain_count += 1
            continuity_points += 1
            if prev_camera and cur_camera and prev_camera == cur_camera:
                continuity_points += 1
        elif transition_type == "justified_cut":
            continuity_points += 0.5
        elif transition_type == "match_cut":
            continuity_points += 0.75
        elif transition_type == "perspective_shift":
            continuity_points += 0.5

    max_points = max(1.0, possible_points * 2.0)
    camera_continuity_score = round((continuity_points / max_points) * 100.0, 1)
    unjustified_cut_like_count = sum(1 for idx, scene in enumerate(scenes) if idx > 0 and str(scene.get("transitionType") or "") == "justified_cut")
    random_cut_risk = "low"
    if unjustified_cut_like_count >= max(2, len(scenes) // 3):
        random_cut_risk = "medium"
    if unjustified_cut_like_count >= max(3, len(scenes) // 2):
        random_cut_risk = "high"

    return {
        "cameraContinuityScore": camera_continuity_score,
        "transitionTypesByScene": transition_types_by_scene,
        "humanAnchorCoverage": round(human_anchor_coverage, 3),
        "scenesWithHumanAnchor": scenes_with_human_anchor,
        "visualModesByScene": visual_modes_by_scene,
        "cameraTypesByScene": camera_types_by_scene,
        "continuationChainCount": continuation_chain_count,
        "randomCutRisk": random_cut_risk,
    }


def _looks_like_prompt_copy(image_prompt: str, video_prompt: str) -> bool:
    image_norm = " ".join(str(image_prompt or "").lower().split())
    video_norm = " ".join(str(video_prompt or "").lower().split())
    if not image_norm or not video_norm:
        return True
    if image_norm == video_norm:
        return True
    if image_norm in video_norm or video_norm in image_norm:
        return True
    image_tokens = set(image_norm.split(" "))
    video_tokens = set(video_norm.split(" "))
    if not image_tokens or not video_tokens:
        return True
    overlap = len(image_tokens.intersection(video_tokens)) / max(len(image_tokens), len(video_tokens))
    return overlap >= 0.88


def _is_temporal_video_prompt_weak(video_prompt: str) -> bool:
    text = " ".join(str(video_prompt or "").lower().split())
    if not text:
        return True
    time_markers = ["beginning", "middle", "end", "start", "then", "finally", "начало", "середин", "конец", "сначала", "потом", "затем"]
    camera_markers = ["camera", "dolly", "pan", "track", "наезд", "камера", "панорам", "проезд"]
    action_markers = ["react", "reaction", "gesture", "micro", "движ", "жест", "реакц"]
    has_timeline = any(marker in text for marker in time_markers)
    has_camera = any(marker in text for marker in camera_markers)
    has_actions = any(marker in text for marker in action_markers)
    return not (has_timeline and has_camera and has_actions)


def _build_temporal_video_prompt(
    *,
    base_prompt: str,
    image_prompt: str,
    camera_plan: str,
    scene_action: str,
    continuity: str,
    requires_dual_character_interaction: bool,
    is_ru: bool,
) -> str:
    if is_ru:
        timeline_line = "Видео-промпт обязан описывать развитие во времени: начало → середина → конец."
        motion_line = f"Микродвижения: {scene_action or 'взгляды, дыхание, жесты, перенос веса, реакция партнёра'}."
        camera_line = f"Движение камеры по времени: {camera_plan or 'плавный dolly/панорама с удержанием субъектов в фокусе'}."
        continuity_line = f"Непрерывность с предыдущим моментом: {continuity or 'сохраняй ту же локацию, свет и пространственную геометрию'}."
        reaction_line = "Обязательно покажи реакцию второго персонажа в средней или финальной фазе." if requires_dual_character_interaction else ""
    else:
        timeline_line = "Video prompt must describe motion over time: beginning -> middle -> end."
        motion_line = f"Micro-actions over time: {scene_action or 'eyes shift, breathing changes, hand and posture reactions'}."
        camera_line = f"Camera progression over time: {camera_plan or 'slow dolly/pan while keeping both subjects readable'}."
        continuity_line = f"Continuity from previous moment: {continuity or 'preserve location layout, light logic and spatial continuity'}."
        reaction_line = "Include the second character reaction during the middle or end beat." if requires_dual_character_interaction else ""
    return " ".join([part for part in [base_prompt or image_prompt, timeline_line, motion_line, camera_line, reaction_line, continuity_line] if part]).strip()


def _enforce_two_character_interaction_text(value: str, *, is_ru: bool) -> str:
    base = str(value or "").strip()
    normalized = base.lower()
    has_char_1 = "character_1" in normalized
    has_char_2 = "character_2" in normalized
    if has_char_1 and has_char_2:
        return base
    suffix = (
        "В кадре одновременно character_1 и character_2; они явно взаимодействуют действием и реакцией друг на друга."
        if is_ru
        else "character_1 and character_2 are in the same frame and explicitly interact through action and reaction."
    )
    return f"{base} {suffix}".strip()


def _build_segmentation_debug(scenes: list[dict[str, Any]], audio_story_mode: str, timing_debug: dict[str, Any]) -> dict[str, Any]:
    durations = [max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes]
    avg_duration = (sum(durations) / len(durations)) if durations else 0.0
    max_duration = max(durations) if durations else 0.0
    min_duration = min(durations) if durations else 0.0
    short_count = sum(1 for d in durations if 0.0 < d < 2.0)
    long_count = sum(1 for d in durations if d > 8.0)

    suspicious_even_chunks = False
    if len(durations) >= 3 and avg_duration > 0:
        max_delta = max(abs(d - avg_duration) for d in durations)
        suspicious_even_chunks = max_delta <= 0.35

    mode_reason_map = {
        "lyrics_music": "semantic_and_vocal_phrases_with_music_transitions",
        "music_only": "music_phrase_energy_and_structure_transitions",
        "music_plus_text": "text_meaning_chunks_synced_to_music_transitions",
        "speech_narrative": "spoken_pauses_sentence_endings_topic_shifts_and_semantic_beats",
    }
    mode_reason = mode_reason_map.get(audio_story_mode, "music_driven_transitions")

    return {
        "averageSceneDurationSec": _round_sec(avg_duration),
        "maxSceneDurationSec": _round_sec(max_duration),
        "minSceneDurationSec": _round_sec(min_duration),
        "shortSceneCountUnder2Sec": short_count,
        "longSceneCountOver8Sec": long_count,
        "normalizationApplied": bool(timing_debug.get("normalizationApplied")),
        "normalizationReason": timing_debug.get("normalizationReason"),
        "segmentationMode": "phrase_transition_oriented",
        "segmentationReason": mode_reason,
        "suspiciousEqualChunking": suspicious_even_chunks,
    }


def _needs_segmentation_refinement(segmentation_debug: dict[str, Any], audio_duration_sec: float | None, scene_count: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    avg_duration = _to_float(segmentation_debug.get("averageSceneDurationSec")) or 0.0
    duration = _to_float(audio_duration_sec)

    if bool(segmentation_debug.get("suspiciousEqualChunking")):
        reasons.append("suspicious_equal_chunking")
    if int(segmentation_debug.get("longSceneCountOver8Sec") or 0) > 0:
        reasons.append("has_scene_over_8_sec")
    if avg_duration > 7.0:
        reasons.append("average_scene_too_long")
    if duration is not None and 25.0 <= duration <= 35.0 and scene_count < 4:
        reasons.append("too_few_scenes_for_25_35_sec_track")

    # Mechanical coarse blocks often look both long and near-uniform.
    if avg_duration >= 6.0 and bool(segmentation_debug.get("suspiciousEqualChunking")):
        reasons.append("large_uniform_blocks_detected")

    return (len(reasons) > 0), reasons


def _build_preview_from_scenes(scenes: list[dict[str, Any]], world_lock: dict[str, Any]) -> dict[str, Any]:
    if not scenes:
        return {
            "sourceSceneId": "",
            "previewType": "none",
            "activeRefs": [],
            "imagePrompt": "",
            "previewScore": 0,
            "continuityNotes": str(world_lock.get("atmosphere") or ""),
        }

    def _preview_score(scene: dict[str, Any]) -> int:
        score = 0
        if str(scene.get("sceneAction") or "").strip():
            score += 2
        strong_visual_focus = bool(scene.get("primaryRole")) or "location" in (scene.get("activeRefs") or []) or "props" in (scene.get("activeRefs") or [])
        if strong_visual_focus:
            score += 2
        if str(scene.get("sceneMeaning") or scene.get("visualDescription") or scene.get("imagePromptEn") or scene.get("imagePrompt") or "").strip():
            score += 2
        if any(role in (scene.get("activeRefs") or []) for role in ["character_1", "character_2", "character_3", "group", "animal"]):
            score += 1
        if "contrast" in str(scene.get("imagePromptEn") or scene.get("imagePrompt") or "").lower() or "light" in str(scene.get("continuity") or "").lower():
            score += 1
        return score

    scored_scenes = [(scene, _preview_score(scene)) for scene in scenes]
    best_scene, best_score = max(
        scored_scenes,
        key=lambda item: (
            item[1],
            _to_float(item[0].get("sceneDynamicScore")) or 0.0,
            _to_float(item[0].get("confidence")) or 0.0,
            _to_float(item[0].get("durationSec")) or 0.0,
        ),
    )
    preview_type = "environment_scene"
    if str(best_scene.get("sceneAction") or "").strip():
        preview_type = "action_scene"
    elif any(role in (best_scene.get("activeRefs") or []) for role in ["character_1", "character_2", "character_3", "group", "animal"]):
        preview_type = "hero_scene"
    return {
        "sourceSceneId": str(best_scene.get("sceneId") or ""),
        "previewType": preview_type,
        "activeRefs": list(best_scene.get("activeRefs") or []),
        "imagePrompt": str(best_scene.get("imagePromptEn") or best_scene.get("imagePrompt") or ""),
        "previewScore": best_score,
        "worldLock": world_lock,
        "entityLocksUsed": list(best_scene.get("activeRefs") or []),
        "continuityNotes": str(best_scene.get("continuity") or world_lock.get("atmosphere") or ""),
    }


def _run_comfy_plan_gemini_only(normalized: dict[str, Any]) -> dict[str, Any]:
    story_context = _derive_gemini_only_story_context(normalized)
    story_source, narrative_source = _normalize_story_sources(
        story_context.get("storySource") or normalized.get("storySource"),
        story_context.get("narrativeSource") or normalized.get("narrativeSource"),
    )
    story_context = {
        **story_context,
        "storySource": story_source,
        "narrativeSource": narrative_source,
    }
    normalized = {
        **normalized,
        "storySource": story_source,
        "narrativeSource": narrative_source,
        "timelineSource": story_context.get("timelineSource") or normalized.get("timelineSource"),
        "storyMissionSummary": story_context.get("storyMissionSummary") or normalized.get("storyMissionSummary"),
    }
    project_input = build_project_planning_input(normalized)
    reference_profiles = build_reference_profiles(normalized.get("refsByRole") or {})
    world_lock = _build_world_lock(normalized, reference_profiles)
    entity_locks = _build_entity_locks(normalized, reference_profiles)
    optional_audio_cues = _build_optional_audio_cues(normalized)
    planner_input = build_audio_first_gemini_planner_input(
        normalized,
        project_input,
        story_context=story_context,
        world_lock=world_lock,
        entity_locks=entity_locks,
        optional_audio_cues=optional_audio_cues,
    )
    planner_system_rules = build_audio_first_gemini_planner_system_rules(planner_input)
    planner_output_contract = build_audio_first_gemini_planner_output_contract(planner_input)
    planner_runtime_payload = build_audio_first_gemini_planner_runtime_payload(planner_input)
    planner_system_instruction = (
        "=== GEMINI PLANNER SYSTEM RULES ===\n"
        f"{planner_system_rules}\n\n"
        "=== GEMINI PLANNER OUTPUT CONTRACT ===\n"
        f"{planner_output_contract}"
    )
    multimodal_parts, media_debug = _build_gemini_only_multimodal_parts(normalized, planner_runtime_payload)

    def _build_contract_failure_result(
        errors: list[str],
        warnings: list[str],
        sanitized_error: str,
        *,
        http_status: int | None = None,
        raw_parsed: dict[str, Any] | None = None,
        raw_debug_summary: str | None = None,
        parse_result=None,
    ) -> dict[str, Any]:
        validation_report = GeminiPlannerValidationReport(
            valid=False,
            blocked=project_input.input_mode.value == "text_to_audio_first" and not bool(project_input.master_audio_url),
            errors=list(dict.fromkeys([str(item).strip() for item in errors if str(item).strip()])),
            warnings=list(dict.fromkeys([str(item).strip() for item in warnings if str(item).strip()])),
        )
        contract_result = map_gemini_plan_to_canonical_audio_first_output(
            planner_input,
            None,
            validation_report,
            raw_payload=raw_parsed or {},
            raw_debug_summary=raw_debug_summary,
            parse_result=parse_result,
        )
        canonical_dump = contract_result.canonical_output.model_dump(mode="json")
        return {
            "ok": False,
            "planMeta": {
                "mode": normalized.get("mode"),
                "plannerMode": "gemini_only",
                "output": normalized.get("output"),
                "stylePreset": normalized.get("stylePreset"),
                "audioStoryMode": normalized.get("audioStoryMode"),
                "roleTypeByRole": normalized.get("roleTypeByRole") if isinstance(normalized.get("roleTypeByRole"), dict) else {},
                "roleSelectionSourceByRole": normalized.get("roleSelectionSourceByRole") if isinstance(normalized.get("roleSelectionSourceByRole"), dict) else {},
                "roleMode": normalized.get("roleMode") or "auto",
                "roleModeReason": normalized.get("roleModeReason") or "",
                "roleDominanceMode": normalized.get("roleDominanceMode") or "off",
                "roleDominanceModeReason": normalized.get("roleDominanceModeReason") or "",
                "roleDominanceApplied": bool(normalized.get("roleDominanceApplied")),
                "audioStoryModeRequested": normalized.get("audioStoryModeRequested"),
                "audioStoryModeGuardReason": normalized.get("audioStoryModeGuardReason"),
                "storyControlMode": normalized.get("storyControlMode"),
                "storyMissionSummary": normalized.get("storyMissionSummary"),
                "timelineSource": normalized.get("timelineSource") or "gemini_contract_failure",
                "narrativeSource": narrative_source,
                "storySource": story_source,
                "plannerSource": "gemini",
                "canonicalSourceOfTruth": True,
                "returnsCompatibilityProjection": True,
                "compatibilityProjection": True,
                "canonicalIsProjection": False,
                "worldLock": world_lock,
                "entityLocks": entity_locks,
                "plannerInput": planner_input.model_dump(mode="json", exclude_none=True),
                "dominantRoleByScene": {},
                "dominantRoleTypeByScene": {},
                "sceneIntentByScene": [],
                "sceneIntentConfidence": [],
                "sceneIntentWarnings": [],
                "sceneIntentDiagnostics": [],
                "roleDominanceWarnings": [],
                "roleValidationWarnings": [],
                "roleValidationStatus": "ok",
            },
            "globalContinuity": world_lock,
            "scenes": [],
            "warnings": validation_report.warnings,
            "errors": validation_report.errors,
            "canonicalPlanning": canonical_dump,
            "debug": {
                "plannerMode": "gemini_only",
                "planner_source": "gemini",
                "plannerSource": "gemini",
                "storySource": story_context.get("storySource"),
                "narrativeSource": story_context.get("narrativeSource"),
                "weakSemanticContext": story_context.get("weakSemanticContext"),
                "semanticContextReason": story_context.get("semanticContextReason"),
                "audioStoryModeRequested": story_context.get("audioStoryModeRequested"),
                "audioStoryModeGuardReason": story_context.get("audioStoryModeGuardReason"),
                "hasAudio": story_context.get("hasAudio"),
                "hasText": story_context.get("hasText"),
                "hasRefs": story_context.get("hasRefs"),
                "plannerContractName": planner_input.planner_contract_name,
                "planner_contract_name": planner_input.planner_contract_name,
                "contractVersion": planner_input.contract_version,
                "contract_version": planner_input.contract_version,
                "schemaVersion": planner_input.schema_version,
                "schema_version": planner_input.schema_version,
                "parseMode": parse_result.parse_mode if parse_result else "invalid",
                "parse_mode": parse_result.parse_mode if parse_result else "invalid",
                "contractSchemaValid": parse_result.contract_schema_valid if parse_result else False,
                "contract_schema_valid": parse_result.contract_schema_valid if parse_result else False,
                "canonicalSourceOfTruth": True,
                "returnsCompatibilityProjection": True,
                "canonicalIsProjection": False,
                "plannerInput": planner_input.model_dump(mode="json", exclude_none=True),
                "worldLock": world_lock,
                "entityLocks": entity_locks,
                "roleMode": normalized.get("roleMode") or "auto",
                "roleModeReason": normalized.get("roleModeReason") or "",
                "roleDominanceMode": normalized.get("roleDominanceMode") or "off",
                "roleDominanceModeReason": normalized.get("roleDominanceModeReason") or "",
                "roleDominanceApplied": bool(normalized.get("roleDominanceApplied")),
                "roleTypeByRole": normalized.get("roleTypeByRole") if isinstance(normalized.get("roleTypeByRole"), dict) else {},
                "roleSelectionSourceByRole": normalized.get("roleSelectionSourceByRole") if isinstance(normalized.get("roleSelectionSourceByRole"), dict) else {},
                "dominantRoleByScene": {},
                "dominantRoleTypeByScene": {},
                "sceneIntentByScene": [],
                "sceneIntentConfidence": [],
                "sceneIntentWarnings": [],
                "sceneIntentDiagnostics": [],
                "roleDominanceWarnings": [],
                "roleValidationWarnings": [],
                "roleValidationStatus": "ok",
                "httpStatus": http_status,
                "sanitizedError": sanitized_error,
                "failureSummary": sanitized_error,
                "contractParseMode": parse_result.parse_mode if parse_result else "invalid",
                "contractValidationErrors": parse_result.contract_validation_errors if parse_result else validation_report.errors,
                "contractValidationWarnings": parse_result.contract_validation_warnings if parse_result else validation_report.warnings,
                "rawGeminiContractVersion": parse_result.raw_gemini_contract_version if parse_result else None,
                "rawGeminiSchemaVersion": parse_result.raw_gemini_schema_version if parse_result else None,
                "parserDebugSummary": parse_result.parser_debug_summary if parse_result else "invalid:failure_before_parse",
                "parserNotes": parse_result.parser_notes if parse_result else [],
                "plannerValidationErrors": validation_report.errors,
                "plannerValidationWarnings": validation_report.warnings,
                "validation_errors": validation_report.errors,
                "validation_warnings": validation_report.warnings,
                "rawGeminiPayload": raw_parsed or {},
                "rawGeminiDebugSummary": raw_debug_summary,
                "plannerRequestLayers": {
                    "systemInstruction": planner_system_instruction,
                    "runtimePayload": planner_runtime_payload,
                },
                **media_debug,
            },
        }

    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        missing_key_error = "gemini_api_key_missing"
        return _build_contract_failure_result([missing_key_error, *story_context.get("errors", [])], story_context.get("warnings") or [], _humanize_storyboard_error(missing_key_error))

    if story_context.get("errors"):
        primary_story_error = str((story_context.get("errors") or [""])[0] or "").strip()
        return _build_contract_failure_result(story_context.get("errors") or [], story_context.get("warnings") or [], _humanize_storyboard_error(primary_story_error))

    requested_model = (settings.GEMINI_TEXT_MODEL or PRIMARY_GEMINI_PLANNER_MODEL or FALLBACK_GEMINI_MODEL).strip()
    body = {
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
        "systemInstruction": {"parts": [{"text": planner_system_instruction}]},
        "contents": [{"role": "user", "parts": multimodal_parts}],
    }
    parsed, diagnostics = _call_gemini_plan_with_model_fallback(api_key, requested_model, body)
    system_instruction_used = True
    if _supports_system_instruction_error(diagnostics):
        fallback_body = {
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{planner_system_instruction}\n\n=== GEMINI PLANNER RUNTIME PAYLOAD ===\n{planner_runtime_payload}"}, *multimodal_parts[1:]],
                }
            ],
        }
        parsed, diagnostics = _call_gemini_plan_with_model_fallback(api_key, requested_model, fallback_body)
        system_instruction_used = False

    warnings = [
        *[str(item) for item in (parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []) if str(item).strip()],
        *[str(item) for item in (story_context.get("warnings") if isinstance(story_context.get("warnings"), list) else []) if str(item).strip()],
    ]
    errors = [str(item) for item in (parsed.get("errors") if isinstance(parsed.get("errors"), list) else []) if str(item).strip()]
    if diagnostics.get("httpStatus"):
        error_code, sanitized_error = _sanitize_gemini_error(diagnostics, parsed)
        if error_code not in errors:
            errors.append(error_code)
        if sanitized_error and not diagnostics.get("sanitizedError"):
            diagnostics["sanitizedError"] = sanitized_error
    if diagnostics.get("fallbackFrom") and diagnostics.get("fallbackTo"):
        warnings.append(f"gemini_model_fallback:{diagnostics['fallbackFrom']}->{diagnostics['fallbackTo']}")
    if not system_instruction_used:
        warnings.append("system_instruction_fallback_to_inline_prompt")

    parse_result = parse_gemini_planner_output(parsed if isinstance(parsed, dict) else {})
    warnings.extend(parse_result.warnings)
    errors.extend(parse_result.errors)
    validation_report = validate_audio_first_gemini_planner_output(planner_input, parse_result.parsed)
    warnings.extend(validation_report.warnings)
    errors.extend(validation_report.errors)
    warnings = list(dict.fromkeys([str(item).strip() for item in warnings if str(item).strip()]))
    errors = list(dict.fromkeys([str(item).strip() for item in errors if str(item).strip()]))
    validation_report = GeminiPlannerValidationReport(
        valid=len(errors) == 0 and validation_report.valid,
        blocked=validation_report.blocked,
        errors=errors,
        warnings=warnings,
    )

    role_type_by_role = normalized.get("roleTypeByRole") if isinstance(normalized.get("roleTypeByRole"), dict) else {}
    role_selection_source_by_role = normalized.get("roleSelectionSourceByRole") if isinstance(normalized.get("roleSelectionSourceByRole"), dict) else {}
    contract_result = map_gemini_plan_to_canonical_audio_first_output(
        planner_input,
        parse_result.parsed,
        validation_report,
        raw_payload=parse_result.raw_payload or (parsed if isinstance(parsed, dict) else {}),
        raw_debug_summary=(parse_result.parsed.debug_summary if parse_result.parsed else None),
        parse_result=parse_result,
    )
    canonical_dump = contract_result.canonical_output.model_dump(mode="json")
    scenes = contract_result.compatibility_scenes
    role_validation = _validate_role_distribution(
        scenes,
        role_mode=normalized.get("roleMode") or "auto",
        role_dominance_mode=normalized.get("roleDominanceMode") or "off",
        role_type_by_role=role_type_by_role,
    )
    role_usage_by_scene = role_validation.get("roleUsageByScene") if isinstance(role_validation.get("roleUsageByScene"), list) else []
    dominant_role_by_scene = role_validation.get("dominantRoleByScene") if isinstance(role_validation.get("dominantRoleByScene"), dict) else {}
    dominant_role_type_by_scene = role_validation.get("dominantRoleTypeByScene") if isinstance(role_validation.get("dominantRoleTypeByScene"), dict) else {}
    scene_intent_by_scene = role_validation.get("sceneIntentByScene") if isinstance(role_validation.get("sceneIntentByScene"), list) else []
    scene_intent_confidence = role_validation.get("sceneIntentConfidence") if isinstance(role_validation.get("sceneIntentConfidence"), list) else []
    scene_intent_warnings = role_validation.get("sceneIntentWarnings") if isinstance(role_validation.get("sceneIntentWarnings"), list) else []
    scene_intent_diagnostics = role_validation.get("sceneIntentDiagnostics") if isinstance(role_validation.get("sceneIntentDiagnostics"), list) else []
    role_dominance_warnings = role_validation.get("roleDominanceWarnings") if isinstance(role_validation.get("roleDominanceWarnings"), list) else []
    role_validation_warnings = role_validation.get("roleValidationWarnings") if isinstance(role_validation.get("roleValidationWarnings"), list) else []
    role_validation_status = str(role_validation.get("roleValidationStatus") or "ok")
    role_refinement_attempted = False
    role_refinement_succeeded = False
    role_refinement_decision, role_refinement_note = "not_needed", "Locked-role refinement was not needed."
    role_refinement_status_before = role_validation_status
    role_refinement_warnings_before = list(role_validation_warnings)
    role_refinement_status_after = role_validation_status
    role_refinement_warnings_after = list(role_validation_warnings)
    role_refinement_model_used = ""
    role_refinement_raw_preview = ""
    role_refinement_improvement_type = "no_change"
    refined_role_warnings: list[str] = []
    should_refine_roles, role_refinement_decision = _decide_locked_role_refinement(
        role_mode=normalized.get("roleMode") or "auto",
        role_dominance_mode=normalized.get("roleDominanceMode") or "off",
        role_validation_status=role_validation_status,
        role_validation_warnings=role_validation_warnings,
        storyboard={"scenes": scenes},
        role_type_by_role=role_type_by_role,
    )
    if should_refine_roles and len(validation_report.errors) == 0 and (parse_result.parsed.planning_status == GeminiPlanningStatus.ok if parse_result.parsed else False):
        role_refinement_attempted = True
        role_refinement_prompt = _build_locked_role_refinement_prompt(
            normalized,
            {"scenes": scenes},
            role_type_by_role,
            role_selection_source_by_role,
            role_validation_warnings,
        )
        refinement_parts = [
            {"text": "=== LOCKED ROLE REFINEMENT REQUEST ===\n" + role_refinement_prompt},
            *multimodal_parts,
        ]
        refinement_body = {
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            "systemInstruction": {"parts": [{"text": planner_system_instruction}]},
            "contents": [{"role": "user", "parts": refinement_parts}],
        }
        refined_parsed_raw, refined_diagnostics = _call_gemini_plan_with_model_fallback(api_key, requested_model, refinement_body)
        refined_system_instruction_used = True
        if _supports_system_instruction_error(refined_diagnostics):
            refinement_fallback_body = {
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": f"{planner_system_instruction}\n\n=== LOCKED ROLE REFINEMENT REQUEST ===\n{role_refinement_prompt}\n\n=== GEMINI PLANNER RUNTIME PAYLOAD ===\n{planner_runtime_payload}"}, *multimodal_parts[1:]],
                    }
                ],
            }
            refined_parsed_raw, refined_diagnostics = _call_gemini_plan_with_model_fallback(api_key, requested_model, refinement_fallback_body)
            refined_system_instruction_used = False
        role_refinement_model_used = str(refined_diagnostics.get("effectiveModel") or refined_diagnostics.get("requestedModel") or requested_model)
        role_refinement_raw_preview = _shorten_debug_preview(refined_diagnostics.get("rawPreview") or "")
        if not refined_system_instruction_used:
            refined_role_warnings.append("locked_role_refinement_system_instruction_fallback_to_inline_prompt")
        if refined_diagnostics.get("fallbackFrom") and refined_diagnostics.get("fallbackTo"):
            refined_role_warnings.append(f"locked_role_refinement_model_fallback:{refined_diagnostics['fallbackFrom']}->{refined_diagnostics['fallbackTo']}")
        refined_parse_result = parse_gemini_planner_output(refined_parsed_raw if isinstance(refined_parsed_raw, dict) else {})
        refined_role_warnings.extend(refined_parse_result.warnings)
        refined_role_errors = list(refined_parse_result.errors)
        refined_validation_report = validate_audio_first_gemini_planner_output(planner_input, refined_parse_result.parsed)
        refined_role_warnings.extend(refined_validation_report.warnings)
        refined_role_errors.extend(refined_validation_report.errors)
        refined_role_warnings = list(dict.fromkeys([str(item).strip() for item in refined_role_warnings if str(item).strip()]))
        refined_role_errors = list(dict.fromkeys([str(item).strip() for item in refined_role_errors if str(item).strip()]))
        if refined_role_errors:
            role_refinement_improvement_type = "rejected"
            role_refinement_note = "Locked-role refinement failed validation; original storyboard_v1 was kept."
            warnings.extend([f"locked_role_refinement_failed:{item}" for item in refined_role_errors])
        else:
            refined_validation_report = GeminiPlannerValidationReport(
                valid=refined_validation_report.valid,
                blocked=refined_validation_report.blocked,
                errors=refined_role_errors,
                warnings=refined_role_warnings,
            )
            refined_contract_result = map_gemini_plan_to_canonical_audio_first_output(
                planner_input,
                refined_parse_result.parsed,
                refined_validation_report,
                raw_payload=refined_parse_result.raw_payload or (refined_parsed_raw if isinstance(refined_parsed_raw, dict) else {}),
                raw_debug_summary=(refined_parse_result.parsed.debug_summary if refined_parse_result.parsed else None),
                parse_result=refined_parse_result,
            )
            refined_scenes = refined_contract_result.compatibility_scenes
            refined_role_validation = _validate_role_distribution(
                refined_scenes,
                role_mode=normalized.get("roleMode") or "auto",
                role_dominance_mode=normalized.get("roleDominanceMode") or "off",
                role_type_by_role=role_type_by_role,
            )
            refined_dominant_role_by_scene = (
                refined_role_validation.get("dominantRoleByScene")
                if isinstance(refined_role_validation.get("dominantRoleByScene"), dict)
                else {}
            )
            refined_dominant_role_type_by_scene = (
                refined_role_validation.get("dominantRoleTypeByScene")
                if isinstance(refined_role_validation.get("dominantRoleTypeByScene"), dict)
                else {}
            )
            refined_role_dominance_warnings = (
                refined_role_validation.get("roleDominanceWarnings")
                if isinstance(refined_role_validation.get("roleDominanceWarnings"), list)
                else []
            )
            refined_role_validation_warnings = (
                refined_role_validation.get("roleValidationWarnings")
                if isinstance(refined_role_validation.get("roleValidationWarnings"), list)
                else []
            )
            refined_scene_intent_by_scene = (
                refined_role_validation.get("sceneIntentByScene")
                if isinstance(refined_role_validation.get("sceneIntentByScene"), list)
                else []
            )
            refined_scene_intent_confidence = (
                refined_role_validation.get("sceneIntentConfidence")
                if isinstance(refined_role_validation.get("sceneIntentConfidence"), list)
                else []
            )
            refined_scene_intent_warnings = (
                refined_role_validation.get("sceneIntentWarnings")
                if isinstance(refined_role_validation.get("sceneIntentWarnings"), list)
                else []
            )
            refined_scene_intent_diagnostics = (
                refined_role_validation.get("sceneIntentDiagnostics")
                if isinstance(refined_role_validation.get("sceneIntentDiagnostics"), list)
                else []
            )
            refined_role_validation_status = str(refined_role_validation.get("roleValidationStatus") or "ok")
            role_refinement_status_after = refined_role_validation_status
            role_refinement_warnings_after = list(refined_role_validation_warnings)
            role_refinement_improvement_type = _classify_refined_storyboard_improvement(
                status_before=role_refinement_status_before,
                warnings_before=role_refinement_warnings_before,
                status_after=refined_role_validation_status,
                warnings_after=refined_role_validation_warnings,
                scenes_before=scenes,
                scenes_after=refined_scenes,
            )
            if role_refinement_improvement_type in {"warnings_reduced", "status_improved", "structure_preserved"}:
                role_refinement_succeeded = True
                parsed = refined_parsed_raw if isinstance(refined_parsed_raw, dict) else parsed
                diagnostics = {
                    **diagnostics,
                    "roleRefinement": {
                        "httpStatus": refined_diagnostics.get("httpStatus"),
                        "rawPreview": refined_diagnostics.get("rawPreview") or "",
                        "warnings": refined_role_warnings,
                        "roleRefinementImprovementType": role_refinement_improvement_type,
                    },
                }
                parse_result = refined_parse_result
                validation_report = refined_validation_report
                contract_result = refined_contract_result
                canonical_dump = contract_result.canonical_output.model_dump(mode="json")
                scenes = refined_scenes
                role_usage_by_scene = (
                    refined_role_validation.get("roleUsageByScene")
                    if isinstance(refined_role_validation.get("roleUsageByScene"), list)
                    else []
                )
                dominant_role_by_scene = dict(refined_dominant_role_by_scene)
                dominant_role_type_by_scene = dict(refined_dominant_role_type_by_scene)
                role_dominance_warnings = list(refined_role_dominance_warnings)
                role_validation_warnings = list(refined_role_validation_warnings)
                scene_intent_by_scene = list(refined_scene_intent_by_scene)
                scene_intent_confidence = list(refined_scene_intent_confidence)
                scene_intent_warnings = list(refined_scene_intent_warnings)
                scene_intent_diagnostics = list(refined_scene_intent_diagnostics)
                role_validation_status = refined_role_validation_status
                role_refinement_note = "Locked-role refinement was applied and improved or preserved validation without making it worse."
            else:
                if role_refinement_improvement_type not in {"no_change", "rejected"}:
                    role_refinement_improvement_type = "rejected"
                role_refinement_note = "Locked-role refinement returned a weaker or non-improving storyboard; original storyboard_v1 was kept."
                role_refinement_warnings_after = list(refined_role_validation_warnings)
                role_refinement_status_after = refined_role_validation_status
    elif role_refinement_decision == "skipped_auto_mode":
        role_refinement_note = "Locked-role refinement is disabled in auto role mode."
    elif role_refinement_decision == "skipped_trivial_storyboard":
        role_refinement_note = "Locked-role refinement was skipped because the storyboard is too small for a meaningful second pass."
    elif role_refinement_decision == "skipped_no_locked_roles":
        role_refinement_note = "Locked-role refinement was skipped because no locked hero/support/antagonist roles were present."
    elif role_refinement_decision == "skipped_role_dominance_off":
        role_refinement_note = "Locked-role refinement kept role presence checks only because roleDominanceMode is off."
    elif role_refinement_decision == "soft_mode_warning_only":
        role_refinement_note = "Locked-role refinement stayed conservative because roleDominanceMode is soft; warnings were surfaced without forcing stronger repair."
    elif role_refinement_decision == "not_needed":
        role_refinement_note = "Locked-role refinement was not needed because locked-role warnings were absent."
    timing_debug = {
        "sceneCount": len(scenes),
        "sceneCountAfterSpeechSplit": len(scenes),
        "audioDurationSec": normalized.get("audioDurationSec"),
        "timelineDurationSec": max([float(scene.get("endSec") or 0.0) for scene in scenes], default=0.0),
        "sceneDurationTotalSec": round(sum(max(0.0, float(scene.get("durationSec") or 0.0)) for scene in scenes), 3),
        "normalizationApplied": False,
        "normalizationReason": None,
    }
    segmentation_debug = {
        "segmentationMode": "gemini_audio_first_contract",
        "segmentationReason": "gemini_scene_and_shot_planning",
        "sceneCount": len(scenes),
    }
    director_debug = _build_director_debug(scenes)
    preview = _build_preview_from_scenes(scenes, world_lock)
    if not preview.get("sourceSceneId") and scenes:
        preview["sourceSceneId"] = str((scenes[0] or {}).get("sceneId") or "")

    plan_meta = {
        "mode": normalized.get("mode"),
        "plannerMode": "gemini_only",
        "output": normalized.get("output"),
        "stylePreset": normalized.get("stylePreset"),
        "audioStoryMode": normalized.get("audioStoryMode"),
        "audioStoryModeRequested": normalized.get("audioStoryModeRequested"),
        "audioStoryModeGuardReason": normalized.get("audioStoryModeGuardReason"),
        "genre": normalized.get("genre") or planner_input.genre,
        "storyControlMode": normalized.get("storyControlMode"),
        "storyMissionSummary": normalized.get("storyMissionSummary"),
        "timelineSource": normalized.get("timelineSource") or "gemini_audio_first_contract",
        "narrativeSource": narrative_source,
        "storySource": story_source,
        "plannerSource": "gemini",
        "canonicalSourceOfTruth": True,
        "returnsCompatibilityProjection": True,
        "compatibilityProjection": True,
        "canonicalIsProjection": False,
        "weakSemanticContext": bool(story_context.get("weakSemanticContext")),
        "semanticContextReason": story_context.get("semanticContextReason") or "",
        "audioDurationSec": timing_debug.get("audioDurationSec"),
        "timelineDurationSec": timing_debug.get("timelineDurationSec"),
        "sceneDurationTotalSec": timing_debug.get("sceneDurationTotalSec"),
        "worldLock": world_lock,
        "entityLocks": entity_locks,
        "preview": preview,
        "plannerInput": planner_input.model_dump(mode="json", exclude_none=True),
        "roleTypeByRole": role_type_by_role,
        "roleSelectionSourceByRole": normalized.get("roleSelectionSourceByRole") if isinstance(normalized.get("roleSelectionSourceByRole"), dict) else {},
        "roleMode": normalized.get("roleMode") or "auto",
        "roleModeReason": normalized.get("roleModeReason") or "",
        "roleDominanceMode": normalized.get("roleDominanceMode") or "off",
        "roleDominanceModeReason": normalized.get("roleDominanceModeReason") or "",
        "roleDominanceApplied": bool(normalized.get("roleDominanceApplied")),
        "systemPromptVersion": "gemini_audio_first_planner_v1",
        "plannerContractName": planner_input.planner_contract_name,
        "contractVersion": planner_input.contract_version,
        "schemaVersion": planner_input.schema_version,
        "plannerSourceContract": planner_input.planner_source,
        "validationWarnings": validation_report.warnings,
        "validationErrors": validation_report.errors,
        "roleValidationWarnings": role_validation_warnings,
        "roleValidationStatus": role_validation_status,
        "dominantRoleByScene": dominant_role_by_scene,
        "dominantRoleTypeByScene": dominant_role_type_by_scene,
        "sceneIntentByScene": scene_intent_by_scene,
        "sceneIntentConfidence": scene_intent_confidence,
        "sceneIntentWarnings": scene_intent_warnings,
        "sceneIntentDiagnostics": scene_intent_diagnostics,
        "roleDominanceWarnings": role_dominance_warnings,
        "roleRefinementAttempted": role_refinement_attempted,
        "roleRefinementSucceeded": role_refinement_succeeded,
        "roleRefinementDecision": role_refinement_decision,
        "roleRefinementWarningsBefore": role_refinement_warnings_before,
        "roleRefinementWarningsAfter": role_refinement_warnings_after,
        "roleRefinementStatusBefore": role_refinement_status_before,
        "roleRefinementStatusAfter": role_refinement_status_after,
        "roleRefinementModelUsed": role_refinement_model_used,
        "roleRefinementRawPreview": role_refinement_raw_preview,
        "roleRefinementImprovementType": role_refinement_improvement_type,
        "roleRefinementNote": role_refinement_note,
        "summary": {
            "sceneCount": len(scenes),
            "cameraContinuityScore": director_debug.get("cameraContinuityScore"),
            "humanAnchorCoverage": director_debug.get("humanAnchorCoverage"),
            "continuationChainCount": director_debug.get("continuationChainCount"),
        },
    }
    return {
        "ok": len(validation_report.errors) == 0 and (parse_result.parsed.planning_status == GeminiPlanningStatus.ok if parse_result.parsed else False),
        "planMeta": plan_meta,
        "globalContinuity": world_lock,
        "scenes": scenes,
        "warnings": list(dict.fromkeys([*validation_report.warnings, *role_validation_warnings])),
        "errors": validation_report.errors,
        "canonicalPlanning": canonical_dump,
        "debug": {
            **(parsed.get("debug") if isinstance(parsed.get("debug"), dict) else {}),
            "plannerMode": "gemini_only",
            "planner_source": "gemini",
            "plannerSource": "gemini",
            "requestedModel": diagnostics.get("requestedModel") or requested_model,
            "fallbackFrom": diagnostics.get("fallbackFrom"),
            "fallbackTo": diagnostics.get("fallbackTo"),
            "effectiveModel": diagnostics.get("effectiveModel") or requested_model,
            "httpStatus": diagnostics.get("httpStatus"),
            "rawPreview": diagnostics.get("rawPreview") or "",
            "errorText": diagnostics.get("errorText") or "",
            "parseFailedReason": "; ".join(validation_report.errors) if validation_report.errors else "",
            "sanitizedError": diagnostics.get("sanitizedError") or _humanize_storyboard_error((validation_report.errors[0] if validation_report.errors else "")),
            "storySource": story_context.get("storySource"),
            "narrativeSource": story_context.get("narrativeSource"),
            "weakSemanticContext": bool(story_context.get("weakSemanticContext")),
            "semanticContextReason": story_context.get("semanticContextReason") or "",
            "audioStoryModeRequested": story_context.get("audioStoryModeRequested"),
            "audioStoryModeGuardReason": story_context.get("audioStoryModeGuardReason"),
            "hasAudio": story_context.get("hasAudio"),
            "hasText": story_context.get("hasText"),
            "hasRefs": story_context.get("hasRefs"),
            "systemPromptVersion": "gemini_audio_first_planner_v1",
            "systemInstructionUsed": system_instruction_used,
            "plannerContractName": planner_input.planner_contract_name,
            "planner_contract_name": planner_input.planner_contract_name,
            "contractVersion": planner_input.contract_version,
            "contract_version": planner_input.contract_version,
            "schemaVersion": planner_input.schema_version,
            "schema_version": planner_input.schema_version,
            "contractParseMode": parse_result.parse_mode,
            "parseMode": parse_result.parse_mode,
            "parse_mode": parse_result.parse_mode,
            "contractSchemaValid": parse_result.contract_schema_valid,
            "contract_schema_valid": parse_result.contract_schema_valid,
            "contractValidationErrors": parse_result.contract_validation_errors,
            "contractValidationWarnings": parse_result.contract_validation_warnings,
            "rawGeminiContractVersion": parse_result.raw_gemini_contract_version,
            "rawGeminiSchemaVersion": parse_result.raw_gemini_schema_version,
            "parserDebugSummary": parse_result.parser_debug_summary,
            "parserNotes": parse_result.parser_notes,
            "parser_notes": parse_result.parser_notes,
            "plannerInput": planner_input.model_dump(mode="json", exclude_none=True),
            "plannerValidationWarnings": validation_report.warnings,
            "plannerValidationErrors": validation_report.errors,
            "validation_errors": validation_report.errors,
            "validation_warnings": validation_report.warnings,
            "canonicalSourceOfTruth": True,
            "returnsCompatibilityProjection": True,
            "canonicalIsProjection": False,
            "parsedGeminiContract": parse_result.raw_payload or {},
            "plannerRequestLayers": {
                "systemInstruction": planner_system_instruction,
                "outputContract": planner_output_contract,
                "runtimePayload": planner_runtime_payload,
            },
            "worldLock": world_lock,
            "entityLocks": entity_locks,
            "preview": preview,
            "timing": timing_debug,
            "segmentation": segmentation_debug,
            **director_debug,
            "roleMode": normalized.get("roleMode") or "auto",
            "roleModeReason": normalized.get("roleModeReason") or "",
            "roleDominanceMode": normalized.get("roleDominanceMode") or "off",
            "roleDominanceModeReason": normalized.get("roleDominanceModeReason") or "",
            "roleDominanceApplied": bool(normalized.get("roleDominanceApplied")),
            "roleTypeByRole": role_type_by_role,
            "roleSelectionSourceByRole": normalized.get("roleSelectionSourceByRole") if isinstance(normalized.get("roleSelectionSourceByRole"), dict) else {},
            "roleUsageByScene": role_usage_by_scene,
            "dominantRoleByScene": dominant_role_by_scene,
            "dominantRoleTypeByScene": dominant_role_type_by_scene,
            "sceneIntentByScene": scene_intent_by_scene,
            "sceneIntentConfidence": scene_intent_confidence,
            "sceneIntentWarnings": scene_intent_warnings,
            "sceneIntentDiagnostics": scene_intent_diagnostics,
            "roleDominanceWarnings": role_dominance_warnings,
            "roleValidationWarnings": role_validation_warnings,
            "roleValidationStatus": role_validation_status,
            "roleRefinementAttempted": role_refinement_attempted,
            "roleRefinementSucceeded": role_refinement_succeeded,
            "roleRefinementDecision": role_refinement_decision,
            "roleRefinementWarningsBefore": role_refinement_warnings_before,
            "roleRefinementWarningsAfter": role_refinement_warnings_after,
            "roleRefinementStatusBefore": role_refinement_status_before,
            "roleRefinementStatusAfter": role_refinement_status_after,
            "roleRefinementModelUsed": role_refinement_model_used,
            "roleRefinementRawPreview": role_refinement_raw_preview,
            "roleRefinementImprovementType": role_refinement_improvement_type,
            "roleRefinementNote": role_refinement_note,
            "referenceProfilesSummary": summarize_profiles(reference_profiles),
            "activeRolesByScene": {str(scene.get("sceneId") or ""): list(scene.get("activeRefs") or []) for scene in scenes},
            **media_debug,
            "rawEntityTypesByRole": {
                role: lock.get("rawEntityType")
                for role, lock in entity_locks.items()
                if isinstance(lock, dict)
            },
            "normalizedEntityTypesByRole": {
                role: lock.get("normalizedEntityType") or lock.get("entityType")
                for role, lock in entity_locks.items()
                if isinstance(lock, dict)
            },
        },
    }


def _build_audio_first_foundation_response(normalized: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    existing_canonical = result.get("canonicalPlanning") if isinstance(result.get("canonicalPlanning"), dict) else None
    if existing_canonical is not None:
        canonical_dump = existing_canonical
    else:
        project_input = build_project_planning_input(normalized)
        canonical_output = build_audio_first_planner_output(project_input, result)
        canonical_dump = canonical_output.model_dump(mode="json")
        result["canonicalPlanning"] = canonical_dump
    plan_meta = result.get("planMeta") if isinstance(result.get("planMeta"), dict) else {}
    plan_meta["projectMode"] = canonical_dump.get("project_mode")
    plan_meta["inputMode"] = canonical_dump.get("input_mode")
    plan_meta["planningBlocked"] = bool((canonical_dump.get("validation") or {}).get("blocked"))
    plan_meta["planningBlockedReason"] = (canonical_dump.get("planning_context") or {}).get("planning_blocked_reason")
    plan_meta["audioFirstCanon"] = {
        "brain": "gemini",
        "timingSource": "master_audio",
        "elevenLabsUsage": "full_master_narration_only",
        "globalMusicTrackLayer": bool(normalized.get("globalMusicTrackUrl")),
        "auxiliaryAudioAnalyzerRole": "debug_fallback_only",
    }
    result["planMeta"] = plan_meta
    result.setdefault("warnings", [])
    result.setdefault("errors", [])
    result["warnings"] = list(dict.fromkeys([*result.get("warnings", []), *((canonical_dump.get("validation") or {}).get("warnings") or [])]))
    result["errors"] = list(dict.fromkeys([*result.get("errors", []), *((canonical_dump.get("validation") or {}).get("errors") or [])]))
    debug = result.get("debug") if isinstance(result.get("debug"), dict) else {}
    debug["audioFirstPlanning"] = canonical_dump.get("debug") or {}
    result["debug"] = debug
    scenes = result.get("scenes") if isinstance(result.get("scenes"), list) else []
    canonical_scenes = canonical_dump.get("scenes") if isinstance(canonical_dump.get("scenes"), list) else []
    canonical_by_scene = {str(scene.get("scene_id") or ""): scene for scene in canonical_scenes if isinstance(scene, dict)}
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_key = str(scene.get("sceneId") or scene.get("id") or "")
        canonical_scene = canonical_by_scene.get(scene_key)
        if not canonical_scene:
            continue
        shots = canonical_scene.get("shots") if isinstance(canonical_scene.get("shots"), list) else []
        shot = shots[0] if shots and isinstance(shots[0], dict) else {}
        scene["projectMode"] = canonical_dump.get("project_mode")
        scene["inputMode"] = canonical_dump.get("input_mode")
        scene["audioSegmentType"] = canonical_scene.get("audio_segment_type") or shot.get("audio_segment_type")
        scene["narrationMode"] = canonical_scene.get("narration_mode") or shot.get("narration_mode")
        scene["renderMode"] = shot.get("render_mode") or scene.get("renderMode")
        scene["renderReason"] = shot.get("render_reason")
        scene["hasVocalRhythm"] = bool(shot.get("has_vocal_rhythm"))
        scene["motionInterpretation"] = shot.get("motion_interpretation")
        scene["lipsyncPolicy"] = shot.get("lipsync_policy")
        scene["startFrameSource"] = shot.get("start_frame_source")
        scene["parentShotId"] = shot.get("parent_shot_id")
        scene["needsTwoFrames"] = bool(shot.get("needs_two_frames"))
        scene["validationErrors"] = shot.get("validation_errors") or []
        scene["validationWarnings"] = shot.get("validation_warnings") or []
    return result


def run_comfy_plan(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_comfy_payload(payload)
    project_input = build_project_planning_input(normalized)
    canonical_probe = build_audio_first_planner_output(project_input, {"scenes": []})
    if canonical_probe.validation.blocked:
        return _build_audio_first_foundation_response(
            normalized,
            {
                "ok": False,
                "planMeta": {
                    "mode": normalized.get("mode"),
                    "plannerMode": normalized.get("plannerMode"),
                    "output": normalized.get("output"),
                    "stylePreset": normalized.get("stylePreset"),
                    "audioStoryMode": normalized.get("audioStoryMode"),
                    "timelineSource": normalized.get("timelineSource") or "master_audio_required",
                    "narrativeSource": normalized.get("narrativeSource") or "text_waiting_for_audio",
                },
                "globalContinuity": {},
                "scenes": [],
                "warnings": canonical_probe.validation.warnings,
                "errors": canonical_probe.validation.errors,
                "debug": {
                    "plannerMode": normalized.get("plannerMode"),
                    "blockingReason": canonical_probe.planning_context.planning_blocked_reason,
                },
            },
        )
    if normalized.get("plannerMode") == "gemini_only":
        return _build_audio_first_foundation_response(normalized, _run_comfy_plan_gemini_only(normalized))
    if normalized.get("mode") == "clip":
        logger.info(
            "[COMFY PLAN][clip] plannerMode=%s audioStoryMode=%s text=%s lyricsText=%s transcriptText=%s spokenHint=%s semanticHints=%s semanticSummary=%s audio=%s",
            normalized.get("plannerMode"),
            normalized.get("audioStoryMode"),
            bool(normalized.get("text")),
            bool(normalized.get("lyricsText")),
            bool(normalized.get("transcriptText")),
            bool(normalized.get("spokenTextHint")),
            bool(normalized.get("audioSemanticHints")),
            bool(normalized.get("audioSemanticSummary")),
            bool(normalized.get("audioUrl")),
        )
        clip_result = plan_comfy_clip(normalized)
        clip_meta = clip_result.get("planMeta") if isinstance(clip_result, dict) else {}
        if isinstance(clip_meta, dict):
            clip_meta["plannerMode"] = normalized.get("plannerMode") or "legacy"
        clip_debug = clip_result.get("debug") if isinstance(clip_result.get("debug"), dict) else {}
        if isinstance(clip_debug, dict):
            clip_debug["plannerMode"] = normalized.get("plannerMode") or "legacy"
        logger.info(
            "[COMFY PLAN][clip] resolved textSource=%s exactLyricsAvailable=%s transcriptAvailable=%s usedSemanticFallback=%s semanticHintCount=%s",
            (clip_meta or {}).get("textSource"),
            (clip_meta or {}).get("exactLyricsAvailable"),
            (clip_meta or {}).get("transcriptAvailable"),
            (clip_meta or {}).get("usedSemanticFallback"),
            (clip_meta or {}).get("semanticHintCount"),
        )
        return _build_audio_first_foundation_response(normalized, clip_result)

    reference_profiles = build_reference_profiles(normalized.get("refsByRole") or {})
    normalized["referenceProfiles"] = reference_profiles
    refs_presence = {k: len(v) for k, v in normalized["refsByRole"].items()}
    debug_signature = "COMFY_DEBUG_STEP_V1"
    module_file = __file__
    # TEMP HARD DEBUG STEP (REMOVE AFTER CONFIRMATION):
    # VERIFY EXACT FILE + EXACT MODEL for COMFY planner requests.
    hard_debug_disable_fallback = True
    logger.info(
        "[COMFY PLAN] request summary plannerMode=%s mode=%s output=%s style=%s audioStoryMode=%s",
        normalized["plannerMode"],
        normalized["mode"],
        normalized["output"],
        normalized["stylePreset"],
        normalized["audioStoryMode"],
    )
    logger.info("[COMFY PLAN] text/audio/refs presence text=%s audio=%s refs=%s", bool(normalized["text"]), bool(normalized["audioUrl"]), refs_presence)
    logger.warning("[%s] run_comfy_plan entered module_file=%s", debug_signature, module_file)
    print(f"[{debug_signature}] ENTER run_comfy_plan")
    print(f"[{debug_signature}] FILE = {module_file}")

    api_key = (settings.GEMINI_API_KEY or "").strip()
    # TEMP DEBUG STEP: hard pin model to remove ambiguity for diagnostic run.
    requested_model = "gemini-2.5-flash"
    logger.warning("[%s] hard_requested_model=%s", debug_signature, requested_model)
    logger.warning("[%s] effective_model_before_request=%s", debug_signature, requested_model)
    print(f"[{debug_signature}] HARD MODEL = {requested_model}")
    if not api_key:
        return {"ok": False, "planMeta": {}, "globalContinuity": {}, "scenes": [], "warnings": [], "errors": ["gemini_api_key_missing"], "debug": {"debugSignature": debug_signature, "moduleFile": module_file, "requestedModel": requested_model, "effectiveModel": None, "httpStatus": None, "rawPreview": "", "sanitizedError": _humanize_storyboard_error("gemini_api_key_missing"), "normalizedPayload": normalized, "fallbackFrom": None, "normalizedScenesCount": 0}}

    body = {
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
        "contents": [{"role": "user", "parts": [{"text": build_comfy_planner_prompt(normalized)}]}],
    }

    parsed, diagnostics = _call_gemini_plan(api_key, requested_model, body)
    warnings: list[str] = []
    errors: list[str] = []

    if not hard_debug_disable_fallback and diagnostics["httpStatus"] == 404 and requested_model != FALLBACK_GEMINI_MODEL:
        logger.info("[COMFY PLAN] fallback_from=%s fallback_to=%s", requested_model, FALLBACK_GEMINI_MODEL)
        warnings.append(f"gemini_model_fallback:{requested_model}->{FALLBACK_GEMINI_MODEL}")
        parsed_fb, diagnostics_fb = _call_gemini_plan(api_key, FALLBACK_GEMINI_MODEL, body)
        diagnostics = {
            **diagnostics_fb,
            "requestedModel": requested_model,
            "effectiveModel": diagnostics_fb.get("effectiveModel") or FALLBACK_GEMINI_MODEL,
            "fallbackFrom": requested_model,
        }
        parsed = parsed_fb

    if diagnostics.get("httpStatus"):
        errors.append(f"gemini_http_error:{diagnostics['httpStatus']}")
    elif isinstance(parsed, dict) and "errors" in parsed and parsed.get("errors") == ["gemini_invalid_json"]:
        errors.append("gemini_invalid_json")
        parsed = {}

    raw_scenes = parsed.get("scenes") if isinstance(parsed.get("scenes"), list) else []
    scenes = [_normalize_scene(scene, idx, normalized.get("refsByRole")) for idx, scene in enumerate(raw_scenes)]
    prompt_contract_warnings: list[str] = []
    for scene in scenes:
        scene_id = str(scene.get("sceneId") or "unknown_scene")
        missing = scene.get("promptMissingLangs") if isinstance(scene.get("promptMissingLangs"), dict) else {}
        image_missing = missing.get("image") if isinstance(missing.get("image"), list) else []
        video_missing = missing.get("video") if isinstance(missing.get("video"), list) else []
        if image_missing:
            prompt_contract_warnings.append(f"scene:{scene_id}:image_missing_languages:{','.join(sorted(set(str(x) for x in image_missing)))}")
        if video_missing:
            prompt_contract_warnings.append(f"scene:{scene_id}:video_missing_languages:{','.join(sorted(set(str(x) for x in video_missing)))}")
    if prompt_contract_warnings:
        warnings.append("planner_prompt_language_contract_not_fully_met")
        warnings.extend(prompt_contract_warnings)

    scenes, timing_debug = _normalize_scene_timeline(scenes, normalized.get("audioDurationSec"))
    scenes, speech_split_debug, speech_split_warnings = _split_oversized_speech_scenes(scenes, normalized)
    warnings.extend(speech_split_warnings)
    timing_debug["sceneCountAfterSpeechSplit"] = len(scenes)
    segmentation_debug = {**_build_segmentation_debug(scenes, normalized.get("audioStoryMode") or "lyrics_music", timing_debug), **speech_split_debug}
    initial_segmentation_debug = dict(segmentation_debug)
    initial_scene_count = len(scenes)
    refinement_attempted = False
    refinement_succeeded = False
    refinement_reasons: list[str] = []
    refinement_pass_count = 0
    refinement_errors: list[str] = []
    refinement_warnings: list[str] = []

    needs_refinement, refinement_reasons = _needs_segmentation_refinement(
        segmentation_debug,
        normalized.get("audioDurationSec"),
        len(scenes),
    )

    if needs_refinement and len(scenes) > 0 and len(errors) == 0:
        refinement_attempted = True
        refinement_pass_count = 1
        refinement_reason_str = ",".join(refinement_reasons)
        refinement_body = {
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.35},
            "contents": [{"role": "user", "parts": [{"text": build_comfy_planner_refinement_prompt(normalized, scenes, refinement_reason_str)}]}],
        }
        refined_parsed, refined_diagnostics = _call_gemini_plan(api_key, requested_model, refinement_body)
        refined_http_status = refined_diagnostics.get("httpStatus")
        if refined_http_status:
            warnings.append(f"segmentation_refinement_http_error:{refined_http_status}")
        else:
            refinement_errors = [str(err) for err in (refined_parsed.get("errors") if isinstance(refined_parsed.get("errors"), list) else []) if str(err)]
            refinement_warnings = [str(warn) for warn in (refined_parsed.get("warnings") if isinstance(refined_parsed.get("warnings"), list) else []) if str(warn)]
            if len(refinement_errors) > 0:
                warnings.append("segmentation_refinement_failed_with_errors")
                warnings.append(f"segmentation_refinement_errors:{'|'.join(refinement_errors)}")
            if len(refinement_warnings) > 0:
                warnings.append(f"segmentation_refinement_warnings:{'|'.join(refinement_warnings)}")
            refined_raw_scenes = refined_parsed.get("scenes") if isinstance(refined_parsed.get("scenes"), list) else []
            refined_scenes = [_normalize_scene(scene, idx, normalized.get("refsByRole")) for idx, scene in enumerate(refined_raw_scenes)]
            valid_refined_scenes = [
                scene for scene in refined_scenes
                if (_to_float(scene.get("endSec")) or 0.0) > (_to_float(scene.get("startSec")) or 0.0)
            ]
            if len(refined_scenes) == 0:
                warnings.append("segmentation_refinement_returned_no_scenes")
            elif len(valid_refined_scenes) == 0:
                warnings.append("segmentation_refinement_returned_invalid_scenes")
            elif len(refinement_errors) == 0:
                scenes, timing_debug = _normalize_scene_timeline(valid_refined_scenes, normalized.get("audioDurationSec"))
                scenes, speech_split_debug, speech_split_warnings = _split_oversized_speech_scenes(scenes, normalized)
                warnings.extend(speech_split_warnings)
                timing_debug["sceneCountAfterSpeechSplit"] = len(scenes)
                segmentation_debug = {**_build_segmentation_debug(scenes, normalized.get("audioStoryMode") or "lyrics_music", timing_debug), **speech_split_debug}
                parsed = refined_parsed
                refinement_succeeded = True
                diagnostics = {
                    **diagnostics,
                    "refinement": {
                        "httpStatus": refined_diagnostics.get("httpStatus"),
                        "rawPreview": refined_diagnostics.get("rawPreview") or "",
                        "errors": refinement_errors,
                        "warnings": refinement_warnings,
                    },
                }
                warnings.append("segmentation_refined_second_pass")
            else:
                warnings.append("segmentation_refinement_not_applied_due_to_errors")

    if segmentation_debug.get("suspiciousEqualChunking"):
        warnings.append("segmentation_suspicious_equal_chunks")

    still_coarse, still_coarse_reasons = _needs_segmentation_refinement(
        segmentation_debug,
        normalized.get("audioDurationSec"),
        len(scenes),
    )
    still_coarse_after_refinement = bool(refinement_attempted and still_coarse)
    if still_coarse_after_refinement:
        reasons_suffix = f":{','.join(still_coarse_reasons)}" if still_coarse_reasons else ""
        warnings.append(f"segmentation_still_coarse_after_refinement{reasons_suffix}")

    scenes = _apply_final_scene_renumber_pass(scenes)
    logger.info("[COMFY PLAN] normalized scenes count=%s", len(scenes))

    parsed_errors = parsed.get("errors") if isinstance(parsed.get("errors"), list) else []
    all_errors = parsed_errors + errors

    plan_meta = (
        {
            **({"mode": normalized["mode"], "plannerMode": normalized["plannerMode"], "output": normalized["output"], "stylePreset": normalized["stylePreset"], "genre": normalized.get("genre"), "audioStoryMode": normalized["audioStoryMode"]}),
            **(parsed.get("planMeta") if isinstance(parsed.get("planMeta"), dict) else {}),
        }
    )
    plan_meta.update({
        "audioDurationSec": timing_debug.get("audioDurationSec"),
        "timelineDurationSec": timing_debug.get("timelineDurationSec"),
        "sceneDurationTotalSec": timing_debug.get("sceneDurationTotalSec"),
        "roleTypeByRole": normalized.get("roleTypeByRole") if isinstance(normalized.get("roleTypeByRole"), dict) else {},
        "roleSelectionSourceByRole": normalized.get("roleSelectionSourceByRole") if isinstance(normalized.get("roleSelectionSourceByRole"), dict) else {},
        "roleMode": normalized.get("roleMode") or "auto",
        "roleModeReason": normalized.get("roleModeReason") or "",
        "roleDominanceMode": normalized.get("roleDominanceMode") or "off",
        "roleDominanceModeReason": normalized.get("roleDominanceModeReason") or "",
        "roleDominanceApplied": bool(normalized.get("roleDominanceApplied")),
    })

    scene_refs_debug = _build_scene_refs_debug(scenes, normalized.get("refsByRole") or {})
    role_validation = _validate_role_distribution(
        scenes,
        role_mode=normalized.get("roleMode") or "auto",
        role_dominance_mode=normalized.get("roleDominanceMode") or "off",
        role_type_by_role=normalized.get("roleTypeByRole") if isinstance(normalized.get("roleTypeByRole"), dict) else {},
    )
    role_usage_by_scene = role_validation.get("roleUsageByScene") if isinstance(role_validation.get("roleUsageByScene"), list) else []
    dominant_role_by_scene = role_validation.get("dominantRoleByScene") if isinstance(role_validation.get("dominantRoleByScene"), dict) else {}
    dominant_role_type_by_scene = role_validation.get("dominantRoleTypeByScene") if isinstance(role_validation.get("dominantRoleTypeByScene"), dict) else {}
    scene_intent_by_scene = role_validation.get("sceneIntentByScene") if isinstance(role_validation.get("sceneIntentByScene"), list) else []
    scene_intent_confidence = role_validation.get("sceneIntentConfidence") if isinstance(role_validation.get("sceneIntentConfidence"), list) else []
    scene_intent_warnings = role_validation.get("sceneIntentWarnings") if isinstance(role_validation.get("sceneIntentWarnings"), list) else []
    scene_intent_diagnostics = role_validation.get("sceneIntentDiagnostics") if isinstance(role_validation.get("sceneIntentDiagnostics"), list) else []
    role_dominance_warnings = role_validation.get("roleDominanceWarnings") if isinstance(role_validation.get("roleDominanceWarnings"), list) else []
    role_validation_warnings = role_validation.get("roleValidationWarnings") if isinstance(role_validation.get("roleValidationWarnings"), list) else []
    role_validation_status = str(role_validation.get("roleValidationStatus") or "ok")
    plan_meta.update({
        "dominantRoleByScene": dominant_role_by_scene,
        "dominantRoleTypeByScene": dominant_role_type_by_scene,
        "roleDominanceWarnings": role_dominance_warnings,
        "roleValidationWarnings": role_validation_warnings,
        "roleValidationStatus": role_validation_status,
        "sceneIntentByScene": scene_intent_by_scene,
        "sceneIntentConfidence": scene_intent_confidence,
        "sceneIntentWarnings": scene_intent_warnings,
        "sceneIntentDiagnostics": scene_intent_diagnostics,
    })

    result = {
        "ok": len(all_errors) == 0,
        "planMeta": plan_meta,
        "globalContinuity": parsed.get("globalContinuity") if isinstance(parsed.get("globalContinuity"), (dict, str)) else {},
        "scenes": scenes,
        "warnings": list(dict.fromkeys([
            *((parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else [])),
            *warnings,
            *role_validation_warnings,
        ])),
        "errors": all_errors,
        "debug": {
            **(parsed.get("debug") if isinstance(parsed.get("debug"), dict) else {}),
            "debugSignature": debug_signature,
            "moduleFile": module_file,
            "requestedModel": diagnostics.get("requestedModel") or requested_model,
            "effectiveModel": diagnostics.get("effectiveModel") or requested_model,
            "httpStatus": diagnostics.get("httpStatus"),
            "rawPreview": diagnostics.get("rawPreview") or "",
            "normalizedPayload": normalized,
            "plannerMode": normalized["plannerMode"],
            "fallbackFrom": diagnostics.get("fallbackFrom"),
            "normalizedScenesCount": len(scenes),
            "timing": timing_debug,
            "segmentation": segmentation_debug,
            "initialSegmentationDebug": initial_segmentation_debug,
            "finalSegmentationDebug": segmentation_debug,
            "initialSceneCount": initial_scene_count,
            "finalSceneCount": len(scenes),
            "initialAverageSceneDurationSec": initial_segmentation_debug.get("averageSceneDurationSec"),
            "finalAverageSceneDurationSec": segmentation_debug.get("averageSceneDurationSec"),
            "refinementApplied": refinement_attempted,
            "refinementAttempted": refinement_attempted,
            "refinementSucceeded": refinement_succeeded,
            "refinementReason": ",".join(refinement_reasons) if refinement_reasons else None,
            "refinementPassCount": refinement_pass_count,
            "refinementErrors": refinement_errors,
            "refinementWarnings": refinement_warnings,
            "stillCoarseAfterRefinement": still_coarse_after_refinement,
            "stillCoarseReasons": still_coarse_reasons if still_coarse_after_refinement else [],
            "promptContractWarnings": prompt_contract_warnings,
            "availableRefsByRoleSummary": {role: len((normalized.get("refsByRole") or {}).get(role) or []) for role in COMFY_REF_ROLES},
            "referenceProfilesSummary": summarize_profiles(reference_profiles),
            "rolesGloballyAvailable": [role for role in COMFY_REF_ROLES if len((normalized.get("refsByRole") or {}).get(role) or []) > 0],
            "roleMode": normalized.get("roleMode") or "auto",
            "roleModeReason": normalized.get("roleModeReason") or "",
            "roleDominanceMode": normalized.get("roleDominanceMode") or "off",
            "roleDominanceModeReason": normalized.get("roleDominanceModeReason") or "",
            "roleDominanceApplied": bool(normalized.get("roleDominanceApplied")),
            "roleTypeByRole": normalized.get("roleTypeByRole") if isinstance(normalized.get("roleTypeByRole"), dict) else {},
            "roleSelectionSourceByRole": normalized.get("roleSelectionSourceByRole") if isinstance(normalized.get("roleSelectionSourceByRole"), dict) else {},
            "roleUsageByScene": role_usage_by_scene,
            "dominantRoleByScene": dominant_role_by_scene,
            "dominantRoleTypeByScene": dominant_role_type_by_scene,
            "roleDominanceWarnings": role_dominance_warnings,
            "roleValidationWarnings": role_validation_warnings,
            "roleValidationStatus": role_validation_status,
            "sceneIntentByScene": scene_intent_by_scene,
            "sceneIntentConfidence": scene_intent_confidence,
            "sceneIntentWarnings": scene_intent_warnings,
            "sceneIntentDiagnostics": scene_intent_diagnostics,
            "sceneRoleSelection": scene_refs_debug,
            "activeRolesByScene": {item.get("sceneId"): item.get("activeRoles") for item in scene_refs_debug},
        },
    }
    if timing_debug.get("normalizationApplied"):
        result["warnings"].append(str(timing_debug.get("normalizationReason") or "timeline_normalized_to_audio"))
    first_scene = scenes[0] if scenes else {}
    logger.info(
        "[%s] result ok=%s mode=%s output=%s style=%s audioStoryMode=%s scenes=%s warnings=%s errors=%s requestedModel=%s effectiveModel=%s httpStatus=%s firstSceneId=%s firstSceneTitle=%s",
        debug_signature,
        result["ok"],
        result.get("planMeta", {}).get("mode"),
        result.get("planMeta", {}).get("output"),
        result.get("planMeta", {}).get("stylePreset"),
        result.get("planMeta", {}).get("audioStoryMode"),
        len(scenes),
        len(result["warnings"]),
        len(result["errors"]),
        result["debug"].get("requestedModel"),
        result["debug"].get("effectiveModel"),
        result["debug"].get("httpStatus"),
        first_scene.get("sceneId") if isinstance(first_scene, dict) else None,
        first_scene.get("title") if isinstance(first_scene, dict) else None,
    )
    return result



def _build_scene_refs_debug(scenes: list[dict[str, Any]], refs_by_role: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    available_summary = {role: len(refs_by_role.get(role) or []) for role in COMFY_REF_ROLES}
    out: list[dict[str, Any]] = []
    for scene in scenes:
        ref_directives = scene.get("refDirectives") if isinstance(scene.get("refDirectives"), dict) else {}
        refs_used = scene.get("refsUsed") if isinstance(scene.get("refsUsed"), (list, dict)) else []
        available_roles = {role for role, count in available_summary.items() if count > 0}
        primary_role = str(scene.get("primaryRole") or "").strip()
        active_roles = _resolve_scene_active_roles(refs_used, ref_directives, available_roles, primary_role)
        secondary_roles_raw = scene.get("secondaryRoles")
        secondary_roles = [
            str(role or "").strip()
            for role in (secondary_roles_raw if isinstance(secondary_roles_raw, list) else [])
            if str(role or "").strip()
        ]
        out.append({
            "sceneId": str(scene.get("sceneId") or ""),
            "availableRefsByRoleSummary": available_summary,
            "refsUsed": refs_used,
            "refDirectives": ref_directives,
            "primaryRole": primary_role,
            "secondaryRoles": secondary_roles,
            "activeRoles": active_roles,
            "heroEntityId": scene.get("heroEntityId"),
            "supportEntityIds": scene.get("supportEntityIds") if isinstance(scene.get("supportEntityIds"), list) else [],
            "mustAppear": scene.get("mustAppear") if isinstance(scene.get("mustAppear"), list) else [],
            "mustNotAppear": scene.get("mustNotAppear") if isinstance(scene.get("mustNotAppear"), list) else [],
            "identityLock": bool(scene.get("identityLock")),
            "environmentLock": bool(scene.get("environmentLock")),
            "styleLock": bool(scene.get("styleLock")),
            "selectionReason": str(scene.get("roleSelectionReason") or "").strip() or "derived_from_refs_used_and_directives",
        })
    return out


def _build_role_usage_by_scene(
    scenes: list[dict[str, Any]],
    role_type_by_role: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    role_types = role_type_by_role if isinstance(role_type_by_role, dict) else {}
    out: list[dict[str, Any]] = []
    for scene in scenes:
        primary_role = str(scene.get("primaryRole") or "").strip()
        secondary_roles = [
            str(role or "").strip()
            for role in (scene.get("secondaryRoles") if isinstance(scene.get("secondaryRoles"), list) else [])
            if str(role or "").strip()
        ]
        active_roles = [
            str(role or "").strip()
            for role in (
                scene.get("activeRefs")
                if isinstance(scene.get("activeRefs"), list)
                else (scene.get("activeRoles") if isinstance(scene.get("activeRoles"), list) else [])
            )
            if str(role or "").strip()
        ]
        combined_roles: list[str] = []
        for role in [primary_role, *secondary_roles, *active_roles]:
            if role and role not in combined_roles:
                combined_roles.append(role)
        scene_role_types = {
            role: role_types.get(role, "unknown")
            for role in combined_roles
            if role
        }
        primary_role_type = scene_role_types.get(primary_role, "unknown") if primary_role else "unknown"
        has_hero = any(str(role_type or "").strip().lower() == "hero" for role_type in scene_role_types.values())
        has_antagonist = any(str(role_type or "").strip().lower() == "antagonist" for role_type in scene_role_types.values())
        has_support = any(str(role_type or "").strip().lower() == "support" for role_type in scene_role_types.values())
        out.append({
            "sceneId": str(scene.get("sceneId") or ""),
            "primaryRole": primary_role,
            "primaryRoleType": primary_role_type,
            "secondaryRoles": secondary_roles,
            "activeRoles": active_roles,
            "hasHero": has_hero,
            "hasAntagonist": has_antagonist,
            "hasSupport": has_support,
            "sceneRoleFunctionEstimate": _estimate_scene_role_function(
                scene,
                primary_role_type=primary_role_type,
                has_hero=has_hero,
                has_antagonist=has_antagonist,
                has_support=has_support,
            ),
            "roleTypeByRole": scene_role_types,
        })
    return out

def build_comfy_prompt_sync_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are a prompt adaptation engine for visual generation. Return strict JSON only.\n"
        "Fields: ok, translatedPrompt, normalizedPrompt, debug, error.\n"
        "Rules:\n"
        "- sourceLang and targetLang are mandatory.\n"
        "- Convert source text into model-ready prompt in target language.\n"
        "- Preserve story meaning, style cues, camera and motion intent.\n"
        "- Keep concise, no explanations, no markdown, no quotes wrappers.\n"
        "- If promptType=image: prioritize visual composition, subject, light, lens/camera if present.\n"
        "- If promptType=video: preserve motion, timing, camera movement, atmosphere beats.\n"
        f"INPUT={json.dumps(payload, ensure_ascii=False)}"
    )


def _extract_text_from_response(resp: dict[str, Any]) -> str:
    return _extract_text(resp if isinstance(resp, dict) else {})


def run_comfy_prompt_sync(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    source_text = str(data.get("sourceText") or "").strip()
    source_lang = str(data.get("sourceLang") or "ru").strip().lower()
    target_lang = str(data.get("targetLang") or "en").strip().lower()
    prompt_type = str(data.get("promptType") or "image").strip().lower()
    if prompt_type not in {"image", "video"}:
        prompt_type = "image"

    if not source_text:
        return {"ok": False, "translatedPrompt": "", "normalizedPrompt": "", "error": "empty_source_text", "debug": {}}

    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        return {"ok": False, "translatedPrompt": "", "normalizedPrompt": "", "error": "GEMINI_API_KEY missing", "debug": {}}

    normalized_payload = {
        "sourceText": source_text,
        "sourceLang": source_lang,
        "targetLang": target_lang,
        "promptType": prompt_type,
        "sceneContext": data.get("sceneContext") if isinstance(data.get("sceneContext"), dict) else {},
        "stylePreset": str(data.get("stylePreset") or "").strip(),
        "mode": str(data.get("mode") or "").strip(),
    }

    body = {
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        "contents": [{"role": "user", "parts": [{"text": build_comfy_prompt_sync_prompt(normalized_payload)}]}],
    }
    model = "gemini-2.5-flash"
    resp = post_generate_content(api_key, model, body, timeout=90)
    if isinstance(resp, dict) and resp.get("__http_error__"):
        return {
            "ok": False,
            "translatedPrompt": "",
            "normalizedPrompt": "",
            "error": f"gemini_http_error:{resp.get('status')}",
            "debug": {"status": resp.get("status"), "raw": str(resp.get("text") or "")[:1000]},
        }

    raw = _extract_text_from_response(resp)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "translatedPrompt": "",
            "normalizedPrompt": "",
            "error": "gemini_invalid_json",
            "debug": {"raw": raw[:1200]},
        }

    translated = str(parsed.get("translatedPrompt") or parsed.get("normalizedPrompt") or "").strip()
    normalized_prompt = str(parsed.get("normalizedPrompt") or translated).strip()
    if not translated:
        return {
            "ok": False,
            "translatedPrompt": "",
            "normalizedPrompt": "",
            "error": "empty_translated_prompt",
            "debug": {"raw": raw[:1200], "parsed": parsed},
        }

    return {
        "ok": bool(parsed.get("ok", True)),
        "translatedPrompt": translated,
        "normalizedPrompt": normalized_prompt,
        "error": str(parsed.get("error") or "").strip() or None,
        "debug": parsed.get("debug") if isinstance(parsed.get("debug"), dict) else {},
    }
