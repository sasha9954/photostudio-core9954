from __future__ import annotations

import json
import hashlib
import math
import re
from typing import Any, Iterable

from app.engine.gemini_rest import post_generate_content
from app.engine.prompt_polish_policies import (
    build_ia2v_readability_clauses,
    clean_negative_prompt_artifacts,
)
from app.engine.scenario_stage_timeout_policy import (
    get_scenario_stage_timeout,
    is_timeout_error,
    scenario_timeout_policy_name,
)
from app.engine.scenario_story_guidance import story_guidance_to_notes_list
from app.engine.video_capability_canon import (
    DEFAULT_VIDEO_MODEL_ID,
    build_capability_diagnostics_summary,
    get_capability_rules_source_version,
    get_first_last_pairing_rules,
    get_lipsync_rules,
    get_video_model_capability_profile,
)

SCENE_PROMPTS_PROMPT_VERSION = "scene_prompts_v1.1"
ALLOWED_ROUTES = {"i2v", "ia2v", "first_last"}

_GLOBAL_NEGATIVE_PROMPT = (
    "identity drift, outfit drift, lighting/world drift, unstable anatomy, extra limbs, surreal deformation, chaotic camera, layout change"
)

_LIP_SYNC_NEGATIVE_PROMPT = (
    "hidden mouth, unreadable lips, mouth fully obscured for long, face fully turned away from readability, severe identity drift, duplicate main subject, severe facial deformation"
)
_SCENES_CORE_NEGATIVE_PROMPT = (
    "empty walking, no-action scene, static camera, generic city shot, "
    "lifeless environment, random motion, broken anatomy"
)
_IA2V_LIP_SYNC_NEGATIVE_CANON = _LIP_SYNC_NEGATIVE_PROMPT
_IA2V_VIDEO_PROMPT_CANON = (
    "Use the uploaded image as the exact first frame and identity anchor. "
    "A performance shot of the same performer singing an emotional line. "
    "Clear expressive lip sync, natural jaw motion, subtle cheek and throat effort, and readable emotional expression are mandatory. "
    "Allow expressive but controlled gestures with smooth grounded motion in shoulders, torso, head, neck, breath tension, slight lean, and controlled weight shift. "
    "Hands may emphasize phrases when visible, but no jerky or chaotic dance-like motion unless explicitly requested by story. "
    "The face and mouth remain readable and important. "
    "Framing is flexible from tight close-up to full body as long as mouth readability, emotion readability, and identity clarity are preserved. "
    "Camera may remain frontal, gently drift sideways, or perform a slow restrained partial orbit around the performer when the emotional beat supports it. "
    "Slow arc moves and occasional slow 180-feel are allowed, and in stronger scenes a stylized partial 270-feel may be used when still smooth and controlled. "
    "Keep motion cinematic, restrained, and LTX-safe: no fast spins, no whip motion, and no chaotic handheld. "
    "In intimate beats prefer softer camera behavior. Singer may occasionally turn gaze toward the moving camera and continue singing naturally. "
    "If visible, background should show natural low-level life and ambient movement rather than frozen stillness; keep it subtle, secondary, and never stealing focus from singer."
)

_FIRST_LAST_NEGATIVE_PROMPT = (
    "camera drift, zoom spikes, chaotic reframing, body-axis jump, step, crouch, bow, torso dip, large arm action, spin, added actors, layout change, temporal instability, identity drift, outfit drift, finger choreography near face, wearable-touch micro choreography"
)
_IDENTITY_WARDROBE_NEGATIVE = (
    "different person, different face, changed face, changed body type, changed silhouette, different outfit, hairstyle drift, age drift, body proportion drift"
)
_GLOBAL_HERO_IDENTITY_LOCK = (
    "GLOBAL HERO IDENTITY LOCK: Keep the same current performer identity in every scene. Preserve face identity, age impression, body proportions, hairstyle, clothing silhouette, outfit family and overall look from the current connected reference."
)
_BODY_CONTINUITY_LOCK = (
    "BODY CONTINUITY: Keep the same body type, proportions and silhouette from the current reference; avoid body-shape drift."
)
_WARDROBE_CONTINUITY_LOCK = (
    "WARDROBE CONTINUITY: Keep outfit continuity from the current reference when present; do not introduce unrelated wardrobe identity drift."
)

_GLOBAL_PROMPT_RULES = [
    "Preserve hero identity, world anchor, style family, and realistic lighting continuity across all scenes.",
    "Preserve current world continuity, season continuity, weather continuity, and environment family from the established package. Do not introduce a different season or contradictory weather.",
    "Continuity lock is strict unless story explicitly changes it: keep same face/identity, body type/age impression, hair/facial hair, canonical outfit/accessories, object identity/ownership, world family/location family, lighting family, weather, season, and time-of-day.",
    "Keep prompts short, production-friendly, and route-aware; one clear action + one clear camera idea per video prompt.",
    "Respect wardrobe continuity when current input/story locks wardrobe; do not invent wardrobe progression defaults unless explicitly provided by current story/refs.",
    "Enforce LTX-safe motion and anatomy-safe constraints for all routes.",
    "Differentiate each scene from adjacent scenes in shot purpose, composition, and subject emphasis.",
]
_CHARACTER_STYLE_LEAK_TOKENS = (
    "same performer",
    "same man",
    "same person",
    "woman",
    "man",
    "person",
    "age",
    "hair",
    "hairstyle",
    "face",
    "body",
    "clothing",
    "outfit",
    "top",
    "jacket",
    "female",
    "girl",
    "lady",
    "dress",
    "crop top",
    "cropped top",
    "open neckline",
    "bust",
    "hips",
    "jewelry",
)
_IDENTITY_REFERENCE_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bshow the same (woman|man|person|performer)\b"),
    re.compile(r"(?i)\bprovided character_1 reference\b"),
    re.compile(r"(?i)\bsame (woman|man|performer) as the provided\b"),
)
_MALE_CONFLICT_STALE_TERMS: tuple[str, ...] = (
    "same woman",
    "woman's",
    "women",
    "light linen dress",
    "beige cropped sleeveless top",
    "cropped top",
    "crop top",
    "open neckline",
    "crop length",
    "bust/hips",
    "woman",
    "female",
    "feminine",
    "girl",
    "girl's",
    "lady",
    "lady's",
    "heroine",
    "her",
    "she",
    "dress",
    "bust",
    "hips",
)
_MALE_STALE_VALIDATION_TERMS: tuple[str, ...] = (
    "woman",
    "woman's",
    "female",
    "girl",
    "lady",
    "heroine",
    "her",
    "she",
    "dress",
    "cropped top",
    "crop top",
    "open neckline",
    "bust",
    "hips",
)
_LIP_SYNC_ONLY_I2V_VIOLATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bcharacter_1\b.{0,40}\b(main|primary)\b"),
    re.compile(r"(?i)\bsame current performer\b"),
    re.compile(r"(?i)\bsame character_1\b"),
    re.compile(r"(?i)\blip[- ]?sync\b"),
    re.compile(r"(?i)\bmouth close[- ]?up\b"),
)
_LIP_SYNC_ONLY_I2V_IDENTITY_ANCHOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bshow the same\b"),
    re.compile(r"(?i)\bprovided character_1 reference\b"),
    re.compile(r"(?i)\bpreserve the same face\b"),
    re.compile(r"(?i)\bsame performer\b"),
    re.compile(r"(?i)\bsame character_1\b"),
    re.compile(r"(?i)\bmouth visible\b"),
    re.compile(r"(?i)\blip[- ]?sync\b"),
    re.compile(r"(?i)\bperformer-first\b"),
)
_STALE_IDENTITY_TERMS = {
    "same woman",
    "woman",
    "woman's",
    "women",
    "female",
    "feminine",
    "girl",
    "girl's",
    "lady",
    "lady's",
    "heroine",
    "her",
    "she",
}
_STALE_WARDROBE_TERMS = {
    "light linen dress",
    "dress",
    "beige cropped sleeveless top",
    "cropped top",
    "crop top",
    "open neckline",
    "crop length",
    "bust/hips",
    "bust",
    "hips",
}

_NEGATIVE_LEAK_TOKENS = (
    "low quality",
    "blurry",
    "worst quality",
    "distorted features",
    "morphing",
    "flickering",
    "extra limbs",
    "unrealistic physics",
    "neon",
    "club lighting",
    "warehouse",
    "distorted anatomy",
    "bad quality",
    "deformed",
)
_STALE_WORLD_TOKENS = ("apartment", "cassette", "stale wardrobe token")
_EXPLICIT_NEGATIVE_MARKERS = (
    "[negative:",
    "(negative:",
    "negative:",
    "avoid:",
    "do not show:",
)
_IA2V_POSITIVE_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsilent internal scream\b", re.IGNORECASE),
    re.compile(r"\bsilent scream\b", re.IGNORECASE),
    re.compile(r"\bsilent pain\b", re.IGNORECASE),
    re.compile(r"\bsilent emotional beat\b", re.IGNORECASE),
    re.compile(r"\bno mouth[- ]?sync(?:ing)?\b", re.IGNORECASE),
    re.compile(r"\bwithout mouth[- ]?sync\b", re.IGNORECASE),
    re.compile(r"\bno lip movement\b", re.IGNORECASE),
    re.compile(r"\bnot singing\b", re.IGNORECASE),
    re.compile(r"\bmouth closed\b", re.IGNORECASE),
    re.compile(r"\bclosed mouth\b", re.IGNORECASE),
    re.compile(r"\bsilent face\b", re.IGNORECASE),
    re.compile(r"\binternal anguish without speech\b", re.IGNORECASE),
    re.compile(r"\bhand choreography\b", re.IGNORECASE),
    re.compile(r"\btorso pulse\b", re.IGNORECASE),
    re.compile(r"\bstronger hand language\b", re.IGNORECASE),
    re.compile(r"\bpouring action as main focus\b", re.IGNORECASE),
    re.compile(r"\bhands trembling as main mechanic\b", re.IGNORECASE),
    re.compile(r"\bno multistep prop mechanics\b", re.IGNORECASE),
    re.compile(r"\bno action-heavy choreography\b", re.IGNORECASE),
)
_IA2V_ANTI_LIPSYNC_NEGATIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmouth[- ]?sync(?:ing)?\b", re.IGNORECASE),
    re.compile(r"\blip[- ]?sync(?:ing)?\b", re.IGNORECASE),
    re.compile(r"\bsinging\b", re.IGNORECASE),
    re.compile(r"\bsinger\b", re.IGNORECASE),
    re.compile(r"\bvocal performance\b", re.IGNORECASE),
    re.compile(r"\blip movement\b", re.IGNORECASE),
    re.compile(r"\bjaw motion\b", re.IGNORECASE),
    re.compile(r"\bnot singing\b", re.IGNORECASE),
    re.compile(r"\bno lip movement\b", re.IGNORECASE),
    re.compile(r"\bno mouth[- ]?sync(?:ing)?\b", re.IGNORECASE),
    re.compile(r"\bno lip[- ]?sync\b", re.IGNORECASE),
    re.compile(r"\bmouth not synchronized\b", re.IGNORECASE),
    re.compile(r"\bsilent face\b", re.IGNORECASE),
    re.compile(r"\bclosed mouth\b", re.IGNORECASE),
)
FIRST_LAST_MODES = {
    "push_in_emotional",
    "pull_back_release",
    "small_side_arc",
    "reveal_face_from_shadow",
    "foreground_parallax",
    "camera_settle",
    "visibility_reveal",
}
SAFE_MOTION_CANON = (
    "slow walk / steady transit",
    "head turn",
    "gaze shift",
    "shoulder drop",
    "exhale / breath release",
    "weight shift",
    "controlled sway",
    "stillness with atmosphere motion",
    "subtle upper-body performance",
    "steady stare / direct gaze",
    "simple body reorientation",
    "camera push-in",
    "camera pull-back",
    "gentle lateral tracking",
    "small parallax / small arc around mostly stable subject",
)
I2V_MOTION_FAMILIES = {
    "push_in_follow",
    "side_tracking_walk",
    "look_reveal_follow",
    "baseline_forward_walk",
    "tension_head_turn",
    "pull_back_release",
}
_OWNERSHIP_ROLE_MAP = {
    "main": "character_1",
    "support": "character_2",
    "antagonist": "character_3",
    "shared": "shared",
    "world": "environment",
}
_BINDING_TYPES = {"carried", "worn", "held", "pocketed", "nearby", "environment"}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _append_prompt_clause(base: str, clause: str) -> str:
    text = str(base or "").strip()
    part = str(clause or "").strip()
    if not part:
        return text
    if part.lower() in text.lower():
        return text
    if not text:
        return part
    return f"{text.rstrip('. ')}. {part}"


def _text_mentions_role(text: str, role: str) -> bool:
    body = str(text or "").strip().lower()
    token = str(role or "").strip().lower()
    if not body or not token:
        return False
    return bool(re.search(rf"\b{re.escape(token)}\b", body))


def _shared_space_enforcement_clause(must_be_visible_roles: list[str]) -> str:
    roles = [str(role).strip() for role in must_be_visible_roles if str(role).strip()]
    if not roles:
        return "All required visible characters remain present in the same shared scene space."
    role_list = ", ".join(roles)
    return (
        f"Required visible cast in the same shared scene space: {role_list}. "
        "Visual focus may dominate, but every required role remains visibly present in-frame "
        "(background, edge, partial profile, shoulder, silhouette, reflection, or nearby presence)."
    )


def _coerce_speaker_confidence(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value if isinstance(value, int) or math.isfinite(value) else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return text
    if not math.isfinite(parsed):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _compact_prompt_payload(value: Any) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = _compact_prompt_payload(item)
            if cleaned in (None, "", [], {}):
                continue
            compact[str(key)] = cleaned
        return compact
    if isinstance(value, list):
        compact_list: list[Any] = []
        for item in value:
            cleaned = _compact_prompt_payload(item)
            if cleaned in (None, "", [], {}):
                continue
            compact_list.append(cleaned)
        return compact_list
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_ref_meta(meta: Any) -> dict[str, str]:
    row = _safe_dict(meta)
    ownership_role = str(row.get("ownershipRole") or row.get("ownership_role") or "auto").strip().lower() or "auto"
    ownership_mapped = str(row.get("ownershipRoleMapped") or row.get("ownership_role_mapped") or "").strip().lower()
    if ownership_mapped not in {"character_1", "character_2", "character_3", "shared", "environment"}:
        ownership_mapped = _OWNERSHIP_ROLE_MAP.get(ownership_role, "")
    binding_type = str(row.get("bindingType") or row.get("binding_type") or "nearby").strip().lower() or "nearby"
    if binding_type not in _BINDING_TYPES:
        binding_type = "nearby"
    return {
        "ownershipRole": ownership_role,
        "ownershipRoleMapped": ownership_mapped,
        "bindingType": binding_type,
    }


def _build_ref_binding_inventory(refs_inventory: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for key, value in refs_inventory.items():
        row = _safe_dict(value)
        meta = _normalize_ref_meta(row.get("meta"))
        if not meta["ownershipRoleMapped"] and meta["bindingType"] == "nearby":
            continue
        out.append(
            {
                "ref_id": str(key),
                "ownershipRoleMapped": meta["ownershipRoleMapped"],
                "bindingType": meta["bindingType"],
            }
        )
    return out[:16]


def _binding_prompt_clause(primary_role: str, ownership_binding_inventory: list[dict[str, str]]) -> str:
    role = str(primary_role or "").strip().lower()
    for item in ownership_binding_inventory:
        owner = str(_safe_dict(item).get("ownershipRoleMapped") or "").strip().lower()
        binding = str(_safe_dict(item).get("bindingType") or "").strip().lower()
        if role and owner and owner != role:
            continue
        if binding == "carried":
            return " Keep the same owner-bound carried object close to body; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
        if binding == "held":
            return (
                " Keep the same owner-bound held object across transit/evasion/release beats with readable handling only; "
                "it is not a replaceable random prop and one hand/handling attention stays committed, shaping posture, pace, and route decisions even off center."
            )
        if binding == "worn":
            return " Preserve worn-object silhouette continuity; treat it as look anchor, not choreography driver."
        if binding == "pocketed":
            return " Pocketed owner-bound object may stay implicit (not always visible) but continuity must hold."
        if binding == "nearby":
            return " Keep owner-bound object nearby/within reach when scene logic allows."
        if binding == "environment":
            return " Treat bound object as environment anchor in local scene, not hand choreography prop."
    return ""




def _is_owner_carried_active_scene(scene_plan_row: dict[str, Any], role_row: dict[str, Any], ownership_binding_inventory: list[dict[str, str]]) -> bool:
    primary_role = str(role_row.get("primary_role") or scene_plan_row.get("primary_role") or "").strip().lower()
    if not primary_role:
        return False
    active_roles = {str(v).strip().lower() for v in _safe_list(role_row.get("active_roles") or scene_plan_row.get("active_roles")) if str(v).strip()}
    if "props" not in active_roles:
        return False
    for item in ownership_binding_inventory:
        owner = str(_safe_dict(item).get("ownershipRoleMapped") or "").strip().lower()
        binding = str(_safe_dict(item).get("bindingType") or "").strip().lower()
        if owner == primary_role and binding == "carried":
            return True
    return False


def _is_owner_held_active_scene(scene_plan_row: dict[str, Any], role_row: dict[str, Any], ownership_binding_inventory: list[dict[str, str]]) -> bool:
    primary_role = str(role_row.get("primary_role") or scene_plan_row.get("primary_role") or "").strip().lower()
    if not primary_role:
        return False
    active_roles = {str(v).strip().lower() for v in _safe_list(role_row.get("active_roles") or scene_plan_row.get("active_roles")) if str(v).strip()}
    if "props" not in active_roles:
        return False
    for item in ownership_binding_inventory:
        owner = str(_safe_dict(item).get("ownershipRoleMapped") or "").strip().lower()
        binding = str(_safe_dict(item).get("bindingType") or "").strip().lower()
        if owner == primary_role and binding == "held":
            return True
    return False


def _resolve_active_video_model_id(package: dict[str, Any]) -> str:
    input_pkg = _safe_dict(package.get("input"))
    for key in ("video_model", "video_model_id", "model_id"):
        value = str(input_pkg.get(key) or "").strip().lower()
        if value:
            return value
    return DEFAULT_VIDEO_MODEL_ID


def _round3(value: Any) -> float:
    try:
        return round(float(value), 3)
    except Exception:
        return 0.0


def _extract_json_obj(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        first_obj, last_obj = raw.find("{"), raw.rfind("}")
        if first_obj >= 0 and last_obj > first_obj:
            try:
                return json.loads(raw[first_obj : last_obj + 1])
            except Exception:
                pass
        first_arr, last_arr = raw.find("["), raw.rfind("]")
        if first_arr >= 0 and last_arr > first_arr:
            try:
                return json.loads(raw[first_arr : last_arr + 1])
            except Exception:
                return {}
    return {}


def _strip_json_code_fences(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, flags=re.IGNORECASE)
    return fenced.strip()


def _preview_payload(value: Any, *, max_len: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value or "")
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:max_len]


def _coerce_prompt_notes(notes: Any) -> dict[str, Any]:
    if isinstance(notes, dict):
        return {str(k): v for k, v in notes.items()}
    if isinstance(notes, list):
        cleaned = [str(v).strip() for v in notes if str(v or "").strip()]
        return {"notes": cleaned} if cleaned else {}
    if isinstance(notes, str):
        cleaned = str(notes).strip()
        return {"notes": [cleaned]} if cleaned else {}
    return {}


def _segment_has_transition_payload(segment: dict[str, Any]) -> bool:
    row = _safe_dict(segment)
    route = str(row.get("route") or "").strip().lower()
    notes = _safe_dict(row.get("prompt_notes"))
    transition = _safe_dict(notes.get("transition"))
    start = str(
        transition.get("start_state")
        or notes.get("start_state")
        or row.get("first_frame_prompt")
        or row.get("start_image_prompt")
        or ""
    ).strip()
    end = str(
        transition.get("end_state")
        or notes.get("end_state")
        or row.get("last_frame_prompt")
        or row.get("end_image_prompt")
        or ""
    ).strip()
    if route != "first_last":
        return True
    return bool(start and end)


def _coerce_scene_prompts_payload(raw: Any) -> dict[str, Any]:
    data = _safe_dict(raw)
    if isinstance(raw, list):
        return {"scenes": _safe_list(raw)}
    scenes = _safe_list(data.get("scenes"))
    if scenes:
        return {"scenes": scenes, "global_prompt_rules": _safe_list(data.get("global_prompt_rules"))}
    for key in ("result", "data", "output"):
        nested = _safe_dict(data.get(key))
        nested_scenes = _safe_list(nested.get("scenes"))
        if nested_scenes:
            return {"scenes": nested_scenes, "global_prompt_rules": _safe_list(data.get("global_prompt_rules")) or _safe_list(nested.get("global_prompt_rules"))}
    return {"scenes": [], "global_prompt_rules": _safe_list(data.get("global_prompt_rules"))}


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
    if not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part.get("text") or "")
    return "\n".join(chunks).strip()


def _build_scene_windows(audio_map: dict[str, Any]) -> list[dict[str, Any]]:
    # Transitional bridge input: scene_candidate_windows remains legacy until full segment_id-first PROMPTS flow.
    windows: list[dict[str, Any]] = []
    for idx, row_raw in enumerate(_safe_list(audio_map.get("scene_candidate_windows")), start=1):
        row = _safe_dict(row_raw)
        t0 = _round3(row.get("t0"))
        t1 = _round3(row.get("t1"))
        if t1 <= t0:
            continue
        windows.append(
            {
                "scene_id": str(row.get("id") or f"sc_{idx}"),
                "t0": t0,
                "t1": t1,
                "duration_sec": _round3(row.get("duration_sec") or (t1 - t0)),
                "phrase_text": str(row.get("phrase_text") or "").strip(),
                "scene_function": str(row.get("scene_function") or "").strip(),
                "energy": str(row.get("energy") or "").strip(),
            }
        )
    return windows


def _build_scene_role_lookup(role_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    scene_casting = _safe_list(role_plan.get("scene_casting"))
    if scene_casting:
        for row_raw in scene_casting:
            row = _safe_dict(row_raw)
            segment_id = str(row.get("segment_id") or "").strip()
            if segment_id:
                primary_role = str(row.get("primary_role") or "").strip()
                secondary_roles = [str(role).strip() for role in _safe_list(row.get("secondary_roles")) if str(role).strip()]
                lookup[segment_id] = {
                    "scene_id": segment_id,
                    "segment_id": segment_id,
                    "primary_role": primary_role,
                    "secondary_roles": secondary_roles,
                    "active_roles": list(dict.fromkeys([primary_role, *secondary_roles])),
                    "scene_presence_mode": str(row.get("presence_mode") or "").strip(),
                    "presence_weight": str(row.get("presence_weight") or "").strip(),
                    "performance_focus": str(row.get("performance_focus") or "").strip(),
                }
        return lookup
    for row_raw in _safe_list(role_plan.get("scene_roles")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            lookup[scene_id] = row
    return lookup


def _compact_join(parts: list[str], *, sep: str = "; ", max_len: int = 600) -> str:
    text = sep.join([p.strip() for p in parts if str(p or "").strip()])
    return text[:max_len]


def _build_identity_lock_summary(story_core: dict[str, Any]) -> str:
    identity_lock = _safe_dict(story_core.get("identity_lock"))
    direct_summary = str(identity_lock.get("summary") or "").strip()
    if direct_summary:
        return direct_summary[:600]

    hero = _safe_dict(identity_lock.get("hero"))
    name = str(hero.get("name") or "").strip()
    appearance = str(hero.get("appearance_notes") or "").strip()
    core_trait = str(hero.get("core_trait") or "").strip()

    parts: list[str] = []
    if name:
        parts.append(f"Hero: {name}")
    if appearance:
        parts.append(f"Appearance: {appearance}")
    if core_trait:
        parts.append(f"Core trait: {core_trait}")
    return _compact_join(parts, max_len=600)


def _build_world_lock_summary(story_core: dict[str, Any]) -> str:
    world_lock = _safe_dict(story_core.get("world_lock"))
    direct_summary = str(world_lock.get("summary") or "").strip()
    if direct_summary:
        return direct_summary[:600]

    setting = str(world_lock.get("setting") or "").strip()
    setting_description = str(world_lock.get("setting_description") or "").strip()
    rules = str(world_lock.get("rules") or "").strip()
    mood_and_tone = str(world_lock.get("mood_and_tone") or "").strip()
    social_mood = str(world_lock.get("social_mood") or "").strip()
    key_locations = ", ".join([str(v).strip() for v in _safe_list(world_lock.get("key_locations")) if str(v).strip()])
    key_themes = ", ".join([str(v).strip() for v in _safe_list(world_lock.get("key_themes")) if str(v).strip()])

    parts: list[str] = []
    if setting:
        parts.append(f"Setting: {setting}")
    if setting_description:
        parts.append(f"Setting details: {setting_description}")
    if rules:
        parts.append(f"World rules: {rules}")
    if social_mood:
        parts.append(f"Social mood: {social_mood}")
    if mood_and_tone:
        parts.append(f"Mood/tone: {mood_and_tone}")
    if key_locations:
        parts.append(f"Key locations: {key_locations}")
    if key_themes:
        parts.append(f"Key themes: {key_themes}")
    return _compact_join(parts, max_len=600)


def _build_style_lock_summary(story_core: dict[str, Any]) -> str:
    style_lock = _safe_dict(story_core.get("style_lock"))
    direct_summary = str(style_lock.get("summary") or "").strip()
    if direct_summary:
        return direct_summary[:600]

    visual_style = str(style_lock.get("visual_style") or "").strip()
    visual_style_tags = ", ".join([str(v).strip() for v in _safe_list(style_lock.get("visual_style_tags")) if str(v).strip()])
    visual_mood = str(style_lock.get("visual_mood") or "").strip()
    color_palette = str(style_lock.get("color_palette") or "").strip()
    lighting = str(style_lock.get("lighting") or "").strip()
    camera_work = str(style_lock.get("camera_work") or "").strip()
    mood_and_tone = str(style_lock.get("mood_and_tone") or style_lock.get("overall_tone") or "").strip()
    audio_style = str(style_lock.get("audio_style") or "").strip()
    has_negative_style = bool(style_lock.get("negative_prompts") or style_lock.get("negative_style_tags"))

    parts: list[str] = []
    if visual_style:
        parts.append(f"Visual style: {visual_style}")
    if visual_style_tags:
        parts.append(f"Style tags: {visual_style_tags}")
    if visual_mood:
        parts.append(f"Visual mood: {visual_mood}")
    if color_palette:
        parts.append(f"Palette: {color_palette}")
    if lighting:
        parts.append(f"Lighting: {lighting}")
    if camera_work:
        parts.append(f"Camera: {camera_work}")
    if mood_and_tone:
        parts.append(f"Tone: {mood_and_tone}")
    if audio_style:
        parts.append(f"Audio style: {audio_style}")
    if has_negative_style:
        parts.append("Respect negative style constraints")
    return _compact_join(parts, max_len=600)


def _build_human_subject_label(role_row: dict[str, Any], story_core: dict[str, Any], scene_plan_row: dict[str, Any]) -> str:
    hero = _safe_dict(_safe_dict(story_core.get("identity_lock")).get("hero"))
    hero_name = str(hero.get("name") or "").strip()
    if hero_name:
        return hero_name

    age_bracket = str(hero.get("age_bracket") or "").strip().lower()
    gender = str(hero.get("gender_presentation") or "").strip().lower()
    appearance = str(hero.get("appearance_notes") or "").strip()
    world_lock = _safe_dict(story_core.get("world_lock"))
    setting = str(world_lock.get("setting") or world_lock.get("setting_description") or "").strip()

    age_hint = "young" if "young" in age_bracket else ""
    gender_hint = ""
    if "female" in gender or "woman" in gender:
        gender_hint = "woman"
    elif "male" in gender or "man" in gender:
        gender_hint = "man"
    elif gender:
        gender_hint = "person"

    setting_token = ""
    setting_lower = setting.lower()
    for token in ["iranian", "persian", "arab", "european", "asian", "latin", "african"]:
        if token in setting_lower:
            setting_token = token
            break

    descriptor = " ".join([p for p in [age_hint, setting_token, gender_hint] if p]).strip()
    if descriptor:
        return f"a {descriptor}"

    if appearance:
        if "woman" in appearance.lower():
            return "a woman"
        if "man" in appearance.lower():
            return "a man"
        return "a protagonist with distinctive appearance"

    primary_role = str(role_row.get("primary_role") or scene_plan_row.get("primary_role") or "").strip()
    if primary_role and primary_role != "character_1":
        return primary_role

    if gender_hint:
        return f"{gender_hint} protagonist"
    return "the protagonist"


def _build_scene_anchor_bundle(
    *,
    package: dict[str, Any],
    story_core: dict[str, Any],
    role_row: dict[str, Any],
    scene_plan_row: dict[str, Any],
    world_continuity: dict[str, Any],
) -> dict[str, str]:
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    active_roles = [str(role).strip() for role in _safe_list(role_row.get("active_roles")) if str(role).strip()]
    hero_ref_hint = (
        "same character_1 from the current connected character_1 reference"
        if _safe_dict(refs_inventory.get("ref_character_1"))
        else ""
    )
    location_ref_hint = "same location from reference" if _safe_dict(refs_inventory.get("ref_location")) else ""

    identity_lock = _safe_dict(story_core.get("identity_lock"))
    hero_raw = identity_lock.get("hero")
    identity_parts: list[str] = [hero_ref_hint, "same hero identity continuity"]
    if isinstance(hero_raw, dict):
        hero = _safe_dict(hero_raw)
        age_band = str(hero.get("age_bracket") or "").strip()
        hair = str(hero.get("hair_signature") or hero.get("appearance_notes") or "").strip()
        outfit = str(hero.get("outfit_essentials") or "").strip()
        identity_parts.extend([age_band, hair, outfit])
    elif isinstance(hero_raw, str):
        hero_compact = " ".join(hero_raw.strip().split())[:140]
        if hero_compact:
            identity_parts.append(hero_compact)
    identity_anchor = ", ".join([part for part in identity_parts if part])[:220]

    world_lock = _safe_dict(story_core.get("world_lock"))
    style_lock = _safe_dict(story_core.get("style_lock"))
    environment = str(
        world_continuity.get("environment_family")
        or world_lock.get("setting")
        or world_lock.get("setting_description")
        or "same environment"
    ).strip()
    lighting_contract_anchor = _lighting_anchor_from_contract(world_continuity)
    lighting = str(style_lock.get("lighting") or "").strip() or lighting_contract_anchor
    world_parts = [f"{environment} world family"]
    if location_ref_hint:
        world_parts.append("same location reference continuity")
    world_anchor = ", ".join([part for part in world_parts if part])[:220]
    lighting_anchor = lighting[:160]

    route = str(scene_plan_row.get("route") or "i2v").strip().lower()
    continuity_anchor = f"{str(scene_plan_row.get('scene_function') or 'scene beat').strip()}, route={route}, active_roles={','.join(active_roles) or 'none'}"
    return {
        "identity_anchor": identity_anchor or "same hero identity continuity",
        "world_anchor": world_anchor or "same world continuity",
        "lighting_anchor": lighting_anchor or "same lighting family",
        "continuity_anchor": continuity_anchor[:240],
    }


def _enrich_prompt_with_anchor(prompt: str, identity_anchor: str, world_anchor: str) -> str:
    clean = str(prompt or "").strip()
    prefix = "; ".join([part for part in [identity_anchor, world_anchor] if part]).strip()
    if not prefix:
        return clean
    if prefix.lower() in clean.lower():
        return clean
    joined = f"{prefix}. {clean}" if clean else prefix
    return joined[:900]


def _trim_sentence(text: str, *, max_len: int = 220) -> str:
    clean = " ".join(str(text or "").strip().split())
    return clean[:max_len]


def _build_current_world_context_for_fallback(
    package: dict[str, Any],
    story_core: dict[str, Any],
    prompt_row: dict[str, Any],
    global_style_anchor: str,
) -> str:
    row = _safe_dict(prompt_row)
    core = _safe_dict(story_core) or _safe_dict(package.get("story_core"))
    identity_doctrine = _safe_dict(core.get("identity_doctrine"))
    world_lock = _safe_dict(core.get("world_lock"))
    style_lock = _safe_dict(core.get("style_lock"))

    raw_candidates = [
        str(row.get("scene_goal") or ""),
        str(row.get("background_story_evidence") or ""),
        str(row.get("narrative_function") or ""),
        str(row.get("photo_staging_goal") or ""),
        str(row.get("ltx_video_goal") or ""),
        str(identity_doctrine.get("world_doctrine") or ""),
        str(identity_doctrine.get("style_doctrine") or ""),
        str(world_lock.get("rule") or ""),
        str(style_lock.get("rule") or ""),
        str(global_style_anchor or ""),
    ]

    compact_parts: list[str] = []
    seen_normalized: set[str] = set()
    for candidate in raw_candidates:
        trimmed = _trim_sentence(candidate, max_len=120)
        if not trimmed:
            continue
        normalized = trimmed.lower()
        if normalized in seen_normalized:
            continue
        seen_normalized.add(normalized)
        compact_parts.append(trimmed.rstrip("."))
        if len(compact_parts) >= 2:
            break

    if not compact_parts:
        return "current grounded story world, coherent realistic atmosphere"
    return "; ".join(compact_parts)[:260]


def _lighting_anchor_from_contract(world_continuity: dict[str, Any]) -> str:
    lighting = _safe_dict(world_continuity.get("lighting_continuity"))
    tod = str(lighting.get("time_of_day_base") or "").replace("_", " ").strip()
    contrast = str(lighting.get("contrast_profile") or "").replace("_", " ").strip()
    shadows = str(lighting.get("shadow_behavior") or "").replace("_", " ").strip()
    practicals = ", ".join([str(item).replace("_", " ").strip() for item in _safe_list(lighting.get("practical_sources")) if str(item).strip()])
    parts = []
    if tod:
        parts.append(f"{tod} natural light")
    if contrast:
        parts.append(f"{contrast} contrast")
    if shadows:
        parts.append(shadows)
    if practicals:
        parts.append(f"stable practical sources ({practicals})")
    return "; ".join(parts)[:220] or "stable naturalistic light continuity"


def _is_high_motion_risk(scene_plan_row: dict[str, Any]) -> bool:
    risk = _safe_dict(scene_plan_row.get("motion_risk"))
    return any(
        str(risk.get(key) or "").strip().lower() == "high"
        for key in ("ltx_motion_risk", "finger_precision_risk", "prop_interaction_complexity", "face_occlusion_risk")
    )


def _detect_attached_prop_token(*texts: str) -> str:
    blob = " ".join([str(item or "").lower() for item in texts])
    for token in ("cap", "hat", "helmet", "glasses", "mask", "scarf", "headphones"):
        if token in blob:
            return token
    return ""


def _resolve_first_last_continuity_mode(scene_plan_row: dict[str, Any]) -> str:
    route = str(scene_plan_row.get("route") or "").strip().lower()
    scene_function = str(scene_plan_row.get("scene_function") or "").strip().lower()
    if route == "first_last" and scene_function in {"release", "afterimage"}:
        return "strict_echo"
    return "controlled_micro_transition"


def _resolve_first_last_semantic_beat(scene_plan_row: dict[str, Any]) -> str:
    scene_function = str(scene_plan_row.get("scene_function") or "").strip().lower()
    if scene_function == "release":
        return "dissipation_beat"
    if scene_function == "afterimage":
        return "residual_beat"
    return "transition_beat"


def _is_strict_echo_first_last_scene(scene_plan_row: dict[str, Any], notes: dict[str, Any] | None = None) -> bool:
    continuity_mode = str(_safe_dict(notes).get("continuity_mode") or "").strip().lower()
    if continuity_mode == "strict_echo":
        return True
    return _resolve_first_last_continuity_mode(scene_plan_row) == "strict_echo"


def _build_first_last_visual_delta(
    *,
    scene_plan_row: dict[str, Any],
    primary_role: str,
    attached_prop_token: str,
) -> tuple[str, str, str]:
    first_field = _trim_sentence(str(scene_plan_row.get("first_state") or "").strip(), max_len=180)
    last_field = _trim_sentence(str(scene_plan_row.get("last_state") or "").strip(), max_len=180)
    transition_action = _trim_sentence(str(scene_plan_row.get("transition_action") or "").strip(), max_len=180)
    scene_goal = _trim_sentence(str(scene_plan_row.get("scene_goal") or "").strip(), max_len=180)
    frame_description = _trim_sentence(str(scene_plan_row.get("frame_description") or "").strip(), max_len=180)
    motion_intent = _trim_sentence(str(scene_plan_row.get("motion_intent") or "").strip(), max_len=180)
    emotional_intent = _trim_sentence(str(scene_plan_row.get("emotional_intent") or "").strip(), max_len=180)

    visual_hints = ("head", "gaze", "eye", "hand", "brim", "face", "shoulder", "posture", "cap", "hat", "mask", "glasses")
    abstract_hints = ("emotion", "emotional", "mood", "tension", "defiant", "internal", "feeling")

    def _is_visual(text: str) -> bool:
        low = text.lower()
        if not low:
            return False
        if any(token in low for token in visual_hints):
            return True
        return not any(token in low for token in abstract_hints)

    source_fields = [first_field, frame_description, transition_action, scene_goal, motion_intent]
    first_state = next((text for text in source_fields if _is_visual(text)), "") or (
        f"{primary_role} holds the exact start pose before the controlled shift"
    )

    prop_stability = ""
    if attached_prop_token:
        if attached_prop_token in ("cap", "hat", "helmet"):
            prop_stability = f"{attached_prop_token} remains worn on head"
        elif attached_prop_token in ("glasses", "mask"):
            prop_stability = f"{attached_prop_token} remains in place"
        else:
            prop_stability = f"{attached_prop_token} remains attached with no detachment"

    candidate_delta = next((text for text in [last_field, transition_action, scene_goal, frame_description, motion_intent] if _is_visual(text)), "")
    if not candidate_delta:
        candidate_delta = emotional_intent or "one small posture/gaze shift with camera settle"
    candidate_delta_low = candidate_delta.lower()
    if any(token in candidate_delta_low for token in ("finger", "brim", "pinch", "grip", "regrip", "tiny hand")):
        candidate_delta = "gaze lifts slightly while shoulder line relaxes and framing settles"
    delta_parts = [_trim_sentence(candidate_delta, max_len=180)]
    if prop_stability and attached_prop_token not in candidate_delta.lower():
        delta_parts.append(prop_stability)
    delta_phrase = " while ".join([part for part in delta_parts if part])
    last_state = _trim_sentence(delta_phrase, max_len=180)

    if prop_stability and attached_prop_token not in last_state.lower():
        last_state = _trim_sentence(f"{last_state}; {prop_stability}", max_len=180)
    if prop_stability and attached_prop_token not in delta_phrase.lower():
        delta_phrase = _trim_sentence(f"{delta_phrase} while {prop_stability}", max_len=180)
    return _trim_sentence(first_state, max_len=180), _trim_sentence(last_state, max_len=180), _trim_sentence(delta_phrase, max_len=180)


def _build_first_last_start_image_prompt(
    *,
    primary_role: str,
    scene_space: str,
    first_state: str,
    attached_prop_token: str,
) -> str:
    prop_clause = f", {attached_prop_token} stays worn in the same place" if attached_prop_token else ""
    return (
        f"Start frame still of {primary_role} in {scene_space}: {first_state}. "
        f"Keep same subject, same world, same wardrobe, same framing family, same perspective, same camera distance, same body line{prop_clause}."
    )


def _build_first_last_end_image_prompt(
    *,
    primary_role: str,
    scene_space: str,
    last_state: str,
    attached_prop_token: str,
) -> str:
    prop_clause = f", {attached_prop_token} remains worn in the same place" if attached_prop_token else ""
    return (
        f"End frame still of {primary_role} in {scene_space}, one subtle visible delta only: {last_state}. "
        f"Keep same subject, same world, same wardrobe, same framing family, same perspective, same camera distance, same body line{prop_clause}."
    )


def _build_first_last_negative_prompt(*, attached_prop_token: str) -> str:
    base = _FIRST_LAST_NEGATIVE_PROMPT
    if not attached_prop_token:
        return base
    return (
        f"{base}, detached {attached_prop_token}, floating {attached_prop_token}, {attached_prop_token} teleportation, {attached_prop_token} drift"
    )


def _build_first_last_prompt_pair(
    *,
    primary_role: str,
    scene_space: str,
    first_state: str,
    last_state: str,
    visual_delta: str,
    attached_prop_token: str,
    first_last_mode: str = "",
    scene_function: str = "",
    emotional_intent: str = "",
    continuity_mode: str = "controlled_micro_transition",
    semantic_beat: str = "transition_beat",
) -> tuple[str, str, str, str]:
    start_image_prompt = _build_first_last_start_image_prompt(
        primary_role=primary_role,
        scene_space=scene_space,
        first_state=_trim_sentence(first_state, max_len=180),
        attached_prop_token=attached_prop_token,
    )
    end_image_prompt = _build_first_last_end_image_prompt(
        primary_role=primary_role,
        scene_space=scene_space,
        last_state=_trim_sentence(last_state, max_len=180),
        attached_prop_token=attached_prop_token,
    )
    prop_clause = f" Keep {attached_prop_token} attached/worn with no drift or detachment." if attached_prop_token else ""
    clean_mode = first_last_mode if first_last_mode in FIRST_LAST_MODES else "camera_settle"
    camera_clause_map = {
        "push_in_emotional": "camera performs a smooth minimal push-in",
        "pull_back_release": "camera performs a smooth minimal pull-back",
        "small_side_arc": "camera performs a small controlled side arc",
        "reveal_face_from_shadow": "camera settles to slightly improve face visibility",
        "foreground_parallax": "camera allows subtle foreground parallax pass",
        "camera_settle": "camera settles with no perspective jump",
        "visibility_reveal": "camera reframes minimally to reveal visibility shift",
    }
    camera_clause = camera_clause_map.get(clean_mode, "camera settles with no perspective jump")
    scene_function_low = str(scene_function or "").strip().lower()
    continuity_mode_low = str(continuity_mode or "controlled_micro_transition").strip().lower()
    semantic_beat_low = str(semantic_beat or "transition_beat").strip().lower()
    release_clause = ""
    if continuity_mode_low == "strict_echo" and scene_function_low == "release":
        release_clause = (
            " Dissipation beat (strict_echo): fade-out release, tension exits body line, breath settles, and energy decays "
            "without geography change or new entity introduction."
        )
    elif continuity_mode_low == "strict_echo" and scene_function_low == "afterimage":
        release_clause = (
            " Residual beat (strict_echo): stillness holds, afterimage lingers as a trace, body line stabilizes, and final presence "
            "remains without introducing new motion idea."
        )
    elif "release" in scene_function_low:
        release_clause = " Readable release beat: tension exits body line and breath settles without geography change."
    elif "afterimage" in scene_function_low:
        release_clause = " Readable afterimage beat: lingering echo of previous tension remains while body line stabilizes."
    elif "callback" in scene_function_low:
        release_clause = " Readable callback beat: visual echo links back to opening motif with continuity-first framing."
    emotion_clause = f" Emotional register: {_trim_sentence(emotional_intent, max_len=120)}." if emotional_intent else ""
    continuity_clause = (
        " Anchor strategy: same global world family, echo continuity from previous scene, and state delta from previous scene; "
        "avoid local stage-label noise and avoid new entity invention."
        if continuity_mode_low == "strict_echo"
        else ""
    )
    positive_video_prompt = (
        f"Controlled first_last transition in {scene_space}: {camera_clause} while {primary_role} keeps a broad readable state shift, {_trim_sentence(visual_delta, max_len=180)}. "
        "Keep same subject/world/outfit/shot family, same framing family, smooth continuity, no abrupt zoom spikes, no large perspective jump, no fine-motor prop choreography. "
        f"Only one subtle visible delta, with no added actors or layout change.{continuity_clause}{release_clause}{emotion_clause} Mode={continuity_mode_low}; beat={semantic_beat_low}.{prop_clause}"
    )
    negative_video_prompt = _build_first_last_negative_prompt(attached_prop_token=attached_prop_token)
    return start_image_prompt[:650], end_image_prompt[:700], positive_video_prompt[:850], negative_video_prompt




def _resolve_prompt_interface_contract(story_core: dict[str, Any]) -> dict[str, Any]:
    core = _safe_dict(story_core)
    root_contract = _safe_dict(core.get("prompt_interface_contract"))
    if root_contract:
        return root_contract
    return _safe_dict(_safe_dict(core.get("story_core_v1")).get("prompt_interface_contract"))


def _build_compact_context(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    compiled_contract = _safe_dict(role_plan.get("compiled_contract"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    prompt_interface_contract = _resolve_prompt_interface_contract(story_core)

    scene_windows = _build_scene_windows(audio_map)
    role_lookup = _build_scene_role_lookup(role_plan)
    model_id = _resolve_active_video_model_id(package)
    route_profiles = {
        route: get_video_model_capability_profile(model_id, route)
        for route in ("i2v", "ia2v", "first_last", "lipsync")
    }
    ownership_binding_inventory = _build_ref_binding_inventory(refs_inventory)

    compact_context = {
        "mode": "clip",
        "content_type": str(input_pkg.get("content_type") or ""),
        "format": str(input_pkg.get("format") or ""),
        "story_core": {
            "story_summary": str(story_core.get("story_summary") or "")[:1200],
            "opening_anchor": str(story_core.get("opening_anchor") or "")[:600],
            "ending_callback_rule": str(story_core.get("ending_callback_rule") or "")[:600],
            "global_arc": str(story_core.get("global_arc") or "")[:600],
            "identity_lock_summary": _build_identity_lock_summary(story_core),
            "world_lock_summary": _build_world_lock_summary(story_core),
            "style_lock_summary": _build_style_lock_summary(story_core),
            "prompt_interface_contract": {
                "visibility_mode": str(prompt_interface_contract.get("visibility_mode") or ""),
                "subject_presence_requirement": str(prompt_interface_contract.get("subject_presence_requirement") or ""),
                "must_be_visible": [
                    str(v).strip()
                    for v in _safe_list(prompt_interface_contract.get("must_be_visible"))
                    if str(v).strip()
                ],
                "may_be_offscreen": [
                    str(v).strip()
                    for v in _safe_list(prompt_interface_contract.get("may_be_offscreen"))
                    if str(v).strip()
                ],
            },
        },
        "audio_map": {
            "scene_windows": scene_windows,
            "sections": _safe_list(audio_map.get("sections")),
            "cut_policy": _safe_dict(audio_map.get("cut_policy")),
            "audio_dramaturgy": _safe_dict(audio_map.get("audio_dramaturgy")),
        },
        "role_plan": {
            "roles_version": str(role_plan.get("roles_version") or ""),
            "roster": _safe_list(role_plan.get("roster")),
            "scene_casting": _safe_list(role_plan.get("scene_casting")),
            "world_continuity": _safe_dict(story_core.get("world_lock")) or _safe_dict(role_plan.get("world_continuity")),
            "compiled_contract": {
                "global_contract": _safe_dict(compiled_contract.get("global_contract")),
                "scene_contracts": _safe_list(compiled_contract.get("scene_contracts")),
            },
            "scene_roles": [
                {
                    "scene_id": sid,
                    "primary_role": str(_safe_dict(role).get("primary_role") or ""),
                    "scene_presence_mode": str(_safe_dict(role).get("scene_presence_mode") or ""),
                    "performance_focus": bool(_safe_dict(role).get("performance_focus")),
                }
                for sid, role in role_lookup.items()
            ],
            "continuity_notes": story_guidance_to_notes_list(story_core.get("story_guidance"), max_items=8) or _safe_list(role_plan.get("continuity_notes")),
        },
        "scene_plan": {
            "route_mix_summary": _safe_dict(scene_plan.get("route_mix_summary")),
            "scenes": [
                {
                    "scene_id": str(_safe_dict(row).get("scene_id") or ""),
                    "t0": _round3(_safe_dict(row).get("t0")),
                    "t1": _round3(_safe_dict(row).get("t1")),
                    "duration_sec": _round3(_safe_dict(row).get("duration_sec")),
                    "scene_function": str(_safe_dict(row).get("scene_function") or ""),
                    "route": str(_safe_dict(row).get("route") or ""),
                    "route_reason": str(_safe_dict(row).get("route_reason") or ""),
                    "emotional_intent": str(_safe_dict(row).get("emotional_intent") or ""),
                    "motion_intent": str(_safe_dict(row).get("motion_intent") or ""),
                    "watchability_role": str(_safe_dict(row).get("watchability_role") or ""),
                    "shot_scale": str(_safe_dict(row).get("shot_scale") or ""),
                    "camera_intimacy": str(_safe_dict(row).get("camera_intimacy") or ""),
                    "performance_openness": str(_safe_dict(row).get("performance_openness") or ""),
                    "visual_event_type": str(_safe_dict(row).get("visual_event_type") or ""),
                    "repeat_variation_rule": str(_safe_dict(row).get("repeat_variation_rule") or ""),
                    "first_last_mode": str(_safe_dict(row).get("first_last_mode") or ""),
                    "motion_risk": _safe_dict(_safe_dict(row).get("motion_risk")),
                }
                for row in _safe_list(scene_plan.get("scenes"))
            ],
        },
        "ownership_binding_inventory": ownership_binding_inventory,
        "prompt_policy": {
            "ltx_safe_motion": True,
            "realism_required": True,
            "world_continuity_required": True,
            "identity_continuity_required": True,
        },
        "video_capability_canon": {
            "model_id": model_id,
            "capability_rules_source_version": get_capability_rules_source_version(),
            "route_profiles": route_profiles,
            "first_last_pairing_rules": get_first_last_pairing_rules(model_id),
            "lipsync_rules": get_lipsync_rules(model_id),
            "usage_policy": {
                "prefer_verified_safe_by_default": True,
                "experimental_is_opt_in_not_default": True,
                "blocked_patterns_must_be_filtered": True,
            },
        },
    }

    aux = {
        "scene_rows": _safe_list(scene_plan.get("scenes")),
        "role_lookup": role_lookup,
        "story_core": story_core,
        "world_continuity": _safe_dict(story_core.get("world_lock")) or _safe_dict(role_plan.get("world_continuity")),
        "ownership_binding_inventory": ownership_binding_inventory,
        "compiled_contract": compiled_contract,
        # Bridge markers: scene_candidate_windows/scene_id flows and compiled_contract are temporary transition paths.
        "uses_legacy_scene_candidate_windows_bridge": bool(scene_windows),
        "uses_legacy_compiled_contract_bridge": bool(compiled_contract),
    }
    return _compact_prompt_payload(compact_context), aux


def _build_scene_contract_lookup(compiled_contract: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(compiled_contract.get("scene_contracts")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            out[scene_id] = row
    return out, _safe_dict(compiled_contract.get("global_contract"))


def _presence_policy_clause(presence_policy: dict[str, Any]) -> str:
    policy = str(_safe_dict(presence_policy).get("presence_policy") or "").strip().upper()
    if policy == "STRICT":
        return "No extra visible people in frame; keep only required contract actors."
    if policy == "MINIMAL":
        return "Extras absent or extremely sparse; no identifiable secondary hero unless explicitly contracted."
    if policy == "ADDITIVE":
        return "Allow only anonymous atmospheric background presence; no second identifiable hero unless contract allows."
    return ""


def _append_compact_clauses(prompt: str, clauses: list[str], *, max_len: int = 900) -> str:
    text = str(prompt or "").strip()
    if not clauses:
        return text[:max_len]
    low = text.lower()
    for clause in clauses:
        clean_clause = str(clause or "").strip().rstrip(".")
        if not clean_clause:
            continue
        if clean_clause.lower() in low:
            continue
        text = f"{text.rstrip('. ')}. {clean_clause}".strip() if text else clean_clause
        low = text.lower()
    return text[:max_len]


def _scene_semantic_blob(scene: dict[str, Any]) -> str:
    parts = [
        str(scene.get("scene_function") or ""),
        str(scene.get("narrative_function") or ""),
        str(scene.get("scene_goal") or ""),
        str(scene.get("visual_focus_role") or ""),
        str(scene.get("subject_priority") or ""),
        str(scene.get("background_story_evidence") or ""),
    ]
    return " ".join(parts).lower()


def _build_prompt(context: dict[str, Any]) -> str:
    canon = _safe_dict(context.get("video_capability_canon"))
    route_profiles = _safe_dict(canon.get("route_profiles"))
    i2v_profile = _safe_dict(route_profiles.get("i2v"))
    first_last_profile = _safe_dict(route_profiles.get("first_last"))
    lipsync_profile = _safe_dict(route_profiles.get("lipsync"))
    i2v_safe = ", ".join([str(v).strip() for v in _safe_list(i2v_profile.get("verified_safe")) if str(v).strip()])
    i2v_blocked = ", ".join([str(v).strip() for v in _safe_list(i2v_profile.get("blocked")) if str(v).strip()])
    first_last_blocked = ", ".join([str(v).strip() for v in _safe_list(first_last_profile.get("blocked")) if str(v).strip()])
    lipsync_blocked = ", ".join([str(v).strip() for v in _safe_list(lipsync_profile.get("blocked")) if str(v).strip()])
    return (
        "You are SCENE PROMPTS stage for scenario pipeline.\\n"
        "Return STRICT JSON only. No markdown.\\n"
        "MODE is clip only.\\n"
        "Task: build planning-to-generation bridge prompts for later storyboard/render stages.\\n"
        "Do not access raw Scenario Director text directly; treat role_plan.scene_casting/roster as cast source (legacy compiled_contract optional fallback only).\\n"
        "Prompts are translation layer only: do not invent new plot geography beyond upstream story/role/scene contracts.\\n"
        "Do NOT produce render payloads or API calls.\\n"
        "For each scene from scene_plan, write route-aware photo_prompt and video_prompt with compact production language.\\n"
        "Use only CURRENT PACKAGE context in this request; do not reuse stale or previous package prompts.\\n"
        "Preserve identity/world/style continuity and realism.\\n"
        "Clip-mode principle: visual/emotional arc under music energy, not default literal travel plot.\\n"
        "Prompt text must be short, usable, and not overloaded.\\n"
        "Avoid unnecessary world/geography decoration (no forced urban/industrial/location labels unless explicitly grounded in inputs).\\n"
        "Use scene visual progression attributes (shot_scale, camera_intimacy, performance_openness, visual_event_type, repeat_variation_rule) to keep repeated phrases visually different.\\n"
        "Use lighting continuity contract as stable anchor and translate it to natural cinematic language, not numeric dump.\\n"
        "If motion_risk shows high complexity, simplify action wording: broad readable motion only, no tiny finger-sequence choreography.\\n"
        f"Video capability canon model={str(canon.get('model_id') or DEFAULT_VIDEO_MODEL_ID)} version={str(canon.get('capability_rules_source_version') or '')}.\\n"
        f"Use VERIFIED_SAFE defaults first: {i2v_safe}.\\n"
        "Experimental patterns are opt-in only and must not be default.\\n"
        f"Blocked i2v patterns (filter out): {i2v_blocked}.\\n"
        f"Blocked first_last patterns (filter out): {first_last_blocked}.\\n"
        f"Blocked lipsync patterns (filter out): {lipsync_blocked}.\\n"
        "Camera-led transitions are preferred over fine-motor body actions when either can express the same beat.\\n"
        "When wardrobe or worn-object anchors are present, preserve continuity and avoid default item-manipulation choreography unless explicitly requested by current input.\\n"
        "Use ownership_binding_inventory for owner/binding grammar: carried/held stronger owner continuity, worn silhouette continuity, pocketed/nearby lighter continuity, environment world-anchor behavior.\\n"
        "Do not randomly detach owner-bound carried/held objects from owner continuity.\\n"
        "Video prompts must be LTX-native, anatomy-safe, and motion-first.\\n"
        "Write prompts in natural cinematic English, present tense, one connected paragraph, chronological motion logic.\\n"
        "Describe what starts happening after the still image; do NOT mechanically re-describe all static elements already visible.\\n"
        "Keep one primary motion idea per scene and avoid contradictory instruction stacks.\\n"
        "Hard constraints must be compressed into a short safety tail at the end only.\\n"
        "Route rules:\\n"
        "- i2v (normal): motion-first continuation from the still image, one visible action line, camera behavior, energy/atmosphere, short safety tail at end.\\n"
        "- ia2v (lip_sync_music/performance): performance-first and emotionally active; readable lips/mouth and emotion are mandatory; framing is flexible (tight close-up/close-up/medium close-up/waist-up/full-body) as long as mouth and identity stay readable; allow expressive but controlled body-led performance (shoulders/torso/head/neck/breath/slight lean/controlled weight shift), no frozen mannequin posture, smooth LTX-safe motion only.\\n"
        "- first_last (locked transition): controlled camera/framing/state transition between near-matched anchor frames with one subtle visible delta only; same subject/stance/world/costume/shot feeling; must include TWO standalone prompts start_image_prompt and end_image_prompt; short safety tail at end.\\n"
        "- first_last must honor scene_plan.first_last_mode when present: push_in_emotional, pull_back_release, small_side_arc, reveal_face_from_shadow, foreground_parallax, camera_settle, visibility_reveal.\\n"
        "Energy tier behavior (mandatory): low-energy i2v -> restrained motion and held tension/afterimage; medium-energy i2v -> forward motion with controlled camera support; high-energy ia2v -> expressive but readable upper-body performance; first_last -> continuity-first micro-transition and never transit/geography change.\\n"
        "FIRST_LAST FORBIDDEN BY DEFAULT: stepping, crouching, bowing, torso dip, dance choreography, large arm action, spinning, dramatic camera movement, added background actors, layout changes, fine-motor hand/prop choreography.\\n"
        "Do NOT use dance/performance language in first_last unless scene contract explicitly asks for it.\\n"
        "Scene-level quality beats (if scene ids exist):\\n"
        "- sc_1: intro-observational, more static, more closed, shadow-heavy, restrained framing intent.\\n"
        "- sc_5: breather with internal defiance; quiet but tense pause with readable subtle emotional charge (not dead static).\\n"
        "Honor scene_plan route semantics exactly: first_last must stay strict first_last contract; ia2v must stay audio-driven singing/performance; i2v must stay simple observable action.\\n"
        "For every scene, consume and apply these scene_plan fields explicitly: speaker_role, spoken_line, lip_sync_allowed, mouth_visible_required, listener_reaction_allowed, reaction_role.\\n"
        "Read story_core.prompt_interface_contract as source-of-truth for subject visibility: visibility_mode, must_be_visible, may_be_offscreen, subject_presence_requirement.\\n"
        "When story_core.prompt_interface_contract.must_be_visible contains multiple cast roles, every photo_prompt must place those roles in the same shared physical space defined by world_lock/user input.\\n"
        "The visual_focus_role may dominate the frame, but every must_be_visible role must remain visibly present in the same scene unless explicitly listed in may_be_offscreen.\\n"
        "Do not collapse such scenes into a single-person portrait; keep non-focus required roles visible as background/edge of frame/partial profile/shoulder/silhouette/reflection/nearby presence.\\n"
        "If a role is in must_be_visible and not in may_be_offscreen, do not describe that role as offscreen.\\n"
        "For ia2v with lip_sync_allowed=true: only speaker_role can have mouth movement, listener/reaction_role stays silent, and never output simultaneous dual-speaker lip movement.\\n"
        "For ia2v/lipsync scenes with multiple must_be_visible roles: keep speaker_role/vocal_owner_role as the readable mouth-sync face, and keep other must_be_visible roles visibly present but silent/reaction/background in the same shared scene space.\\n"
        "For ia2v with lip_sync_allowed=false: keep performance/audio-reactive behavior without mouth-sync directives.\\n"
        "For i2v reaction scenes: visual_focus_role can be primary while speaker_role differs, but other must_be_visible roles still remain visible in the same shared scene space.\\n"
        "If speaker_role is unknown, do not author lip-sync prompt language.\\n"
        "For first_last scenes, start_image_prompt and end_image_prompt must also satisfy must_be_visible in the same shared scene space; visual state delta may prioritize visual_focus_role but other must_be_visible roles remain visible.\\n"
        "Always include compact negative_prompt with safety constraints as short tail text.\\n"
        "For ia2v, negative_prompt must stay minimal technical-only: hidden mouth, unreadable lips, mouth fully obscured for long, face fully turned away from readability, severe identity drift, duplicate main subject, severe facial deformation.\\n"
        "Do not overload ia2v negative_prompt with long motion/framing bans.\\n"
        "For i2v, keep normal structured route-specific positive+negative separation and avoid visible singing/lip-sync unless explicitly requested by scene contract.\\n"
        "GLOBAL continuity lock: if not explicitly changed by story, forbid random wardrobe/accessory/object/location/weather/season/day-night/style drift or random extra lead character.\\n"
        "Never mix negative prompt text into positive video_prompt; keep positive and negative fields separated.\\n"
        "For first_last, return both positive_video_prompt and negative_video_prompt fields (negative_video_prompt is mandatory for first_last).\\n"
        "Set prompt_notes.audio_driven=true for ia2v scenes.\\n"
        "Return EXACT contract keys:\\n"
        "{\\n"
        '  \"plan_version\": \"scene_prompts_v1\",\\n'
        '  \"mode\": \"clip\",\\n'
        '  \"scenes\": [{\"scene_id\": \"sc_1\", \"route\": \"i2v\", \"photo_prompt\": \"\", \"video_prompt\": \"\", \"negative_prompt\": \"\", \"positive_video_prompt\": \"\", \"negative_video_prompt\": \"\", \"start_image_prompt\": \"\", \"end_image_prompt\": \"\", \"prompt_notes\": {\"shot_intent\": \"\", \"continuity_anchor\": \"\", \"world_anchor\": \"\", \"identity_anchor\": \"\", \"lighting_anchor\": \"\", \"motion_safety\": \"\", \"audio_driven\": false}}],\\n'
        '  \"global_prompt_rules\": [\"\"]\\n'
        "}\\n\\n"
        f"SCENE_PROMPTS_CONTEXT:\\n{json.dumps(context, ensure_ascii=False)}"
    )


def _prompt_notes_template(route: str) -> dict[str, Any]:
    clean_route = route if route in ALLOWED_ROUTES else "i2v"
    notes = {
        "shot_intent": "",
        "continuity_anchor": "keep identity/world/style continuity from previous scene",
        "world_anchor": "same grounded world tone and atmosphere",
        "identity_anchor": "same hero face, body proportions, and wardrobe logic",
        "lighting_anchor": "plausible lighting progression within same realism family",
        "motion_safety": "single clear motion line, smooth camera, anatomy-safe body dynamics",
        "audio_driven": clean_route == "ia2v",
    }
    if clean_route == "i2v":
        notes.update(
            {
                "i2v_motion_family": "baseline_forward_walk",
                "pace_class": "purposeful",
                "camera_pattern": "stable_follow",
                "reveal_target": "none",
                "parallax_required": False,
                "allow_head_turn": False,
                "allow_simple_hand_motion": True,
                "forbid_complex_hand_motion": True,
                "forbid_slow_motion_feel": True,
                "forbid_bullet_time": True,
                "forbid_stylized_action": True,
                "require_real_time_pacing": True,
                "max_camera_intensity": "low",
                "i2v_prompt_duration_hint_sec": 0.0,
                "template_built": False,
            }
        )
    if clean_route == "first_last":
        notes.update(
            {
                "transition_contract": "controlled_micro_transition",
                "continuity_mode": "controlled_micro_transition",
                "semantic_beat": "transition_beat",
                "first_last_mode": "",
                "first_state": "",
                "last_state": "",
                "same_world_required": True,
                "same_outfit_required": True,
                "same_lighting_required": True,
                "same_camera_family_required": True,
            }
        )
    return notes


def _scene_plan_semantics_lock(scene_plan_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": str(scene_plan_row.get("scene_id") or "").strip(),
        "route": str(scene_plan_row.get("route") or "i2v").strip().lower(),
        "scene_function": str(scene_plan_row.get("scene_function") or "").strip(),
        "emotional_intent": str(scene_plan_row.get("emotional_intent") or "").strip(),
        "motion_intent": str(scene_plan_row.get("motion_intent") or "").strip(),
        "shot_scale": str(scene_plan_row.get("shot_scale") or "").strip(),
        "camera_intimacy": str(scene_plan_row.get("camera_intimacy") or "").strip(),
        "performance_openness": str(scene_plan_row.get("performance_openness") or "").strip(),
        "visual_event_type": str(scene_plan_row.get("visual_event_type") or "").strip(),
        "first_last_mode": str(scene_plan_row.get("first_last_mode") or "").strip(),
        "continuity_mode": _resolve_first_last_continuity_mode(scene_plan_row),
        "semantic_beat": _resolve_first_last_semantic_beat(scene_plan_row),
        "i2v_motion_family": str(scene_plan_row.get("i2v_motion_family") or "").strip(),
        "pace_class": str(scene_plan_row.get("pace_class") or "").strip(),
        "camera_pattern": str(scene_plan_row.get("camera_pattern") or "").strip(),
        "reveal_target": str(scene_plan_row.get("reveal_target") or "").strip(),
        "parallax_required": bool(scene_plan_row.get("parallax_required")),
        "allow_head_turn": bool(scene_plan_row.get("allow_head_turn")),
        "allow_simple_hand_motion": bool(scene_plan_row.get("allow_simple_hand_motion")),
        "forbid_complex_hand_motion": bool(scene_plan_row.get("forbid_complex_hand_motion")),
        "forbid_slow_motion_feel": bool(scene_plan_row.get("forbid_slow_motion_feel")),
        "forbid_bullet_time": bool(scene_plan_row.get("forbid_bullet_time")),
        "forbid_stylized_action": bool(scene_plan_row.get("forbid_stylized_action")),
        "require_real_time_pacing": bool(scene_plan_row.get("require_real_time_pacing")),
        "max_camera_intensity": str(scene_plan_row.get("max_camera_intensity") or "").strip(),
        "i2v_prompt_duration_hint_sec": _round3(scene_plan_row.get("i2v_prompt_duration_hint_sec")),
        "motion_risk": _safe_dict(scene_plan_row.get("motion_risk")),
    }


def _detect_scene_prompt_contract_mismatch(
    *,
    expected_route: str,
    scene_plan_row: dict[str, Any],
    model_row: dict[str, Any],
) -> tuple[bool, bool, list[str]]:
    actual_route = str(model_row.get("route") or expected_route).strip().lower()
    route_mismatch = actual_route != expected_route
    mismatch_reasons: list[str] = []
    if route_mismatch:
        mismatch_reasons.append("route_mismatch")
    if not model_row:
        return route_mismatch, False, mismatch_reasons

    notes = _safe_dict(model_row.get("prompt_notes"))
    blob = " ".join(
        [
            str(model_row.get("photo_prompt") or ""),
            str(model_row.get("video_prompt") or ""),
            str(model_row.get("positive_video_prompt") or ""),
            str(model_row.get("start_image_prompt") or ""),
            str(model_row.get("end_image_prompt") or ""),
        ]
    ).lower()
    has_first_last_terms = any(
        token in blob
        for token in (
            "micro-transition",
            "micro transition",
            "one subtle",
            "one subtle visible delta",
            "subtle delta",
            "start frame",
            "end frame",
            "start keyframe",
            "end keyframe",
            "locked transition",
            "controlled transition",
            "same subject/world/outfit",
        )
    )
    has_performance_terms = any(token in blob for token in ("audio", "vocal", "sing", "lip", "performance"))
    has_face_readability = any(
        token in blob for token in ("readable face", "face readable", "mouth", "readable mouth", "upper-body", "upper body")
    )
    has_transition_transit_language = bool(
        re.search(
            r"\b(travel|walk to|transit|location change|geography change)\b",
            blob,
        )
    )

    semantic_mismatch = False
    scene_function = str(scene_plan_row.get("scene_function") or "").lower()
    transition_contract = str(notes.get("transition_contract") or "").strip().lower()
    has_transition_contract = transition_contract == "controlled_micro_transition"
    has_scene_function_callback = any(token in scene_function for token in ("release", "afterimage", "callback"))
    has_scene_function_echo = has_scene_function_callback and any(token in blob for token in ("release", "afterimage", "callback", "echo"))
    inferred_audio_driven = bool(notes.get("audio_driven")) or (
        has_performance_terms and (has_face_readability or "music" in blob or "phrase" in blob)
    )
    if expected_route == "ia2v":
        if has_first_last_terms and not has_performance_terms:
            semantic_mismatch = True
            mismatch_reasons.append("ia2v_first_last_terms_without_performance")
        if not inferred_audio_driven:
            semantic_mismatch = True
            mismatch_reasons.append("ia2v_audio_driven_not_detected")
        if not (has_performance_terms and has_face_readability):
            semantic_mismatch = True
            mismatch_reasons.append("ia2v_performance_or_face_readability_missing")
        if ("climax" in scene_function or "performance" in scene_function) and has_first_last_terms and not has_performance_terms:
            semantic_mismatch = True
            mismatch_reasons.append("ia2v_climax_scene_without_performance_terms")
    elif expected_route == "first_last":
        has_start = bool(str(model_row.get("start_image_prompt") or "").strip())
        has_end = bool(str(model_row.get("end_image_prompt") or "").strip())
        if not has_start or not has_end:
            semantic_mismatch = True
            mismatch_reasons.append("first_last_missing_start_or_end_image_prompt")
        if transition_contract and not has_transition_contract:
            semantic_mismatch = True
            mismatch_reasons.append("first_last_transition_contract_not_honored")

        strict_echo_mode = _is_strict_echo_first_last_scene(scene_plan_row, notes)
        if strict_echo_mode:
            continuity_ok = any(
                token in blob
                for token in (
                    "same subject",
                    "same world",
                    "continuity",
                    "echo",
                    "strict_echo",
                    "state delta",
                    "no added actors",
                    "no new entity",
                )
            )
            forbidden_new_entity = bool(
                re.search(
                    r"\b(new character|another character|extra character|extra identifiable actor|new actor|crowd appears|new world|different world|new location|different location|new prop|new key prop|switch to|cut to another place)\b",
                    blob,
                )
            )
            explicit_contradiction = bool(
                re.search(
                    r"\b(wardrobe change|different outfit|identity change|new costume|new cast|added cast)\b",
                    blob,
                )
            )
            if not continuity_ok:
                semantic_mismatch = True
                mismatch_reasons.append("strict_echo_continuity_not_detected")
            if forbidden_new_entity or explicit_contradiction or has_transition_transit_language:
                semantic_mismatch = True
                mismatch_reasons.append("strict_echo_forbidden_entity_or_transition_detected")
            if has_performance_terms and "afterimage" in scene_function and "residual" not in blob and "echo" not in blob:
                semantic_mismatch = True
                mismatch_reasons.append("afterimage_echo_missing")
        else:
            if not (has_first_last_terms or (has_start and has_end and has_scene_function_echo)):
                semantic_mismatch = True
                mismatch_reasons.append("first_last_transition_echo_not_detected")
            if has_transition_transit_language:
                semantic_mismatch = True
                mismatch_reasons.append("first_last_transition_transit_language_detected")
            if has_performance_terms and not (has_first_last_terms or has_scene_function_echo):
                semantic_mismatch = True
                mismatch_reasons.append("first_last_performance_without_transition_or_echo")
    else:  # i2v
        if has_first_last_terms:
            semantic_mismatch = True
            mismatch_reasons.append("i2v_first_last_terms_detected")
        if has_performance_terms and bool(notes.get("audio_driven")):
            semantic_mismatch = True
            mismatch_reasons.append("i2v_audio_driven_performance_terms_detected")
        if str(model_row.get("start_image_prompt") or "").strip() or str(model_row.get("end_image_prompt") or "").strip():
            semantic_mismatch = True
            mismatch_reasons.append("i2v_start_or_end_image_prompt_detected")

    return route_mismatch, semantic_mismatch, list(dict.fromkeys(mismatch_reasons))


def _sanitize_positive_prompt(text: str, negative_text: str) -> tuple[str, bool]:
    clean = str(text or "").strip()
    if not clean:
        return "", False
    changed = False
    low = clean.lower()
    neg_low = str(negative_text or "").strip().lower()
    cut_idx = -1
    for token in _NEGATIVE_LEAK_TOKENS:
        idx = low.find(token)
        if idx >= 0 and (cut_idx < 0 or idx < cut_idx):
            cut_idx = idx
    for marker in _EXPLICIT_NEGATIVE_MARKERS:
        idx = low.find(marker)
        if idx >= 0 and (cut_idx < 0 or idx < cut_idx):
            cut_idx = idx
    if cut_idx >= 0:
        clean = clean[:cut_idx].rstrip(" ,;.")
        changed = True
    if neg_low and neg_low in clean.lower():
        clean = clean[: clean.lower().find(neg_low)].rstrip(" ,;.")
        changed = True
    before_cleanup = clean
    clean = re.sub(r"[\[\(]\s*$", "", clean).strip()
    clean = re.sub(r"[\]\)]", "", clean)
    clean = re.sub(r"\s*[,;:.!?-]\s*[,;:.!?-]\s*", ", ", clean)
    clean = re.sub(r"[,;:\-]+\s*$", "", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" ,;:.")
    if clean != before_cleanup:
        changed = True
    return clean[:900], changed


def _strip_ia2v_positive_noise(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    for pattern in _IA2V_POSITIVE_NOISE_PATTERNS:
        clean = pattern.sub(" ", clean)
    clean = re.sub(r"\s*[,;:.!?-]\s*[,;:.!?-]\s*", ", ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" ,;:.")
    return clean


def _clean_ia2v_negative_prompt(text: str) -> str:
    parts = [p.strip() for p in str(text or "").split(",") if p.strip()]
    # For ia2v we intentionally keep the final negative prompt minimal and technical-only.
    # Legacy generic world/style bans (e.g. color/architecture/atmosphere) must not pass through.
    kept: list[str] = []
    canon_tokens = [p.strip() for p in _IA2V_LIP_SYNC_NEGATIVE_CANON.split(",") if p.strip()]
    canon_norm = {re.sub(r"\s+", " ", token.lower()).strip(" ,;:.") for token in canon_tokens}
    for part in parts:
        if any(pattern.search(part) for pattern in _IA2V_ANTI_LIPSYNC_NEGATIVE_PATTERNS):
            continue
        normalized = re.sub(r"\s+", " ", part.lower()).strip(" ,;:.")
        if normalized in canon_norm:
            kept.append(part)
    kept.extend(canon_tokens)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in kept:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return ", ".join(deduped)[:900]


def apply_ia2v_lipsync_canon_to_prompt_row(row: dict[str, Any], source_scene: dict[str, Any] | None = None) -> bool:
    current = _safe_dict(row)
    source = _safe_dict(source_scene)
    route = str(current.get("route") or source.get("route") or "").strip().lower()
    if route != "ia2v":
        return False

    emotional_intent = str(current.get("emotional_intent") or source.get("emotional_intent") or "").strip()
    spoken_line = str(current.get("spoken_line") or source.get("spoken_line") or "").strip()
    speaker_role = (
        str(current.get("speaker_role") or "").strip()
        or str(current.get("primary_role") or "").strip()
        or str(source.get("speaker_role") or "").strip()
        or str(source.get("primary_role") or "").strip()
        or "character_1"
    )

    canon_prompt = _IA2V_VIDEO_PROMPT_CANON
    action_heavy_markers = (
        "pour",
        "trembling hands",
        "packing",
        "walking away",
        "holding bottle",
        "sink action",
    )
    if emotional_intent and not any(marker in emotional_intent.lower() for marker in action_heavy_markers):
        canon_prompt = _append_prompt_clause(canon_prompt, _trim_sentence(f"Tone accent: {emotional_intent}", max_len=120))
    if spoken_line:
        canon_prompt = _append_prompt_clause(canon_prompt, _trim_sentence(f"Vocal phrase anchor: {spoken_line}", max_len=180))
    canon_prompt = _strip_ia2v_positive_noise(canon_prompt)

    base_negative = str(current.get("negative_video_prompt") or current.get("negative_prompt") or "").strip()
    canon_negative = _clean_ia2v_negative_prompt(base_negative)

    current["route"] = "ia2v"
    current["lip_sync_allowed"] = True
    current["lip_sync_priority"] = "primary"
    current["mouth_visible_required"] = True
    current["singing_readiness_required"] = True
    current["speaker_role"] = speaker_role
    current["object_action_allowed"] = False
    current["foreground_performance_rule"] = (
        "Performer-first vocal performance priority; keep lips and mouth readable, background action only as soft context."
    )
    if spoken_line:
        current["spoken_line"] = spoken_line
    current["video_prompt"] = canon_prompt[:900]
    current["positive_video_prompt"] = canon_prompt[:900]
    current["negative_prompt"] = canon_negative
    current["negative_video_prompt"] = canon_negative
    return True


def _build_package_anchor_fingerprint(package: dict[str, Any], story_core: dict[str, Any], world_continuity: dict[str, Any]) -> dict[str, Any]:
    refs = _safe_dict(package.get("refs_inventory"))
    hero = _safe_dict(_safe_dict(story_core.get("identity_lock")).get("hero"))
    style_lock = _safe_dict(story_core.get("style_lock"))
    world_lock = _safe_dict(story_core.get("world_lock"))
    anchor_tokens = [
        str(hero.get("outfit_essentials") or ""),
        str(hero.get("appearance_notes") or ""),
        str(world_continuity.get("environment_family") or ""),
        str(world_lock.get("setting") or ""),
        str(world_lock.get("setting_description") or ""),
        str(style_lock.get("lighting") or ""),
        str(_safe_dict(refs.get("ref_location")).get("value") or ""),
        str(_safe_dict(refs.get("ref_character_1")).get("value") or ""),
    ]
    token_words: set[str] = set()
    for chunk in anchor_tokens:
        for word in re.findall(r"[a-zA-Z]{4,}", chunk.lower()):
            token_words.add(word)
    return {
        "hero_anchor": _build_identity_lock_summary(story_core),
        "world_anchor": _build_world_lock_summary(story_core),
        "lighting_anchor": str(style_lock.get("lighting") or "").strip(),
        "continuity_tokens": sorted(token_words)[:40],
    }


def _row_looks_unrelated_to_current_package(row: dict[str, Any], fingerprint: dict[str, Any]) -> bool:
    blob = " ".join(
        [
            str(row.get("photo_prompt") or ""),
            str(row.get("video_prompt") or ""),
            str(row.get("positive_video_prompt") or ""),
            str(_safe_dict(row.get("prompt_notes")).get("world_anchor") or ""),
            str(_safe_dict(row.get("prompt_notes")).get("identity_anchor") or ""),
        ]
    ).lower()
    if not blob:
        return False
    stale_hits = sum(1 for token in _STALE_WORLD_TOKENS if token in blob)
    continuity_tokens = _safe_list(fingerprint.get("continuity_tokens"))
    anchor_hits = sum(1 for token in continuity_tokens if token and str(token) in blob)
    return stale_hits > 0 and anchor_hits == 0


def _build_i2v_base_guardrail(*, role_label: str, world_anchor: str, lighting_anchor: str) -> str:
    return (
        f"Exact first-frame identity anchor for {role_label}: same primary subject identity, same face, same wardrobe family when locked. "
        f"Keep the same world family ({world_anchor}) and lighting family ({lighting_anchor or 'current locked lighting family'}) when locked by current context. "
        "Preserve same background geometry and grounded documentary realism. "
        "No identity drift, no wardrobe change, no location change, no broken anatomy, no floating limbs, no leg warping, no face deformation, no camera shake, no slow-motion feel, no stylized action feel, no bullet-time effect."
    )


def _build_i2v_motion_family_prompt(scene_plan_row: dict[str, Any]) -> tuple[str, str]:
    family = str(scene_plan_row.get("i2v_motion_family") or "").strip()
    pace_class = str(scene_plan_row.get("pace_class") or "").strip().lower()
    camera_pattern = str(scene_plan_row.get("camera_pattern") or "").strip().lower()
    reveal_target = str(scene_plan_row.get("reveal_target") or "").strip().lower()
    hint_sec = _round3(scene_plan_row.get("i2v_prompt_duration_hint_sec"))
    duration_hint = f" Keep pacing real-time around ~{hint_sec:.1f}s." if hint_sec > 0 else " Keep pacing real-time."
    pace_prefix_map = {
        "restrained": "Restrained, controlled pacing.",
        "purposeful": "Purposeful, readable pacing.",
        "energetic": "Energetic but controlled pacing.",
    }
    camera_clause_map = {
        "push_in": "Camera pattern: push_in with smooth forward pressure only.",
        "side_track": "Camera pattern: side_track with stable lateral travel and coherent parallax.",
        "follow_reveal": "Camera pattern: follow_reveal that follows attention into a clear reveal.",
        "pull_back": "Camera pattern: pull_back opening depth while keeping framing stable.",
        "stable_follow": "Camera pattern: stable_follow with minimal reframing spikes.",
    }
    reveal_clause_map = {
        "forward_path": "Reveal target: forward_path.",
        "side_space": "Reveal target: side_space.",
        "noticed_object": "Reveal target: noticed_object.",
    }
    templates = {
        "push_in_follow": (
            "Natural forward motion line. Smooth push-in from medium-full framing toward a controlled medium shot; physically natural motion with stable legs/feet, natural simple arm swing only, subtle fabric/hair response, no dramatic camera turn.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn, aggressive zoom spikes, extreme close-up crash-in",
        ),
        "side_tracking_walk": (
            "Forward walk continuation while camera tracks sideways with clearly visible but controlled parallax. Keep environment geometry stable, physically coherent body travel, and no background collapse.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn, unstable parallax, no parallax",
        ),
        "look_reveal_follow": (
            "Subject keeps moving forward and shifts attention; slight head/upper-body turn without stopping. Camera follows attention via lateral move plus pan, opening a revealed traveling view with believable parallax and stable geometry.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn, unstable parallax, no parallax, head turn without camera follow",
        ),
        "baseline_forward_walk": (
            "Restrained natural forward walk: one to two calm steps or short grounded walk continuation, mostly stable frontal/stable-follow camera, subtle fabric/hair response, safe realism.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn",
        ),
        "tension_head_turn": (
            "Slight slowdown with restrained side glance/cautious check, subtle shoulder tension, simple body motion, suspicious/alert feel, no large gestures.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn",
        ),
        "pull_back_release": (
            "Camera slowly pulls back while subject remains grounded in motion/stance; world depth opens behind with stable geometry and ambient life; restrained emotional tone for release/aftermath distance.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn, static background collapse, artificial zoom feel",
        ),
    }
    motion_prompt, negative_prompt = templates.get("baseline_forward_walk")
    if family in templates:
        motion_prompt, negative_prompt = templates[family]
    pace_clause = pace_prefix_map.get(pace_class, "Purposeful, readable pacing.")
    camera_clause = camera_clause_map.get(camera_pattern, "Camera pattern: stable_follow with minimal reframing spikes.")
    reveal_clause = ""
    if family == "look_reveal_follow":
        reveal_clause = f" {reveal_clause_map.get(reveal_target, 'Reveal target: forward_path.')}"
    return f"{pace_clause} {camera_clause} {motion_prompt}{reveal_clause}{duration_hint}", negative_prompt


def _build_i2v_negative_prompt(scene_plan_row: dict[str, Any]) -> str:
    _, negative_prompt = _build_i2v_motion_family_prompt(scene_plan_row)
    return negative_prompt


def _build_i2v_prompt_bundle(
    *,
    role_label: str,
    scene_plan_row: dict[str, Any],
    world_anchor: str,
    lighting_anchor: str,
    identity_anchor: str,
) -> dict[str, str]:
    guardrail = _build_i2v_base_guardrail(role_label=role_label, world_anchor=world_anchor, lighting_anchor=lighting_anchor)
    motion_prompt, _ = _build_i2v_motion_family_prompt(scene_plan_row)
    negative_prompt = _build_i2v_negative_prompt(scene_plan_row)
    photo_prompt = (
        f"Exact anchor frame of {role_label} in the same locked world family and same locked lighting family; "
        "same face/wardrobe continuity anchors and stable geometry, grounded documentary realism."
    )
    positive_video_prompt = f"{guardrail} {motion_prompt}".strip()
    return {
        "photo_prompt": _enrich_prompt_with_anchor(photo_prompt, identity_anchor, world_anchor),
        "video_prompt": _enrich_prompt_with_anchor(positive_video_prompt, identity_anchor, world_anchor),
        "positive_video_prompt": _enrich_prompt_with_anchor(positive_video_prompt, identity_anchor, world_anchor),
        "negative_video_prompt": negative_prompt,
        "negative_prompt": negative_prompt,
    }


def _build_scenes_core_image_prompt(scene: dict[str, Any]) -> str:
    camera = _safe_dict(scene.get("camera"))
    prompt_parts: list[str] = []
    scene_specific_count = 0
    for value in (
        scene.get("location"),
        scene.get("action"),
        scene.get("environment_interaction"),
        scene.get("visual_hook"),
    ):
        text = str(value or "").strip()
        if text:
            prompt_parts.append(text)
            scene_specific_count += 1
    framing = str(camera.get("framing") or "").strip()
    if framing:
        prompt_parts.append(f"{framing} shot")
        scene_specific_count += 1
    angle = str(camera.get("angle") or "").strip()
    if angle:
        prompt_parts.append(f"{angle} angle")
        scene_specific_count += 1

    if scene_specific_count == 0:
        return ""

    prompt_parts.append("cinematic lighting")
    prompt_parts.append("realistic environment")
    prompt_parts.append("consistent character identity")
    return ", ".join(prompt_parts)


def _build_scenes_core_video_prompt(scene: dict[str, Any], route: str) -> str:
    camera = _safe_dict(scene.get("camera"))
    video_parts: list[str] = []
    scene_specific_count = 0

    action = str(scene.get("action") or "").strip()
    if action:
        video_parts.append(action)
        scene_specific_count += 1
    movement = str(camera.get("movement") or "").strip()
    if movement:
        video_parts.append(f"camera {movement}")
        scene_specific_count += 1
    environment_interaction = str(scene.get("environment_interaction") or "").strip()
    if environment_interaction:
        video_parts.append(f"environment reacts: {environment_interaction}")
        scene_specific_count += 1
    visual_hook = str(scene.get("visual_hook") or "").strip()
    if visual_hook:
        video_parts.append(f"visual focus: {visual_hook}")
        scene_specific_count += 1
    energy = str(scene.get("energy") or "").strip()
    if energy:
        video_parts.append(f"energy level: {energy}")
        scene_specific_count += 1

    if scene_specific_count == 0:
        return ""

    video_parts.append("no idle walking")
    video_parts.append("no static lifeless frames")
    if route == "ia2v":
        video_parts.append("clear vocal performance, visible mouth articulation, expressive face")
    elif route == "i2v":
        video_parts.append("natural motion, no lip sync")
    return ", ".join(video_parts)


def _build_fallback_scene_prompts(
    package: dict[str, Any],
    scene_plan_row: dict[str, Any],
    role_row: dict[str, Any],
    story_core: dict[str, Any],
    world_continuity: dict[str, Any],
) -> dict[str, Any]:
    scene_id = str(scene_plan_row.get("scene_id") or "")
    route = str(scene_plan_row.get("route") or "i2v").strip()
    if route not in ALLOWED_ROUTES:
        route = "i2v"

    primary_role = _build_human_subject_label(role_row, story_core, scene_plan_row)
    speaker_role = str(scene_plan_row.get("speaker_role") or "").strip()
    speaker_label = speaker_role if speaker_role and speaker_role != "unknown" else primary_role
    spoken_line = str(scene_plan_row.get("spoken_line") or "").strip()
    lip_sync_allowed = bool(scene_plan_row.get("lip_sync_allowed"))
    mouth_visible_required = bool(scene_plan_row.get("mouth_visible_required"))
    listener_reaction_allowed = bool(scene_plan_row.get("listener_reaction_allowed"))
    reaction_role = str(scene_plan_row.get("reaction_role") or "").strip()
    scene_function = str(scene_plan_row.get("scene_function") or "scene beat")
    emotional = str(scene_plan_row.get("emotional_intent") or "grounded emotion")
    motion_intent = str(scene_plan_row.get("motion_intent") or "subtle motion")
    world_anchor = str(world_continuity.get("environment_family") or world_continuity.get("country_or_region") or "grounded realistic world")
    opening_anchor = str(story_core.get("opening_anchor") or "")
    anchors = _build_scene_anchor_bundle(
        package=package,
        story_core=story_core,
        role_row=role_row,
        scene_plan_row=scene_plan_row,
        world_continuity=world_continuity,
    )
    ownership_binding_inventory = _build_ref_binding_inventory(_safe_dict(package.get("refs_inventory")))
    binding_clause = _binding_prompt_clause(str(role_row.get("primary_role") or ""), ownership_binding_inventory)

    positive_video_prompt = ""
    negative_video_prompt = ""
    high_motion_risk = _is_high_motion_risk(scene_plan_row)

    if route == "ia2v":
        photo_prompt = (
            f"Story-grounded emotionally charged singing-ready start frame of {speaker_label} in {world_anchor} for {scene_function} with {emotional}. "
            "Framing is flexible (tight close-up, close-up, medium close-up, waist-up, or full-body) while face identity, mouth readability, and emotion readability stay clear. "
            "Mouth is open or slightly open in a natural singing shape with visible vocal effort; body involvement is encouraged through neck/shoulders/clavicles/breath tension and meaningful gesture when hands are visible."
        )
        if lip_sync_allowed and speaker_role and speaker_role != "unknown":
            video_prompt = (
                "Use the uploaded image as the exact first frame and identity anchor. "
                "A performance shot of the same performer singing an emotional line with clear expressive lip sync and readable vocal delivery. "
                "Allow expressive but controlled gestures and smooth body-led micro-performance through shoulders, torso, head, neck, breath tension, slight lean, and controlled weight shift. "
                "Hands may emphasize phrases when visible, but avoid jerky, abrupt, or chaotic dance-like motion. "
                "The face and mouth remain readable and important in any framing from tight close-up to full body. Cinematic realism with smooth LTX-safe camera motion. "
                f"{speaker_label} is the only active vocal performer for this phrase{f' ({spoken_line})' if spoken_line else ''}. "
                f"{f'{reaction_role} may stay nearby as silent listener reaction. ' if listener_reaction_allowed and reaction_role else ''}"
                f"{'Mouth readability is required for the active speaker. ' if mouth_visible_required else ''}"
                f"{binding_clause}"
            )
        else:
            video_prompt = (
                "Use the uploaded image as the exact first frame and identity anchor. "
                "Same performer in emotional vocal performance with readable face and mouth, visible vocal effort, controlled breathing, and smooth body-led micro-performance. "
                "Subtle shoulders/torso/head/neck motion, slight lean, restrained weight shift, and optional meaningful hand emphasis when visible are allowed. "
                "Keep performer-first lip readability with flexible framing and smooth LTX-safe camera motion."
                f"{f'{reaction_role} may stay nearby as silent listener reaction. ' if listener_reaction_allowed and reaction_role else ''}"
                f"{binding_clause}"
            )
        negative_video_prompt = _LIP_SYNC_NEGATIVE_PROMPT
    elif route == "first_last":
        first_last_mode = str(scene_plan_row.get("first_last_mode") or "").strip().lower()
        if first_last_mode not in FIRST_LAST_MODES:
            first_last_mode = "camera_settle"
        attached_prop_token = _detect_attached_prop_token(
            str(scene_plan_row.get("first_state") or ""),
            str(scene_plan_row.get("last_state") or ""),
            str(scene_plan_row.get("transition_action") or ""),
            str(scene_plan_row.get("scene_goal") or ""),
            str(scene_plan_row.get("frame_description") or ""),
            str(scene_plan_row.get("motion_intent") or ""),
            str(scene_plan_row.get("emotional_intent") or ""),
            scene_function,
            str(scene_plan_row.get("scene_summary") or ""),
            str(story_core.get("story_summary") or ""),
        )
        continuity_mode = _resolve_first_last_continuity_mode(scene_plan_row)
        semantic_beat = _resolve_first_last_semantic_beat(scene_plan_row)
        first_state, last_state, visual_delta = _build_first_last_visual_delta(
            scene_plan_row=scene_plan_row,
            primary_role=primary_role,
            attached_prop_token=attached_prop_token,
        )
        scene_space = _trim_sentence(f"the same global {world_anchor} world-family scene space", max_len=90)
        photo_prompt = (
            f"One transition keyframe of {primary_role} in the same global {world_anchor} world-family scene space, hinge moment for {scene_function}, "
            "subject and environment remain stable, same outfit/light/framing family, echo continuity from previous scene."
        )
        start_image_prompt, end_image_prompt, positive_video_prompt, negative_video_prompt = _build_first_last_prompt_pair(
            primary_role=primary_role,
            scene_space=scene_space,
            first_state=first_state,
            last_state=last_state,
            visual_delta=visual_delta,
            attached_prop_token=attached_prop_token,
            first_last_mode=first_last_mode,
            scene_function=scene_function,
            emotional_intent=emotional,
            continuity_mode=continuity_mode,
            semantic_beat=semantic_beat,
        )
        video_prompt = positive_video_prompt
    else:
        if scene_id == "sc_1":
            photo_prompt = (
                f"Intro keyframe of {primary_role}, static and observational, closed posture near the same wall, shadow-heavy composition, "
                f"emotion: restrained {emotional}, continuity with prior scenes and lighting arc."
            )
            video_prompt = (
                "Very restrained intro beat with nearly static body line and subtle breath-level motion only. "
                f"Camera intent is observational and controlled, preserving closed mood and shadow-heavy framing.{binding_clause}"
            )
        elif scene_id == "sc_5":
            photo_prompt = (
                f"Quiet tension keyframe of {primary_role} near the same wall, internal defiance gathering under stillness, "
                "subtle but readable emotional charge in face/shoulders, continuity with prior scenes and lighting arc."
            )
            video_prompt = (
                "Breather beat with micro-performance only: controlled pause, slight posture reset, contained energy building before final push. "
                f"Keep movement subtle but alive, no dead static, no chaotic motion.{binding_clause}"
            )
        else:
            photo_prompt = (
                f"Realistic keyframe of {primary_role} in {world_anchor}, {scene_function} beat, clear composition, "
                f"emotion: {emotional}, continuity with prior scenes and lighting arc."
            )
            video_prompt = (
                f"After the still frame, the moment moves forward through one clear action: {motion_intent}, with camera behavior that follows the action and keeps the atmosphere grounded in {emotional}. "
                f"Safety tail: preserve identity/world continuity, stable anatomy, and controlled camera.{binding_clause}"
            )

    if high_motion_risk and route in {"i2v", "ia2v"}:
        simplified = (
            f"Use one broad readable action only in {world_anchor}: controlled gaze/head/shoulder shift with minimal hand emphasis, no tiny finger sequencing near face, no wearable-adjustment micro details, no multistep prop manipulation. "
            "Prefer smooth camera settle/push/pull over micro hand actions."
        )
        if route == "ia2v":
            video_prompt = f"{video_prompt.rstrip('. ')}. {simplified}".strip()
            positive_video_prompt = f"{(positive_video_prompt or video_prompt).rstrip('. ')}. {simplified}".strip()
        else:
            video_prompt = simplified
            positive_video_prompt = simplified

    scenes_core = _safe_dict(scene_plan_row.get("scenes_core")) or _safe_dict(scene_plan_row.get("scene_core"))
    scene_prompt_source = dict(scenes_core)
    for key in ("location", "action", "environment_interaction", "visual_hook", "energy", "camera"):
        if key not in scene_prompt_source and key in scene_plan_row:
            scene_prompt_source[key] = scene_plan_row.get(key)
    if route in {"i2v", "ia2v"}:
        structured_image_prompt = _build_scenes_core_image_prompt(scene_prompt_source)
        structured_video_prompt = _build_scenes_core_video_prompt(scene_prompt_source, route)
        if structured_image_prompt:
            photo_prompt = structured_image_prompt
        if structured_video_prompt:
            video_prompt = structured_video_prompt
            positive_video_prompt = structured_video_prompt
            negative_video_prompt = _append_prompt_clause(negative_video_prompt, _SCENES_CORE_NEGATIVE_PROMPT)

    fallback_notes = _prompt_notes_template(route)
    fallback_notes["shot_intent"] = scene_function
    fallback_notes["continuity_anchor"] = anchors["continuity_anchor"] if anchors["continuity_anchor"] else (
        f"{opening_anchor[:120]}" if opening_anchor else fallback_notes["continuity_anchor"]
    )
    fallback_notes["world_anchor"] = anchors["world_anchor"]
    fallback_notes["identity_anchor"] = anchors["identity_anchor"]
    fallback_notes["lighting_anchor"] = anchors["lighting_anchor"]
    fallback_notes["shot_scale"] = str(scene_plan_row.get("shot_scale") or "")
    fallback_notes["camera_intimacy"] = str(scene_plan_row.get("camera_intimacy") or "")
    fallback_notes["performance_openness"] = str(scene_plan_row.get("performance_openness") or "")
    fallback_notes["visual_event_type"] = str(scene_plan_row.get("visual_event_type") or "")
    fallback_notes["repeat_variation_rule"] = str(scene_plan_row.get("repeat_variation_rule") or "")
    fallback_notes["motion_risk"] = _safe_dict(scene_plan_row.get("motion_risk"))
    fallback_notes["risk_simplified"] = bool(high_motion_risk and route in {"i2v", "ia2v"})
    if route == "first_last":
        fallback_notes["first_state"] = first_state
        fallback_notes["last_state"] = last_state
        fallback_notes["first_last_mode"] = first_last_mode
        fallback_notes["continuity_mode"] = _resolve_first_last_continuity_mode(scene_plan_row)
        fallback_notes["semantic_beat"] = _resolve_first_last_semantic_beat(scene_plan_row)

    return {
        "scene_id": scene_id,
        "route": route,
        "photo_prompt": _enrich_prompt_with_anchor(photo_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
        "video_prompt": _enrich_prompt_with_anchor(video_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
        "positive_video_prompt": _enrich_prompt_with_anchor(
            positive_video_prompt or video_prompt,
            anchors["identity_anchor"],
            anchors["world_anchor"],
        ),
        "negative_video_prompt": negative_video_prompt or _GLOBAL_NEGATIVE_PROMPT,
        "start_image_prompt": _enrich_prompt_with_anchor(start_image_prompt, anchors["identity_anchor"], anchors["world_anchor"])
        if route == "first_last"
        else "",
        "end_image_prompt": _enrich_prompt_with_anchor(end_image_prompt, anchors["identity_anchor"], anchors["world_anchor"])
        if route == "first_last"
        else "",
        "negative_prompt": (negative_video_prompt or _GLOBAL_NEGATIVE_PROMPT),
        "prompt_notes": fallback_notes,
    }


def _normalize_scene_prompts(
    package: dict[str, Any],
    raw: dict[str, Any],
    *,
    scene_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    scene_contract_lookup: dict[str, dict[str, Any]],
    global_contract: dict[str, Any],
    story_core: dict[str, Any],
    world_continuity: dict[str, Any],
) -> tuple[dict[str, Any], bool, str, int, int, int, int, int, int, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(raw.get("scenes")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            by_id[scene_id] = row

    scenes: list[dict[str, Any]] = []
    used_fallback = False
    validation_errors: list[str] = []
    missing_photo_count = 0
    missing_video_count = 0
    route_mismatch_count = 0
    semantic_mismatch_count = 0
    rows_rebuilt_from_scene_plan_count = 0
    positive_negative_leak_stripped_count = 0
    repaired_from_current_package_count = 0
    repaired_scene_ids: list[str] = []
    semantic_mismatch_scene_ids: list[str] = []
    missing_photo_scene_ids: list[str] = []
    missing_video_scene_ids: list[str] = []
    missing_field_by_scene: dict[str, list[str]] = {}
    mismatch_reason_by_scene: dict[str, list[str]] = {}
    unrelated_rows_discarded_count = 0
    i2v_template_rebuilt_count = 0
    i2v_unknown_family_fallback_count = 0
    i2v_template_override_applied = False
    i2v_prompt_family_counts = {family: 0 for family in sorted(I2V_MOTION_FAMILIES)}
    prompt_interface_contract = _resolve_prompt_interface_contract(story_core)
    must_be_visible_roles = [
        str(role).strip()
        for role in _safe_list(prompt_interface_contract.get("must_be_visible"))
        if str(role).strip()
    ]
    may_be_offscreen_roles = {
        str(role).strip()
        for role in _safe_list(prompt_interface_contract.get("may_be_offscreen"))
        if str(role).strip()
    }
    enforce_shared_space_rule = len(must_be_visible_roles) >= 2
    shared_space_missing_segments: list[str] = []
    offscreen_violation_segments: list[str] = []
    fingerprint = _build_package_anchor_fingerprint(package, story_core, world_continuity)

    for scene_raw in scene_rows:
        scene = _safe_dict(scene_raw)
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue

        expected_route = str(scene.get("route") or "i2v").strip()
        if expected_route not in ALLOWED_ROUTES:
            expected_route = "i2v"

        base = _safe_dict(by_id.get(scene_id))
        role_row = _safe_dict(role_lookup.get(scene_id))
        active_roles = {str(v).strip().lower() for v in _safe_list(role_row.get("active_roles")) if str(v).strip()}
        primary_role = str(role_row.get("primary_role") or scene.get("primary_role") or "").strip().lower()
        has_human_scene = bool(primary_role in {"character_1", "character_2", "character_3", "group"} or active_roles.intersection({"character_1", "character_2", "character_3", "group"}))
        scene_contract = _safe_dict(scene_contract_lookup.get(scene_id))
        fallback_row = _build_fallback_scene_prompts(package, scene, role_row, story_core, world_continuity)
        ownership_binding_inventory = _build_ref_binding_inventory(_safe_dict(package.get("refs_inventory")))
        carried_active_scene = _is_owner_carried_active_scene(scene, role_row, ownership_binding_inventory)
        held_active_scene = _is_owner_held_active_scene(scene, role_row, ownership_binding_inventory)
        anchors = _build_scene_anchor_bundle(
            package=package,
            story_core=story_core,
            role_row=role_row,
            scene_plan_row=scene,
            world_continuity=world_continuity,
        )
        required_world_anchor = str(scene_contract.get("required_world_anchor") or _safe_dict(global_contract.get("persisted_world_state")).get("world_anchor") or "").strip()
        if required_world_anchor:
            anchors["world_anchor"] = required_world_anchor
        required_props = [str(v).strip() for v in _safe_list(scene_contract.get("required_continuity_props")) if str(v).strip()]
        forbidden_actor_ids = {
            str(v).strip()
            for v in [*_safe_list(scene_contract.get("forbidden_actor_ids")), *_safe_list(_safe_dict(global_contract.get("actor_registry")).get("forbidden_actor_ids"))]
            if str(v).strip()
        }
        presence_policy = _safe_dict(scene_contract.get("presence_policy"))
        presence_clause = _presence_policy_clause(presence_policy)

        actual_route = str(base.get("route") or expected_route).strip()
        has_human_scene = bool(has_human_scene or actual_route == "ia2v")
        row_repaired_from_current_package = False
        if base and _row_looks_unrelated_to_current_package(base, fingerprint):
            used_fallback = True
            row_repaired_from_current_package = True
            unrelated_rows_discarded_count += 1
            validation_errors.append(f"unrelated_prompt_row_discarded:{scene_id}")
            base = {}
        route_mismatch, semantic_mismatch, mismatch_reasons = _detect_scene_prompt_contract_mismatch(
            expected_route=expected_route,
            scene_plan_row=scene,
            model_row=base,
        )
        if route_mismatch or semantic_mismatch:
            used_fallback = True
            row_repaired_from_current_package = True
            rows_rebuilt_from_scene_plan_count += 1
            if route_mismatch:
                route_mismatch_count += 1
                validation_errors.append(f"route_mismatch:{scene_id}")
            if semantic_mismatch:
                semantic_mismatch_count += 1
                semantic_mismatch_scene_ids.append(scene_id)
                validation_errors.append(f"semantic_mismatch:{scene_id}")
            if mismatch_reasons:
                mismatch_reason_by_scene[scene_id] = list(dict.fromkeys(mismatch_reasons))
            base = {}
        actual_route = expected_route

        photo_prompt = str(base.get("photo_prompt") or "").strip()
        video_prompt = str(base.get("video_prompt") or "").strip()
        positive_video_prompt = str(base.get("positive_video_prompt") or "").strip()
        negative_video_prompt = str(base.get("negative_video_prompt") or "").strip()

        if actual_route == "first_last":
            semantic_beat = _resolve_first_last_semantic_beat(scene)
            start_image_prompt_base = str(base.get("start_image_prompt") or "").strip()
            end_image_prompt_base = str(base.get("end_image_prompt") or "").strip()
            image_alias_candidates = (
                [end_image_prompt_base, start_image_prompt_base]
                if semantic_beat in {"afterimage", "release"}
                else [start_image_prompt_base, end_image_prompt_base]
            )
            if not photo_prompt:
                for candidate in image_alias_candidates:
                    if candidate:
                        photo_prompt = candidate
                        break
            if not video_prompt and positive_video_prompt:
                video_prompt = positive_video_prompt

        if not photo_prompt:
            missing_photo_count += 1
            missing_photo_scene_ids.append(scene_id)
            missing_field_by_scene.setdefault(scene_id, []).append("photo_prompt")
            used_fallback = True
            row_repaired_from_current_package = True
            photo_prompt = str(fallback_row.get("photo_prompt") or "")

        if not video_prompt:
            missing_video_count += 1
            missing_video_scene_ids.append(scene_id)
            missing_field_by_scene.setdefault(scene_id, []).append("video_prompt")
            used_fallback = True
            row_repaired_from_current_package = True
            video_prompt = str(fallback_row.get("video_prompt") or "")

        if actual_route == "first_last":
            positive_video_prompt = positive_video_prompt or video_prompt or str(fallback_row.get("positive_video_prompt") or "")
            video_prompt = positive_video_prompt or video_prompt
            negative_video_prompt = (
                negative_video_prompt
                or str(base.get("negative_prompt") or "").strip()
                or str(fallback_row.get("negative_video_prompt") or "").strip()
                or _FIRST_LAST_NEGATIVE_PROMPT
            )
            negative_prompt = negative_video_prompt
        elif actual_route == "ia2v":
            negative_video_prompt = negative_video_prompt or str(base.get("negative_prompt") or "").strip() or _LIP_SYNC_NEGATIVE_PROMPT
            positive_video_prompt = positive_video_prompt or video_prompt
            negative_prompt = negative_video_prompt
        else:
            positive_video_prompt = positive_video_prompt or video_prompt
            negative_video_prompt = negative_video_prompt or str(base.get("negative_prompt") or "").strip() or _GLOBAL_NEGATIVE_PROMPT
            negative_prompt = negative_video_prompt
        if has_human_scene and actual_route != "ia2v":
            for lock_clause in (_GLOBAL_HERO_IDENTITY_LOCK, _BODY_CONTINUITY_LOCK, _WARDROBE_CONTINUITY_LOCK):
                photo_prompt = _append_prompt_clause(photo_prompt, lock_clause)
                video_prompt = _append_prompt_clause(video_prompt, lock_clause)
                positive_video_prompt = _append_prompt_clause(positive_video_prompt or video_prompt, lock_clause)
            negative_prompt = _append_prompt_clause(negative_prompt, _IDENTITY_WARDROBE_NEGATIVE)
            negative_video_prompt = _append_prompt_clause(negative_video_prompt or negative_prompt, _IDENTITY_WARDROBE_NEGATIVE)
        if actual_route != "ia2v" and carried_active_scene and "close to body" not in video_prompt.lower():
            video_prompt = (
                f"{video_prompt} Keep the same owner-bound carried object close to body across transit/evasion/release beats, "
                "even when it is not the frame center; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
            ).strip()
        if actual_route != "ia2v" and held_active_scene and "owner-bound held object" not in video_prompt.lower():
            video_prompt = (
                f"{video_prompt} Keep the same owner-bound held object continuous across transit/evasion/release beats, "
                "with readable handling only; one hand/handling attention remains committed so posture, pace, and route decisions stay constrained, "
                "and this is not a replaceable random prop even when off center."
            ).strip()
        video_prompt, video_sanitized = _sanitize_positive_prompt(video_prompt, negative_prompt)
        if actual_route != "ia2v" and carried_active_scene and "close to body" not in positive_video_prompt.lower():
            positive_video_prompt = (
                f"{(positive_video_prompt or video_prompt)} Keep the same owner-bound carried object close to body across transit/evasion/release beats, "
                "even when it is not the frame center; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
            ).strip()
        if actual_route != "ia2v" and held_active_scene and "owner-bound held object" not in positive_video_prompt.lower():
            positive_video_prompt = (
                f"{(positive_video_prompt or video_prompt)} Keep the same owner-bound held object continuous across transit/evasion/release beats, "
                "with readable handling only; one hand/handling attention remains committed so posture, pace, and route decisions stay constrained, "
                "and this is not a replaceable random prop even when off center."
            ).strip()
        positive_video_prompt, positive_sanitized = _sanitize_positive_prompt(positive_video_prompt or video_prompt, negative_prompt)
        if video_sanitized:
            positive_negative_leak_stripped_count += 1
        if positive_sanitized:
            positive_negative_leak_stripped_count += 1
        if not (
            str(base.get("negative_prompt") or "").strip() or str(base.get("negative_video_prompt") or "").strip()
        ):
            used_fallback = True

        prompt_notes = _safe_dict(base.get("prompt_notes"))
        normalized_notes = _prompt_notes_template(actual_route)
        normalized_notes.update(
            {
                "shot_intent": str(prompt_notes.get("shot_intent") or fallback_row["prompt_notes"].get("shot_intent") or ""),
                "continuity_anchor": str(
                    prompt_notes.get("continuity_anchor") or fallback_row["prompt_notes"].get("continuity_anchor") or ""
                ) + ("; same owner-bound carried object stays close to body through transit/evasion/release (not a replaceable random prop)" if carried_active_scene else ""),
                "world_anchor": str(prompt_notes.get("world_anchor") or fallback_row["prompt_notes"].get("world_anchor") or ""),
                "identity_anchor": str(prompt_notes.get("identity_anchor") or fallback_row["prompt_notes"].get("identity_anchor") or ""),
                "lighting_anchor": str(prompt_notes.get("lighting_anchor") or fallback_row["prompt_notes"].get("lighting_anchor") or ""),
                "motion_safety": str(prompt_notes.get("motion_safety") or fallback_row["prompt_notes"].get("motion_safety") or ""),
                "audio_driven": bool(prompt_notes.get("audio_driven")) if "audio_driven" in prompt_notes else (actual_route == "ia2v"),
                "shot_scale": str(prompt_notes.get("shot_scale") or scene.get("shot_scale") or fallback_row["prompt_notes"].get("shot_scale") or ""),
                "camera_intimacy": str(prompt_notes.get("camera_intimacy") or scene.get("camera_intimacy") or fallback_row["prompt_notes"].get("camera_intimacy") or ""),
                "performance_openness": str(
                    prompt_notes.get("performance_openness")
                    or scene.get("performance_openness")
                    or fallback_row["prompt_notes"].get("performance_openness")
                    or ""
                ),
                "visual_event_type": str(prompt_notes.get("visual_event_type") or scene.get("visual_event_type") or fallback_row["prompt_notes"].get("visual_event_type") or ""),
                "repeat_variation_rule": str(
                    prompt_notes.get("repeat_variation_rule")
                    or scene.get("repeat_variation_rule")
                    or fallback_row["prompt_notes"].get("repeat_variation_rule")
                    or ""
                ),
                "motion_risk": _safe_dict(prompt_notes.get("motion_risk")) or _safe_dict(scene.get("motion_risk")) or _safe_dict(fallback_row["prompt_notes"].get("motion_risk")),
            }
        )
        if held_active_scene:
            normalized_notes["continuity_anchor"] = (
                f"{normalized_notes['continuity_anchor']}; same owner-bound held object persists across transit/evasion/release with readable handling continuity (not replaceable, survives off-center framing)"
            ).strip("; ")
        if actual_route == "ia2v":
            normalized_notes["audio_driven"] = True
        if actual_route == "first_last":
            start_image_prompt = str(base.get("start_image_prompt") or "").strip() or str(fallback_row.get("start_image_prompt") or "").strip()
            end_image_prompt = str(base.get("end_image_prompt") or "").strip() or str(fallback_row.get("end_image_prompt") or "").strip()
            first_state = str(
                prompt_notes.get("first_state") or fallback_row["prompt_notes"].get("first_state") or "start of one controlled action"
            ).strip()
            last_state = str(
                prompt_notes.get("last_state") or fallback_row["prompt_notes"].get("last_state") or "completion of the same controlled action"
            ).strip()
            delta_scene_row = dict(scene)
            delta_scene_row["first_state"] = first_state
            delta_scene_row["last_state"] = last_state
            attached_prop_token = _detect_attached_prop_token(
                start_image_prompt,
                end_image_prompt,
                first_state,
                last_state,
                str(scene.get("transition_action") or ""),
                str(scene.get("scene_goal") or ""),
                str(scene.get("frame_description") or ""),
                photo_prompt,
                str(scene.get("scene_function") or ""),
            )
            first_state, last_state, visual_delta = _build_first_last_visual_delta(
                scene_plan_row=delta_scene_row,
                primary_role=_build_human_subject_label(role_row, story_core, scene),
                attached_prop_token=attached_prop_token,
            )
            first_last_mode = str(scene.get("first_last_mode") or prompt_notes.get("first_last_mode") or "").strip().lower()
            if first_last_mode not in FIRST_LAST_MODES:
                first_last_mode = "camera_settle"
            continuity_mode = str(
                prompt_notes.get("continuity_mode")
                or fallback_row["prompt_notes"].get("continuity_mode")
                or _resolve_first_last_continuity_mode(scene)
            ).strip().lower() or "controlled_micro_transition"
            semantic_beat = str(
                prompt_notes.get("semantic_beat")
                or fallback_row["prompt_notes"].get("semantic_beat")
                or _resolve_first_last_semantic_beat(scene)
            ).strip().lower() or "transition_beat"
            strict_start, strict_end, strict_positive, strict_negative = _build_first_last_prompt_pair(
                primary_role=_build_human_subject_label(role_row, story_core, scene),
                scene_space=_trim_sentence(str(world_continuity.get("environment_family") or "the same fixed scene space"), max_len=90),
                first_state=first_state,
                last_state=last_state,
                visual_delta=visual_delta,
                attached_prop_token=attached_prop_token,
                first_last_mode=first_last_mode,
                scene_function=str(scene.get("scene_function") or ""),
                emotional_intent=str(scene.get("emotional_intent") or ""),
                continuity_mode=continuity_mode,
                semantic_beat=semantic_beat,
            )
            start_image_prompt = strict_start
            end_image_prompt = strict_end
            positive_video_prompt = strict_positive
            video_prompt = strict_positive
            negative_video_prompt = strict_negative
            negative_prompt = strict_negative
            normalized_notes["transition_contract"] = "controlled_micro_transition"
            normalized_notes["first_state"] = first_state
            normalized_notes["last_state"] = last_state
            normalized_notes["first_last_mode"] = first_last_mode
            normalized_notes["continuity_mode"] = continuity_mode
            normalized_notes["semantic_beat"] = semantic_beat
            normalized_notes["same_world_required"] = bool(
                prompt_notes.get("same_world_required") if "same_world_required" in prompt_notes else True
            )
            normalized_notes["same_outfit_required"] = bool(
                prompt_notes.get("same_outfit_required") if "same_outfit_required" in prompt_notes else True
            )
            normalized_notes["same_lighting_required"] = bool(
                prompt_notes.get("same_lighting_required") if "same_lighting_required" in prompt_notes else True
            )
            normalized_notes["same_camera_family_required"] = bool(
                prompt_notes.get("same_camera_family_required") if "same_camera_family_required" in prompt_notes else True
            )
            normalized_notes["one_transition_only"] = True
            normalized_notes["prop_attachment_required"] = bool(attached_prop_token)
            normalized_notes["attached_prop"] = attached_prop_token
            if not start_image_prompt or not end_image_prompt:
                used_fallback = True
                validation_errors.append(f"first_last_image_prompt_missing:{scene_id}")
        else:
            start_image_prompt = ""
            end_image_prompt = ""
        if actual_route == "i2v":
            family = str(scene.get("i2v_motion_family") or "").strip()
            if family not in I2V_MOTION_FAMILIES:
                family = "baseline_forward_walk"
                i2v_unknown_family_fallback_count += 1
            i2v_prompt_family_counts[family] = i2v_prompt_family_counts.get(family, 0) + 1
            i2v_scene_row = dict(scene)
            i2v_scene_row["i2v_motion_family"] = family
            bundle = _build_i2v_prompt_bundle(
                role_label=_build_human_subject_label(role_row, story_core, scene),
                scene_plan_row=i2v_scene_row,
                world_anchor=anchors["world_anchor"],
                lighting_anchor=anchors["lighting_anchor"],
                identity_anchor=anchors["identity_anchor"],
            )
            photo_prompt = str(bundle.get("photo_prompt") or photo_prompt)
            video_prompt = str(bundle.get("video_prompt") or video_prompt)
            positive_video_prompt = str(bundle.get("positive_video_prompt") or video_prompt)
            negative_video_prompt = str(bundle.get("negative_video_prompt") or negative_video_prompt or _GLOBAL_NEGATIVE_PROMPT)
            negative_prompt = str(bundle.get("negative_prompt") or negative_video_prompt or _GLOBAL_NEGATIVE_PROMPT)
            normalized_notes["i2v_motion_family"] = family
            normalized_notes["pace_class"] = str(scene.get("pace_class") or "purposeful")
            normalized_notes["camera_pattern"] = str(scene.get("camera_pattern") or "stable_follow")
            normalized_notes["reveal_target"] = str(scene.get("reveal_target") or "none")
            normalized_notes["parallax_required"] = bool(scene.get("parallax_required"))
            normalized_notes["allow_head_turn"] = bool(scene.get("allow_head_turn"))
            normalized_notes["allow_simple_hand_motion"] = bool(scene.get("allow_simple_hand_motion", True))
            normalized_notes["forbid_complex_hand_motion"] = bool(scene.get("forbid_complex_hand_motion", True))
            normalized_notes["forbid_slow_motion_feel"] = bool(scene.get("forbid_slow_motion_feel", True))
            normalized_notes["forbid_bullet_time"] = bool(scene.get("forbid_bullet_time", True))
            normalized_notes["forbid_stylized_action"] = bool(scene.get("forbid_stylized_action", True))
            normalized_notes["require_real_time_pacing"] = bool(scene.get("require_real_time_pacing", True))
            normalized_notes["max_camera_intensity"] = str(scene.get("max_camera_intensity") or "low")
            normalized_notes["i2v_prompt_duration_hint_sec"] = _round3(scene.get("i2v_prompt_duration_hint_sec"))
            normalized_notes["template_built"] = True
            i2v_template_rebuilt_count += 1
            i2v_template_override_applied = True
        if actual_route != "ia2v" and carried_active_scene and "close to body" not in video_prompt.lower():
            video_prompt = (
                f"{video_prompt} Keep the same owner-bound carried object close to body across transit/evasion/release beats, "
                "even when it is not the frame center; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
            ).strip()
        if actual_route != "ia2v" and held_active_scene and "owner-bound held object" not in video_prompt.lower():
            video_prompt = (
                f"{video_prompt} Keep the same owner-bound held object continuous across transit/evasion/release beats, "
                "with readable handling only; one hand/handling attention remains committed so posture, pace, and route decisions stay constrained, "
                "and this is not a replaceable random prop even when off center."
            ).strip()
        video_prompt, video_sanitized = _sanitize_positive_prompt(video_prompt, negative_prompt)
        if actual_route != "ia2v" and carried_active_scene and "close to body" not in positive_video_prompt.lower():
            positive_video_prompt = (
                f"{(positive_video_prompt or video_prompt)} Keep the same owner-bound carried object close to body across transit/evasion/release beats, "
                "even when it is not the frame center; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
            ).strip()
        if actual_route != "ia2v" and held_active_scene and "owner-bound held object" not in positive_video_prompt.lower():
            positive_video_prompt = (
                f"{(positive_video_prompt or video_prompt)} Keep the same owner-bound held object continuous across transit/evasion/release beats, "
                "with readable handling only; one hand/handling attention remains committed so posture, pace, and route decisions stay constrained, "
                "and this is not a replaceable random prop even when off center."
            ).strip()
        positive_video_prompt, positive_sanitized = _sanitize_positive_prompt(positive_video_prompt or video_prompt, negative_prompt)
        photo_prompt, photo_sanitized = _sanitize_positive_prompt(photo_prompt, negative_prompt)
        if video_sanitized:
            positive_negative_leak_stripped_count += 1
        if positive_sanitized:
            positive_negative_leak_stripped_count += 1
        if photo_sanitized:
            positive_negative_leak_stripped_count += 1
        if actual_route == "first_last":
            start_image_prompt, start_sanitized = _sanitize_positive_prompt(start_image_prompt, negative_prompt)
            end_image_prompt, end_sanitized = _sanitize_positive_prompt(end_image_prompt, negative_prompt)
            if has_human_scene:
                for lock_clause in (_GLOBAL_HERO_IDENTITY_LOCK, _BODY_CONTINUITY_LOCK, _WARDROBE_CONTINUITY_LOCK):
                    start_image_prompt = _append_prompt_clause(start_image_prompt, lock_clause)
                    end_image_prompt = _append_prompt_clause(end_image_prompt, lock_clause)
            if start_sanitized:
                positive_negative_leak_stripped_count += 1
            if end_sanitized:
                positive_negative_leak_stripped_count += 1
        if _is_high_motion_risk(scene) and actual_route in {"i2v", "ia2v"}:
            if actual_route == "i2v":
                simplify_suffix = (
                    " Keep one readable motion line only. Avoid complex hand choreography and fine-motor prop action."
                )
                video_prompt = f"{video_prompt.rstrip('. ')}.{simplify_suffix}".strip()
                positive_video_prompt = f"{positive_video_prompt.rstrip('. ')}.{simplify_suffix}".strip()
            else:
                simplify_suffix = (
                    "Keep only one tiny micro-action: slight forward lean or subtle gaze shift. "
                    "Keep props and background passive as soft story context."
                )
                video_prompt = f"{video_prompt.rstrip('. ')}. {simplify_suffix}".strip()
                positive_video_prompt = f"{positive_video_prompt.rstrip('. ')}. {simplify_suffix}".strip()
            normalized_notes["risk_simplified"] = True
        else:
            normalized_notes["risk_simplified"] = False

        scene_blob = _scene_semantic_blob(scene)
        season_world_clause = (
            "Preserve current world continuity, season continuity, weather continuity, and environment family from the established package. "
            "Do not introduce a different season or contradictory weather."
        )
        anti_duplicate_clause = "Differentiate this scene clearly from adjacent scenes in shot purpose, composition, and subject emphasis."
        adjacent_separation_clause = (
            "Adjacent scene separation is mandatory: change at least primary subject presence, composition, zone, human density, and visual function so world-detail cutaways do not look like near-duplicate base plates of performer shots."
        )
        world_detail_human_presence_clause = (
            "Prefer socially legible human presence and lived-in contemporary world texture over empty background spaces, unless the scene explicitly calls for isolation or emptiness."
        )
        world_detail_city_identity_clause = (
            "For world-detail/cutaway city atmosphere, prioritize active public space, populated street texture, social movement, and recognizable contemporary urban identity over generic warehouse/industrial wallpaper."
        )
        world_detail_subject_hierarchy_clause = (
            "For environment/cutaway/world-detail scenes, prioritize socially readable urban life, contemporary populated city texture, and meaningful human presence; use labor/cargo/manual handling only when explicitly implied by the scene text."
        )
        world_cast_clause = (
            "Background figures should match the established world's social role and atmosphere, reading as tense local presence, watchful groups, intimidating entourage, socially charged bystanders, or guarded street presence when tone is dangerous/criminal, not labor-only documentary workers unless explicitly required by the scene."
        )
        cast_tone_tokens = ("underworld", "criminal", "crime", "gang", "smuggling", "threat", "dangerous", "tense")
        explicit_labor_tokens = (
            "labor documentary",
            "documentary labor",
            "industrial labor",
            "workshift documentary",
            "union labor",
            "cargo handling",
            "container loading",
            "manual loading",
            "dock labor",
        )
        scene_is_cutaway = (
            actual_route == "i2v"
            and ("environment" in scene_blob or "cutaway" in scene_blob or str(scene.get("visual_focus_role") or "").strip().lower() == "environment")
            and not bool(scene.get("speaker_role"))
        )
        scene_is_performance = actual_route == "ia2v"
        if scene_is_cutaway:
            cutaway_clause = (
                "Environment-first cutaway: no main performer visible, or performer remains non-dominant peripheral presence only."
            )
            photo_prompt = _append_compact_clauses(
                photo_prompt,
                [
                    season_world_clause,
                    cutaway_clause,
                    anti_duplicate_clause,
                    adjacent_separation_clause,
                    world_detail_subject_hierarchy_clause,
                    world_detail_human_presence_clause,
                    world_detail_city_identity_clause,
                ],
            )
            video_prompt = _append_compact_clauses(
                video_prompt,
                [
                    season_world_clause,
                    cutaway_clause,
                    anti_duplicate_clause,
                    adjacent_separation_clause,
                    world_detail_subject_hierarchy_clause,
                    world_detail_human_presence_clause,
                    world_detail_city_identity_clause,
                ],
            )
            positive_video_prompt = _append_compact_clauses(
                positive_video_prompt or video_prompt,
                [
                    season_world_clause,
                    cutaway_clause,
                    anti_duplicate_clause,
                    adjacent_separation_clause,
                    world_detail_subject_hierarchy_clause,
                    world_detail_human_presence_clause,
                    world_detail_city_identity_clause,
                ],
            )
        elif scene_is_performance:
            performance_clause = "Performer-first performance shot: hero performer is clearly visible and dominant; avoid environment-only composition."
            photo_prompt = _append_compact_clauses(photo_prompt, [season_world_clause, performance_clause, anti_duplicate_clause, adjacent_separation_clause])
            video_prompt = _append_compact_clauses(video_prompt, [season_world_clause, performance_clause, anti_duplicate_clause, adjacent_separation_clause])
            positive_video_prompt = _append_compact_clauses(
                positive_video_prompt or video_prompt,
                [season_world_clause, performance_clause, anti_duplicate_clause, adjacent_separation_clause],
            )
        else:
            photo_prompt = _append_compact_clauses(photo_prompt, [season_world_clause, anti_duplicate_clause, adjacent_separation_clause])
            video_prompt = _append_compact_clauses(video_prompt, [season_world_clause, anti_duplicate_clause, adjacent_separation_clause])
            positive_video_prompt = _append_compact_clauses(
                positive_video_prompt or video_prompt,
                [season_world_clause, anti_duplicate_clause, adjacent_separation_clause],
            )

        if any(token in scene_blob for token in cast_tone_tokens) and not any(token in scene_blob for token in explicit_labor_tokens):
            photo_prompt = _append_compact_clauses(photo_prompt, [world_cast_clause])
            video_prompt = _append_compact_clauses(video_prompt, [world_cast_clause])
            positive_video_prompt = _append_compact_clauses(positive_video_prompt or video_prompt, [world_cast_clause])

        hard_constraints = _safe_dict(global_contract.get("hard_constraints"))
        image_contract_clauses: list[str] = []
        if required_world_anchor:
            image_contract_clauses.append("Keep required world anchor continuity; no world-family drift")
        if required_props:
            props_clause = " Keep required continuity prop identity consistent across frames; do not replace with new key prop."
            video_prompt = f"{video_prompt.rstrip('. ')}.{props_clause}".strip()
            positive_video_prompt = f"{(positive_video_prompt or video_prompt).rstrip('. ')}.{props_clause}".strip()
            image_contract_clauses.append("Keep required continuity prop identity consistent; do not replace key continuity props")
            normalized_notes["continuity_anchor"] = (
                f"{normalized_notes.get('continuity_anchor', '').strip('; ')}; required continuity props: {', '.join(required_props)}"
            ).strip("; ")
        if forbidden_actor_ids and bool(hard_constraints.get("must_not_invent_cast", True)):
            cast_clause = " Do not introduce extra identifiable cast; keep only contract-authorized actors."
            video_prompt = f"{video_prompt.rstrip('. ')}.{cast_clause}".strip()
            positive_video_prompt = f"{(positive_video_prompt or video_prompt).rstrip('. ')}.{cast_clause}".strip()
            image_contract_clauses.append("Do not introduce extra identifiable cast; keep only contract-authorized actors")
            normalized_notes["cast_constraint"] = "must_not_invent_cast"
        if presence_clause:
            video_prompt = f"{video_prompt.rstrip('. ')}. {presence_clause}".strip()
            positive_video_prompt = f"{(positive_video_prompt or video_prompt).rstrip('. ')}. {presence_clause}".strip()
            image_contract_clauses.append(presence_clause)
            normalized_notes["presence_policy"] = str(presence_policy.get("presence_policy") or "")
            normalized_notes["presence_clause"] = presence_clause
        photo_prompt = _append_compact_clauses(photo_prompt, image_contract_clauses)
        if actual_route == "first_last":
            start_image_prompt = _append_compact_clauses(start_image_prompt, image_contract_clauses)
            end_image_prompt = _append_compact_clauses(end_image_prompt, image_contract_clauses)
        if bool(scene_contract.get("allow_scene_local_props", True)):
            normalized_notes["scene_local_props_policy"] = "decor_allowed_non_continuity"
        else:
            normalized_notes["scene_local_props_policy"] = "decor_restricted"
        if actual_route == "ia2v":
            semantic_context = " ".join(
                [
                    str(scene.get("narrative_function") or ""),
                    str(scene.get("scene_goal") or ""),
                    str(scene.get("subject_priority") or ""),
                    str(scene.get("framing") or ""),
                    str(scene.get("emotional_intent") or ""),
                    str(_safe_dict(scene.get("composition")).get("framing") or ""),
                    str(_safe_dict(scene.get("composition")).get("subject_priority") or ""),
                ]
            )
            still_clauses = build_ia2v_readability_clauses(existing_text=photo_prompt, semantic_context=semantic_context)
            motion_clauses = build_ia2v_readability_clauses(
                existing_text=f"{video_prompt} {(positive_video_prompt or '')}",
                semantic_context=semantic_context,
            )
            photo_prompt = _append_compact_clauses(photo_prompt, still_clauses)
            video_prompt = _append_compact_clauses(video_prompt, motion_clauses)
            positive_video_prompt = _append_compact_clauses((positive_video_prompt or video_prompt), motion_clauses)
            speaker_role_value = str(scene.get("speaker_role") or "").strip() or "character_1"
            emotional_tone = str(scene.get("emotional_intent") or "").strip() or "raw emotional release"
            photo_prompt = (
                f"Story-grounded singing-ready frame of the same performer ({speaker_role_value}) in the current scene world with {emotional_tone}. "
                "Mouth open or slightly open in a natural singing shape; emotion and performance intent are clearly readable. "
                "Face and mouth stay readable and important, with performer-first focus."
            )
            video_prompt = _IA2V_VIDEO_PROMPT_CANON
            positive_video_prompt = _IA2V_VIDEO_PROMPT_CANON
            tone_clause = _trim_sentence(
                f"Tone accent: {emotional_tone}; keep it in vocal facial performance only, not action choreography.",
                max_len=170,
            )
            video_prompt = _append_prompt_clause(video_prompt, tone_clause)
            positive_video_prompt = _append_prompt_clause(positive_video_prompt, tone_clause)
            spoken_line = str(scene.get("spoken_line") or "").strip()
            if spoken_line:
                line_clause = _trim_sentence(f"Vocal phrase anchor: {spoken_line}", max_len=160)
                video_prompt = _append_prompt_clause(video_prompt, line_clause)
                positive_video_prompt = _append_prompt_clause(positive_video_prompt, line_clause)
            low_energy_markers = {"low", "minimal", "restrained", "subtle"}
            performance_openness = str(scene.get("performance_openness") or "").strip().lower()
            energy_alignment = str(_safe_dict(scene.get("visual_motion")).get("energy_alignment") or "").strip().lower()
            if performance_openness in low_energy_markers or energy_alignment in low_energy_markers:
                low_energy_clause = (
                    "Allow subtle expressive hand gestures, shoulder emphasis, and torso rhythm that support emotional delivery, while keeping the performance controlled and grounded."
                )
                video_prompt = _append_prompt_clause(video_prompt, low_energy_clause)
                positive_video_prompt = _append_prompt_clause(positive_video_prompt, low_energy_clause)
            photo_prompt = _strip_ia2v_positive_noise(photo_prompt)
            video_prompt = _strip_ia2v_positive_noise(video_prompt)
            positive_video_prompt = _strip_ia2v_positive_noise(positive_video_prompt)
            negative_video_prompt = _clean_ia2v_negative_prompt(negative_video_prompt or negative_prompt)
            negative_prompt = negative_video_prompt
        if enforce_shared_space_rule:
            missing_roles = [role for role in must_be_visible_roles if not _text_mentions_role(photo_prompt, role)]
            if missing_roles:
                shared_space_missing_segments.append(scene_id)
                used_fallback = True
                photo_prompt = _append_prompt_clause(photo_prompt, _shared_space_enforcement_clause(must_be_visible_roles))
            offscreen_violations = [
                role
                for role in must_be_visible_roles
                if role not in may_be_offscreen_roles
                and bool(re.search(rf"\b{re.escape(role)}\b.{0,40}\b(offscreen|off-screen|not visible|outside frame)\b", photo_prompt.lower()))
            ]
            if offscreen_violations:
                offscreen_violation_segments.append(scene_id)
                validation_errors.append(f"must_be_visible_offscreen_violation:{scene_id}")
            if actual_route == "first_last":
                start_missing = [role for role in must_be_visible_roles if not _text_mentions_role(start_image_prompt, role)]
                end_missing = [role for role in must_be_visible_roles if not _text_mentions_role(end_image_prompt, role)]
                if start_missing:
                    shared_space_missing_segments.append(f"{scene_id}:start")
                    used_fallback = True
                    start_image_prompt = _append_prompt_clause(
                        start_image_prompt,
                        _shared_space_enforcement_clause(must_be_visible_roles),
                    )
                if end_missing:
                    shared_space_missing_segments.append(f"{scene_id}:end")
                    used_fallback = True
                    end_image_prompt = _append_prompt_clause(
                        end_image_prompt,
                        _shared_space_enforcement_clause(must_be_visible_roles),
                    )

        scene_out = {
            "scene_id": scene_id,
            "route": actual_route,
            "photo_prompt": _enrich_prompt_with_anchor(photo_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
            "video_prompt": "",
            "positive_video_prompt": "",
            "negative_video_prompt": negative_video_prompt,
            "start_image_prompt": (start_image_prompt[:900] if actual_route == "first_last" else ""),
            "end_image_prompt": (end_image_prompt[:900] if actual_route == "first_last" else ""),
            "negative_prompt": negative_prompt,
            "prompt_notes": normalized_notes,
        }
        semantics_lock = _scene_plan_semantics_lock(scene)
        final_route = semantics_lock["route"] if semantics_lock["route"] in ALLOWED_ROUTES else actual_route
        scene_out["route"] = final_route
        if final_route == "first_last":
            scene_out["video_prompt"] = video_prompt[:900]
            scene_out["positive_video_prompt"] = (positive_video_prompt or video_prompt)[:900]
        elif final_route == "ia2v":
            scene_out["video_prompt"] = video_prompt[:900]
            scene_out["positive_video_prompt"] = (positive_video_prompt or video_prompt)[:900]
        else:
            scene_out["video_prompt"] = _enrich_prompt_with_anchor(video_prompt, anchors["identity_anchor"], anchors["world_anchor"])
            scene_out["positive_video_prompt"] = _enrich_prompt_with_anchor(
                positive_video_prompt or video_prompt,
                anchors["identity_anchor"],
                anchors["world_anchor"],
            )
        scene_out["prompt_notes"].update(semantics_lock)
        if final_route == "ia2v":
            apply_ia2v_lipsync_canon_to_prompt_row(scene_out, source_scene=scene)
        scene_out["prompt_notes"]["row_repaired_from_scene_plan"] = bool(row_repaired_from_current_package)
        if row_repaired_from_current_package:
            repaired_from_current_package_count += 1
            repaired_scene_ids.append(scene_id)
        scenes.append(scene_out)

    normalized = {
        "plan_version": SCENE_PROMPTS_PROMPT_VERSION,
        "mode": "clip",
        "scenes": scenes,
        "global_prompt_rules": _safe_list(raw.get("global_prompt_rules")) or list(_GLOBAL_PROMPT_RULES),
    }
    for row in _safe_list(normalized.get("scenes")):
        apply_ia2v_lipsync_canon_to_prompt_row(_safe_dict(row), source_scene=row)
    validation_error = ";".join(dict.fromkeys(validation_errors))
    ia2v_audio_driven_count = sum(
        1 for row in scenes if str(row.get("route") or "") == "ia2v" and bool(_safe_dict(row.get("prompt_notes")).get("audio_driven"))
    )
    ia2v_rows = [row for row in scenes if str(row.get("route") or "") == "ia2v"]
    ia2v_photo_mouth_ready = bool(ia2v_rows) and all(
        (
            "mouth" in str(row.get("photo_prompt") or "").lower()
            and ("open" in str(row.get("photo_prompt") or "").lower() or "sing" in str(row.get("photo_prompt") or "").lower())
        )
        for row in ia2v_rows
    )
    ia2v_photo_emotion_readable = bool(ia2v_rows) and all(
        "emotion" in str(row.get("photo_prompt") or "").lower() or "emotional" in str(row.get("photo_prompt") or "").lower()
        for row in ia2v_rows
    )
    ia2v_video_prompt_has_singing_mechanics = bool(ia2v_rows) and all(
        all(token in str(row.get("video_prompt") or "").lower() for token in ("sing", "lip", "mouth"))
        for row in ia2v_rows
    )
    normalization_diag = {
        "rows_source_count": len(scene_rows),
        "rows_model_count": len(_safe_list(raw.get("scenes"))),
        "rows_normalized_count": len(scenes),
        "repaired_from_current_package_count": repaired_from_current_package_count,
        "scene_prompts_repaired_scene_ids": repaired_scene_ids,
        "unrelated_rows_discarded_count": unrelated_rows_discarded_count,
        "scene_prompts_route_mismatch_count": route_mismatch_count,
        "scene_prompts_semantic_mismatch_count": semantic_mismatch_count,
        "scene_prompts_semantic_mismatch_scene_ids": semantic_mismatch_scene_ids,
        "scene_prompts_missing_photo_scene_ids": missing_photo_scene_ids,
        "scene_prompts_missing_video_scene_ids": missing_video_scene_ids,
        "scene_prompts_missing_field_by_scene": {
            scene_id: list(dict.fromkeys(fields))
            for scene_id, fields in missing_field_by_scene.items()
        },
        "scene_prompts_mismatch_reason_by_scene": mismatch_reason_by_scene,
        "scene_prompts_rows_rebuilt_from_scene_plan_count": rows_rebuilt_from_scene_plan_count,
        "scene_prompts_positive_negative_leak_stripped_count": positive_negative_leak_stripped_count,
        "i2v_template_rebuilt_count": i2v_template_rebuilt_count,
        "i2v_unknown_family_fallback_count": i2v_unknown_family_fallback_count,
        "i2v_prompt_family_counts": i2v_prompt_family_counts,
        "i2v_template_override_applied": i2v_template_override_applied,
        "scene_prompts_shared_space_rule_applied": enforce_shared_space_rule,
        "scene_prompts_must_be_visible_roles": must_be_visible_roles,
        "scene_prompts_shared_space_missing_segments": list(dict.fromkeys(shared_space_missing_segments)),
        "scene_prompts_offscreen_violation_segments": list(dict.fromkeys(offscreen_violation_segments)),
        "ia2vPhotoMouthReady": bool(ia2v_photo_mouth_ready),
        "ia2vPhotoEmotionReadable": bool(ia2v_photo_emotion_readable),
        "ia2vVideoPromptHasSingingMechanics": bool(ia2v_video_prompt_has_singing_mechanics),
        "stage_source": "current_package",
    }
    return (
        normalized,
        used_fallback,
        validation_error,
        missing_photo_count,
        missing_video_count,
        ia2v_audio_driven_count,
        route_mismatch_count,
        semantic_mismatch_count,
        rows_rebuilt_from_scene_plan_count,
        positive_negative_leak_stripped_count,
        normalization_diag,
    )


PROMPTS_ERROR_CODES = {
    "PROMPTS_SCHEMA_INVALID",
    "PROMPTS_SEGMENT_ID_MISMATCH",
    "PROMPTS_ROUTE_MUTATION",
    "PROMPTS_ROLE_OMISSION",
    "PROMPTS_IDENTITY_DRIFT",
    "PROMPTS_WORLD_DRIFT",
    "PROMPTS_TECHNICAL_TAGGING",
    "PROMPTS_QUALITY_BUZZWORDS",
    "PROMPTS_CAMERA_LEAKAGE",
    "PROMPTS_ROUTE_DELIVERY_LEAKAGE",
    "PROMPTS_MISSING_TRANSITION_DESCRIPTION",
}

_TECHNICAL_TAG_PATTERNS = (
    "fps",
    "seed",
    "renderer",
    "workflow",
    "model_id",
    "ltx",
    "lens",
    "iso",
    "shutter",
)
_QUALITY_BUZZWORDS = (
    "8k",
    "ultra hd",
    "masterpiece",
    "best quality",
    "cinematic quality",
)
_CAMERA_LEAK_PATTERNS = ("camera movement", "dolly", "gimbal", "crane", "rack focus")
_ROUTE_DELIVERY_PATTERNS = ("route payload", "delivery payload", "api call", "json rpc")
_GENERIC_SUBJECT_TOKENS = ("person", "someone", "subject", "individual", "figure", "human")
_IDENTITY_DRIFT_TOKENS = (
    "different subject",
    "new performer",
    "new hero",
    "another woman",
    "another man",
    "new character",
    "identity change",
    "different face",
    "different outfit",
)
_WORLD_DRIFT_TOKENS = (
    "fantasy",
    "alien",
    "spaceship",
    "medieval castle",
    "dragon",
    "cyberpunk neon city",
    "post apocalyptic",
    "new world",
    "different world",
)

_LOCAL_ZONE_FALLBACK_SEQUENCE = (
    "entry transition pocket",
    "reflective transition pocket",
    "threshold transition pocket",
    "face-light pivot pocket",
    "anticipation edge pocket",
    "peak-threshold pocket",
    "retreat release pocket",
    "lingering observation pocket",
)

_LOCAL_ZONE_HINT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entry transition pocket", ("entrance", "arrival", "approach", "door", "intro", "shadow", "hide", "conceal")),
    ("reflective transition pocket", ("reflect", "reflection", "mirror", "glass", "window", "chrome", "gloss", "shimmer")),
    ("threshold transition pocket", ("threshold", "border", "edge", "crossing", "handoff", "pivot")),
    ("face-light pivot pocket", ("face", "portrait", "close", "intimate", "pivot", "turn", "gaze", "eye contact")),
    ("anticipation edge pocket", ("anticipation", "build", "rise", "suspense", "edge")),
    ("peak-threshold pocket", ("performance", "dominant", "climax", "peak", "front", "center")),
    ("retreat release pocket", ("retreat", "withdraw", "cooldown", "recover", "exhale", "release", "backstep")),
    ("lingering observation pocket", ("afterglow", "linger", "observ", "settle", "ending", "outro", "resolve", "calm")),
)

_CAMERA_TECH_LEAK_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:dolly|gimbal|crane|rack focus|whip pan|orbit shot|steadicam|handheld rig)\b", re.IGNORECASE),
    re.compile(r"\b(?:lens|focal length|iso|shutter|aperture)\b", re.IGNORECASE),
    re.compile(r"\bcamera movement\b.{0,30}\b(?:fast|aggressive|jerky|rig|operator)\b", re.IGNORECASE),
)

_DETECH_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bstable framing\b", re.IGNORECASE), "she remains steadily held in view"),
    (re.compile(r"\bslight push[\-\s]?in\b", re.IGNORECASE), "the sense of nearness gently increases"),
    (re.compile(r"\bcamera pushes? in\b", re.IGNORECASE), "attention moves closer to her expression"),
    (re.compile(r"\bpush[\-\s]?in\b", re.IGNORECASE), "the view eases closer"),
    (re.compile(r"\bcamera pulls? back\b", re.IGNORECASE), "the space around her opens slightly"),
    (re.compile(r"\bpull[\-\s]?back\b", re.IGNORECASE), "the view opens slightly around her"),
    (re.compile(r"\blateral tracking\b", re.IGNORECASE), "her movement is followed smoothly across the space"),
    (re.compile(r"\btracking shot\b", re.IGNORECASE), "her movement is followed with smooth continuity"),
    (re.compile(r"\bcamera moves?\b", re.IGNORECASE), "the view shifts with her"),
    (re.compile(r"\bcamera movement\b", re.IGNORECASE), "the perspective shifts gently with the moment"),
    (re.compile(r"\bframing reset\b", re.IGNORECASE), "the view settles into a refreshed composition"),
    (re.compile(r"\bclose[\-\s]?up\b", re.IGNORECASE), "a close view"),
    (re.compile(r"\bmedium shot\b", re.IGNORECASE), "a more open view of her upper body"),
    (re.compile(r"\bwide shot\b", re.IGNORECASE), "a wider view of her within the surrounding space"),
    (re.compile(r"\bdolly\b", re.IGNORECASE), "smooth forward or backward perspective shift"),
    (re.compile(r"\bzoom\b", re.IGNORECASE), "a gradual change in perceived nearness"),
)


def _suggest_local_zone_hint(
    *,
    scene_row: dict[str, Any],
    narrative_row: dict[str, Any],
    idx: int,
    total: int,
) -> tuple[str, str]:
    explicit_hint = str(scene_row.get("location_zone") or scene_row.get("zone_hint") or scene_row.get("location_hint") or "").strip()
    if explicit_hint:
        return explicit_hint[:120], "explicit"

    evidence = " ".join(
        [
            str(scene_row.get("scene_goal") or ""),
            str(scene_row.get("narrative_function") or scene_row.get("scene_function") or ""),
            str(scene_row.get("emotional_intent") or ""),
            str(scene_row.get("framing") or ""),
            str(scene_row.get("layout") or ""),
            str(_safe_dict(scene_row.get("composition")).get("framing") or ""),
            str(_safe_dict(scene_row.get("composition")).get("layout") or ""),
            str(_safe_dict(scene_row.get("visual_motion")).get("camera_intent") or ""),
            str(_safe_dict(scene_row.get("visual_motion")).get("energy_alignment") or ""),
            str(narrative_row.get("beat_purpose") or ""),
            str(narrative_row.get("emotional_key") or ""),
        ]
    ).lower()
    for zone_hint, keys in _LOCAL_ZONE_HINT_PATTERNS:
        if any(key in evidence for key in keys):
            return zone_hint, "derived"

    safe_total = max(int(total or 1), 1)
    step = max(min(idx - 1, safe_total - 1), 0)
    fallback_idx = min(step, len(_LOCAL_ZONE_FALLBACK_SEQUENCE) - 1)
    return _LOCAL_ZONE_FALLBACK_SEQUENCE[fallback_idx], "sequence"


def _legacy_bridge_requested(package: dict[str, Any]) -> bool:
    input_pkg = _safe_dict(package.get("input"))
    feature_flags = _safe_dict(input_pkg.get("feature_flags"))
    diagnostics_flags = _safe_dict(package.get("diagnostics"))
    truthy_keys = (
        "use_legacy_scene_prompts",
        "require_legacy_scene_prompts",
        "legacy_scene_prompts_required",
        "scene_prompts_legacy_bridge_required",
    )
    nodes = (package, input_pkg, feature_flags, diagnostics_flags)
    for node in nodes:
        for key in truthy_keys:
            if bool(node.get(key)):
                return True
    return False


def _scene_plan_storyboard(scene_plan: dict[str, Any]) -> list[dict[str, Any]]:
    storyboard = _safe_list(scene_plan.get("storyboard"))
    if storyboard:
        return [_safe_dict(row) for row in storyboard]
    return [_safe_dict(row) for row in _safe_list(scene_plan.get("scenes"))]


def _index_by_segment(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        segment_id = str(row.get("segment_id") or row.get("scene_id") or "").strip()
        if segment_id:
            out[segment_id] = row
    return out


def _build_prompt_rows(package: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scene_plan = _safe_dict(package.get("scene_plan"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))

    storyboard = _scene_plan_storyboard(scene_plan)
    audio_by_segment = _index_by_segment([_safe_dict(row) for row in _safe_list(audio_map.get("segments"))])
    narrative_by_segment = _index_by_segment([_safe_dict(row) for row in _safe_list(story_core.get("narrative_segments"))])
    role_casting_by_segment = _index_by_segment([_safe_dict(row) for row in _safe_list(role_plan.get("scene_casting"))])

    rows: list[dict[str, Any]] = []
    for idx, scene_row in enumerate(storyboard, start=1):
        segment_id = str(scene_row.get("segment_id") or scene_row.get("scene_id") or f"seg_{idx}").strip()
        route = str(scene_row.get("route") or "i2v").strip().lower()
        role_row = _safe_dict(role_casting_by_segment.get(segment_id))
        audio_row = _safe_dict(audio_by_segment.get(segment_id))
        narrative_row = _safe_dict(narrative_by_segment.get(segment_id))
        composition = _safe_dict(scene_row.get("composition"))
        visual_motion = _safe_dict(scene_row.get("visual_motion"))
        local_zone_hint, local_zone_hint_source = _suggest_local_zone_hint(
            scene_row=scene_row,
            narrative_row=narrative_row,
            idx=idx,
            total=len(storyboard),
        )

        rows.append(
            {
                "segment_id": segment_id,
                "route": route,
                "scene_goal": str(scene_row.get("scene_goal") or "").strip(),
                "narrative_function": str(scene_row.get("narrative_function") or scene_row.get("scene_function") or "").strip(),
                "subject_motion": str(visual_motion.get("subject_motion") or scene_row.get("subject_motion") or scene_row.get("motion_intent") or "").strip(),
                "camera_intent": str(visual_motion.get("camera_intent") or scene_row.get("camera_intent") or "").strip(),
                "pacing": str(visual_motion.get("pacing") or scene_row.get("pacing") or "").strip(),
                "energy_alignment": str(visual_motion.get("energy_alignment") or scene_row.get("energy_alignment") or "").strip(),
                "framing": str(composition.get("framing") or scene_row.get("framing") or "").strip(),
                "subject_priority": str(composition.get("subject_priority") or scene_row.get("subject_priority") or "").strip(),
                "layout": str(composition.get("layout") or scene_row.get("layout") or "").strip(),
                "depth_strategy": str(composition.get("depth_strategy") or scene_row.get("depth_strategy") or "").strip(),
                "audio_visual_sync": str(scene_row.get("audio_visual_sync") or "").strip(),
                "local_zone_hint": local_zone_hint,
                "local_zone_hint_source": local_zone_hint_source,
                "transcript_slice": str(audio_row.get("transcript_slice") or "").strip(),
                "intensity": str(audio_row.get("intensity") or "").strip(),
                "rhythmic_anchor": str(audio_row.get("rhythmic_anchor") or "").strip(),
                "primary_role": str(role_row.get("primary_role") or "").strip(),
                "visual_focus_role": str(scene_row.get("visual_focus_role") or "").strip(),
                "secondary_roles": [str(v).strip() for v in _safe_list(role_row.get("secondary_roles")) if str(v).strip()],
                "presence_mode": str(role_row.get("presence_mode") or "").strip(),
                "presence_weight": str(role_row.get("presence_weight") or "").strip(),
                "speaker_role": str(scene_row.get("speaker_role") or "").strip(),
                "spoken_line": str(scene_row.get("spoken_line") or "").strip(),
                "lip_sync_allowed": bool(scene_row.get("lip_sync_allowed")),
                "lip_sync_priority": str(scene_row.get("lip_sync_priority") or "").strip(),
                "mouth_visible_required": bool(scene_row.get("mouth_visible_required")),
                "listener_reaction_allowed": bool(scene_row.get("listener_reaction_allowed")),
                "reaction_role": str(scene_row.get("reaction_role") or "").strip(),
                "speaker_confidence": _coerce_speaker_confidence(scene_row.get("speaker_confidence")),
                "emotional_key": str(narrative_row.get("emotional_key") or scene_row.get("emotional_intent") or "").strip(),
                "beat_purpose": str(narrative_row.get("beat_purpose") or scene_row.get("scene_goal") or "").strip(),
            }
        )

    return rows, {
        "story_core": story_core,
        "scene_plan": scene_plan,
        "uses_segment_id_canonical": bool(_safe_list(scene_plan.get("storyboard"))),
    }


def _de_technicalize_text(text: str) -> tuple[str, bool]:
    clean = " ".join(str(text or "").strip().split())
    if not clean:
        return "", False
    changed = False
    out = clean
    for pattern, replacement in _DETECH_REPLACEMENTS:
        updated = pattern.sub(replacement, out)
        if updated != out:
            changed = True
            out = updated
    for pattern, replacement in (
        (re.compile(r"\bA an\b"), "An"),
        (re.compile(r"\bA a\b"), "A"),
    ):
        updated = pattern.sub(replacement, out)
        if updated != out:
            changed = True
            out = updated
    for pattern, replacement in (
        (re.compile(r"\b(an?\s+)?intimate near view\b", re.IGNORECASE), "a close view"),
        (re.compile(r"\ba\s+medium\s+a\s+close view\b", re.IGNORECASE), "a medium close view"),
        (re.compile(r"\bclose view\s+and\s+waist-up\b", re.IGNORECASE), "waist-up view"),
        (re.compile(r"\bwaist-up\s+and\s+close view\b", re.IGNORECASE), "waist-up view"),
        (re.compile(r"\b(close view|medium close view|waist-up view)\s+\1\b", re.IGNORECASE), r"\1"),
    ):
        updated = pattern.sub(replacement, out)
        if updated != out:
            changed = True
            out = updated
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;")
    return out, changed


def _sanitize_prompts_v11_wording(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = dict(payload)
    segments_out: list[dict[str, Any]] = []
    changed_segment_ids: list[str] = []
    field_mutation_counts: dict[str, int] = {
        "photo_prompt": 0,
        "video_prompt": 0,
        "negative_prompt": 0,
        "prompt_notes.notes[]": 0,
        "prompt_notes.transition.*": 0,
        "first_frame_prompt": 0,
        "last_frame_prompt": 0,
    }
    for raw_segment in _safe_list(payload.get("segments")):
        segment = dict(_safe_dict(raw_segment))
        segment_changed = False

        for field in ("photo_prompt", "video_prompt", "negative_prompt", "first_frame_prompt", "last_frame_prompt"):
            if field not in segment:
                continue
            rewritten, changed = _de_technicalize_text(str(segment.get(field) or ""))
            if changed:
                segment[field] = rewritten
                segment_changed = True
                field_mutation_counts[field] = field_mutation_counts.get(field, 0) + 1

        for field in ("negative_prompt", "negative_video_prompt"):
            if field not in segment:
                continue
            cleaned_negative = clean_negative_prompt_artifacts(str(segment.get(field) or ""))
            if cleaned_negative != str(segment.get(field) or ""):
                segment[field] = cleaned_negative
                segment_changed = True
                field_mutation_counts["negative_prompt"] = field_mutation_counts.get("negative_prompt", 0) + 1

        prompt_notes = dict(_safe_dict(segment.get("prompt_notes")))
        if "notes" in prompt_notes:
            notes_out: list[str] = []
            notes_changed = False
            for note in _safe_list(prompt_notes.get("notes")):
                rewritten_note, note_changed = _de_technicalize_text(str(note or ""))
                notes_out.append(rewritten_note)
                if note_changed:
                    notes_changed = True
                    field_mutation_counts["prompt_notes.notes[]"] += 1
            if notes_changed:
                prompt_notes["notes"] = notes_out
                segment_changed = True

        transition = dict(_safe_dict(prompt_notes.get("transition")))
        transition_changed = False
        for transition_key in ("start_state", "end_state", "state_delta"):
            if transition_key not in transition:
                continue
            rewritten_transition, changed = _de_technicalize_text(str(transition.get(transition_key) or ""))
            if changed:
                transition[transition_key] = rewritten_transition
                transition_changed = True
                field_mutation_counts["prompt_notes.transition.*"] += 1
        if transition_changed:
            prompt_notes["transition"] = transition
            segment_changed = True

        if segment_changed:
            segment["prompt_notes"] = prompt_notes
            changed_segment_ids.append(str(segment.get("segment_id") or "").strip())
        segments_out.append(segment)

    normalized["segments"] = segments_out
    non_zero_counts = {k: v for k, v in field_mutation_counts.items() if v > 0}
    return normalized, {
        "scene_prompts_de_technicalization_applied": bool(changed_segment_ids),
        "scene_prompts_de_technicalized_segment_ids": [seg for seg in changed_segment_ids if seg],
        "scene_prompts_de_technicalized_field_counts": non_zero_counts,
    }


def _build_global_style_anchor(story_core: dict[str, Any]) -> str:
    world = _build_world_lock_summary(story_core)
    style = _build_style_lock_summary(story_core)
    parts = [
        "Same world family across all segments with location-zone variation only, no random location drift",
        "Grounded realism contract with one coherent lighting family and stable mood progression",
    ]
    if world:
        parts.append(f"World lock: {world}")
    if style:
        parts.append(f"Style lock: {style}")
    return "; ".join(parts)[:1200]


def _sanitize_global_style_anchor(anchor: str, story_core: dict[str, Any]) -> tuple[str, bool]:
    text = str(anchor or "").strip()
    if not text:
        return _build_global_style_anchor(story_core), True
    sentences = [s.strip() for s in re.split(r"[.;]\s*", text) if s.strip()]
    filtered = [
        s
        for s in sentences
        if not any(token in s.lower() for token in _CHARACTER_STYLE_LEAK_TOKENS)
    ]
    if filtered:
        return "; ".join(filtered)[:1200], len(filtered) != len(sentences)
    return _build_global_style_anchor(story_core), True


def _rebuild_global_style_anchor_from_story_core(story_core: dict[str, Any]) -> str:
    identity_doctrine = _safe_dict(story_core.get("identity_doctrine"))
    world_lock = _safe_dict(story_core.get("world_lock"))
    style_lock = _safe_dict(story_core.get("style_lock"))

    candidates = [
        str(identity_doctrine.get("world_doctrine") or "").strip(),
        str(identity_doctrine.get("style_doctrine") or "").strip(),
        str(world_lock.get("rule") or world_lock.get("rules") or "").strip(),
        str(style_lock.get("rule") or style_lock.get("rules") or "").strip(),
        _build_world_lock_summary(story_core),
        _build_style_lock_summary(story_core),
    ]
    leak_tokens = set(_CHARACTER_STYLE_LEAK_TOKENS) | {"woman's", "girl's", "lady's", "her", "she", "women", "feminine"}
    parts: list[str] = []
    for chunk in candidates:
        if not chunk:
            continue
        for sentence in [s.strip() for s in re.split(r"[.;]\s*", chunk) if s.strip()]:
            lower_sentence = sentence.lower()
            if any(token in lower_sentence for token in leak_tokens):
                continue
            parts.append(sentence)
    deduped = list(dict.fromkeys(parts))
    if deduped:
        return "; ".join(deduped)[:1200]
    return "Grounded realistic urban world, coherent documentary realism, stable lighting, consistent atmosphere."


def _is_lip_sync_only_character_1(package: dict[str, Any]) -> bool:
    input_pkg = _safe_dict(package.get("input"))
    summary = _safe_dict(input_pkg.get("connected_context_summary"))
    role_map = _safe_dict(summary.get("role_identity_mapping"))
    char = _safe_dict(role_map.get("character_1"))
    appearance = str(char.get("appearanceMode") or char.get("appearance_mode") or "").strip().lower()
    presence = str(char.get("screenPresenceMode") or char.get("screen_presence_mode") or "").strip().lower()
    return appearance == "lip_sync_only" or presence == "lip_sync_only"


def _character_1_identity_diag(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    connected = _safe_dict(input_pkg.get("connected_context_summary")) or _safe_dict(package.get("connected_context_summary"))
    role_map = _safe_dict(connected.get("role_identity_mapping"))
    char = _safe_dict(role_map.get("character_1"))
    refs = _safe_list(_safe_dict(connected.get("refsPresentByRole")).get("character_1"))
    ref_tokens = [str(v).strip() for v in refs if str(v).strip()]
    signature = hashlib.sha256("|".join(sorted(ref_tokens)).encode("utf-8")).hexdigest() if ref_tokens else ""
    appearance = str(char.get("appearanceMode") or char.get("appearance_mode") or "").strip().lower()
    presence = str(char.get("screenPresenceMode") or char.get("screen_presence_mode") or "").strip().lower()
    return {
        "current_character_1_gender_hint": str(char.get("gender_hint") or "").strip().lower(),
        "current_character_1_identity_label": str(char.get("identity_label") or "").strip(),
        "current_character_1_appearance_mode": appearance,
        "current_character_1_screen_presence_mode": presence,
        "current_character_1_ref_count": len(ref_tokens),
        "current_character_1_ref_present": bool(ref_tokens),
        "current_character_1_ref_signature": signature,
        "current_identity_source": "current_connected_ref",
    }


def _get_character_1_gender_hint(package: dict[str, Any]) -> str:
    input_pkg = _safe_dict(package.get("input"))
    connected = _safe_dict(input_pkg.get("connected_context_summary")) or _safe_dict(package.get("connected_context_summary"))
    role_map = _safe_dict(connected.get("role_identity_mapping"))
    by_role = _safe_dict(connected.get("character_identity_by_role"))
    role_char = _safe_dict(role_map.get("character_1"))
    by_role_char = _safe_dict(by_role.get("character_1"))
    return str(role_char.get("gender_hint") or by_role_char.get("gender_hint") or "").strip().lower()


def _compile_stale_term_pattern(term: str) -> re.Pattern[str]:
    token = str(term or "").strip().lower()
    if not token:
        return re.compile(r"$^")
    possessive_match = re.fullmatch(r"([a-z]+)['’]s", token)
    if possessive_match:
        root = re.escape(possessive_match.group(1))
        return re.compile(rf"(?i)(?<![A-Za-z]){root}['’]s(?![A-Za-z])")
    escaped_parts = [re.escape(part) for part in token.split() if part]
    phrase = r"\s+".join(escaped_parts)
    return re.compile(rf"(?i)(?<![A-Za-z]){phrase}(?![A-Za-z])")


def _find_stale_terms_token_safe(text: str, terms: Iterable[str]) -> list[str]:
    body = str(text or "")
    if not body:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for term in terms:
        token = str(term or "").strip().lower()
        if not token or token in seen:
            continue
        pattern = _compile_stale_term_pattern(token)
        if pattern.search(body):
            seen.add(token)
            hits.append(token)
    return hits


def _remove_stale_terms_token_safe(text: str, terms: Iterable[str]) -> tuple[str, list[str]]:
    original = str(text or "")
    if not original:
        return "", []
    out = original
    removed: list[str] = []
    seen: set[str] = set()
    for term in terms:
        token = str(term or "").strip().lower()
        if not token or token in seen:
            continue
        pattern = _compile_stale_term_pattern(token)
        if pattern.search(out):
            out = pattern.sub(" ", out)
            removed.append(token)
            seen.add(token)
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"([,.;:])\1+", r"\1", out)
    out = re.sub(r"(?:,\s*){2,}", ", ", out)
    out = re.sub(r"(?:\.\s*){2,}", ". ", out)
    out = out.strip(" ,;:.")
    return out, removed


def _iter_stale_term_matches_with_excerpt(text: str, terms: Iterable[str]) -> list[dict[str, str]]:
    body = str(text or "")
    if not body:
        return []
    entries: list[dict[str, str]] = []
    for token in _find_stale_terms_token_safe(body, terms):
        pattern = _compile_stale_term_pattern(token)
        for match in pattern.finditer(body):
            start = max(0, match.start() - 40)
            end = min(len(body), match.end() + 40)
            excerpt = body[start:end].strip()
            entries.append({"term": token, "excerpt": excerpt})
    return entries


def _is_bad_prompt_cleanup_scene_prompts(text: str) -> bool:
    normalized = str(text or "").strip()
    lower = normalized.lower()
    if len(normalized) < 32:
        return True
    bad_fragments = (
        "show the as",
        "show as the",
        "the 's",
        "the looking",
        "the catching",
        "the singing",
        "of the turning",
        "upper body of the turning",
        "duplicate ,",
    )
    if any(fragment in lower for fragment in bad_fragments):
        return True
    if re.search(r"\ba\s+in\s+her\b", lower):
        return True
    if re.search(r"\bwearing\s+(a|the)?\s*$", lower):
        return True
    if re.search(r"\bwith\s*$", lower):
        return True
    return False


def _build_prompts_v11_prompt(
    *,
    prompt_rows: list[dict[str, Any]],
    global_style_anchor: str,
    package: dict[str, Any],
    validation_feedback: str = "",
) -> str:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    role_plan = _safe_dict(package.get("role_plan"))
    audio_map = _safe_dict(package.get("audio_map"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    prompt_interface_contract = _resolve_prompt_interface_contract(story_core)
    connected_context_summary = _safe_dict(input_pkg.get("connected_context_summary")) or _safe_dict(
        package.get("connected_context_summary")
    )
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    active_model_id = _resolve_active_video_model_id(package)
    capability_summary = build_capability_diagnostics_summary(
        model_id=active_model_id,
        route_type="scene_prompts_engine_agnostic_guard",
        story_core_guard_applied=False,
        scene_plan_guard_applied=False,
        prompt_guard_applied=True,
    )
    context = {
        "story_core": {
            "story_summary": str(story_core.get("story_summary") or ""),
            "opening_anchor": str(story_core.get("opening_anchor") or ""),
            "ending_callback_rule": str(story_core.get("ending_callback_rule") or ""),
            "global_arc": str(story_core.get("global_arc") or ""),
            "identity_lock": _safe_dict(story_core.get("identity_lock")),
            "world_lock": _safe_dict(story_core.get("world_lock")),
            "style_lock": _safe_dict(story_core.get("style_lock")),
            "story_guidance": story_guidance_to_notes_list(story_core.get("story_guidance"), max_items=16),
        },
        "roles": {
            "roster": _safe_list(role_plan.get("roster")),
            "scene_casting": _safe_list(role_plan.get("scene_casting")),
        },
        "audio_map": {
            "segments": _safe_list(audio_map.get("segments")),
        },
        "scene_plan": {
            "scenes_version": str(scene_plan.get("scenes_version") or ""),
            "storyboard": _scene_plan_storyboard(scene_plan),
        },
        "prompt_interface_contract": {
            "visibility_mode": str(prompt_interface_contract.get("visibility_mode") or ""),
            "subject_presence_requirement": str(prompt_interface_contract.get("subject_presence_requirement") or ""),
            "must_be_visible": [
                str(v).strip()
                for v in _safe_list(prompt_interface_contract.get("must_be_visible"))
                if str(v).strip()
            ],
            "may_be_offscreen": [
                str(v).strip()
                for v in _safe_list(prompt_interface_contract.get("may_be_offscreen"))
                if str(v).strip()
            ],
        },
        "connected_context_summary": connected_context_summary,
        "refs_inventory_keys": list(refs_inventory.keys())[:40],
        "capability_context": {
            "active_video_model_capability_profile": active_model_id,
            "active_route_capability_mode": "scene_prompts_engine_agnostic_guard",
            "prompt_capability_guard_applied": True,
            "capability_rules_source_version": get_capability_rules_source_version(),
            "summary": capability_summary,
        },
        "global_style_anchor": global_style_anchor,
        "prompt_rows": prompt_rows,
    }
    feedback_block = f"\nVALIDATION_FEEDBACK:\n{validation_feedback}\n" if str(validation_feedback or "").strip() else ""
    return (
        "You are PROMPTS stage only for GEMINI-FIRST pipeline.\n"
        "Return STRICT JSON only. No markdown, no prose outside JSON.\n"
        "Output schema:\n"
        "{\"prompts_version\":\"1.1\",\"global_style_anchor\":\"\",\"segments\":[{\"segment_id\":\"\",\"scene_id\":\"\",\"route\":\"i2v|ia2v|first_last\",\"photo_prompt\":\"\",\"video_prompt\":\"\",\"negative_prompt\":\"\",\"prompt_notes\":{\"emotion\":\"\",\"world_anchor\":\"\",\"notes\":[\"\"],\"transition\":{\"start_state\":\"\",\"end_state\":\"\",\"state_delta\":\"\"}},\"first_frame_prompt\":\"\",\"last_frame_prompt\":\"\"}]}\n"
        "Rules:\n"
        "- One segment row per segment_id from prompt_rows.\n"
        "- prompts_version must be exactly \"1.1\".\n"
        "- Use SCENES as directing source, CORE as doctrine/meaning, ROLES as cast presence, AUDIO only as timing/emotional evidence.\n"
        "- Do not mutate route, cast, timing, doctrine, segment ids, or scene ids.\n"
        "- segment_id and scene_id must be present for every segment.\n"
        "- route must be copied exactly from upstream prompt_rows row for that segment.\n"
        "- Preserve same identity and same world family unless explicitly changed upstream.\n"
        "- Keep all segments inside one coherent world family; segment variation must come from local pocket/zone, emotional beat, and framing emphasis, never from random new geography.\n"
        "- PROMPTS must stay engine-agnostic and useful for PHOTO and VIDEO preparation.\n"
        "- photo_prompt = engine-agnostic still-image prompt for scene image generation.\n"
        "- video_prompt = engine-agnostic motion-oriented scene description, still NOT final engine delivery contract.\n"
        "- negative_prompt = descriptive continuity/drift guardrails only.\n"
        "- prompt_notes must be an OBJECT (not list): emotion, world_anchor, notes[], and optional transition object.\n"
        "- Do not output engine params, renderer-specific phrasing, quality buzzwords, workflow tags, model tags, camera/fps/lens/seed specs.\n"
        "- Do not reconstruct final video prompt and do not output route-delivery payload.\n"
        "- Translate SCENES signal (scene_goal, narrative_function, subject_motion, camera_intent, pacing, energy_alignment, framing, subject_priority, layout, depth_strategy, audio_visual_sync) into natural descriptive writing rather than technical labels.\n"
        "- Each segment must consume prompt_rows speaker/lip-sync controls: speaker_role, spoken_line, lip_sync_allowed, mouth_visible_required, listener_reaction_allowed, reaction_role.\n"
        "- If route == ia2v and lip_sync_allowed is true: mouth-sync only for speaker_role, listener/reaction_role remains silent/background, and never dual-speaker simultaneous lip movement.\n"
        "- If route == ia2v and lip_sync_allowed is false: keep performance or audio-reactive cues but avoid mouth-sync language.\n"
        "- If speaker_role is unknown, do not generate lip-sync phrasing.\n"
        "- Never copy technical scene labels verbatim into final prompts.\n"
        "- camera_intent must be translated into natural descriptive prose.\n"
        "- framing/motion hints must be expressed without camera jargon.\n"
        "- Forbidden literal leakage examples in final prompt text: stable framing, push-in, pull-back, lateral tracking, dolly, zoom, camera move, tracking shot, medium shot, close-up, wide shot.\n"
        "- Make photo_prompt and video_prompt stable/specific across segments (same current-role identity continuity), while changing micro-performance and local action intent by segment.\n"
        "- Prefer explicit role-grounded wording over generic labels: e.g. \"character_1 from current reference\" and \"character_2 from current reference\" when those cast references exist.\n"
        "- Keep prompts within the same world family defined by story_core/world_lock and user input. Use local_zone_hint only as deterministic local pocket guidance. Do not invent a new venue or cast.\n"
        "- Read prompt_interface_contract as source-of-truth for visibility constraints: visibility_mode, must_be_visible, may_be_offscreen, subject_presence_requirement.\n"
        "- If prompt_interface_contract.must_be_visible contains multiple cast roles, every photo_prompt must include all required visible roles in the same shared physical space defined by world_lock/user input.\n"
        "- visual_focus_role may dominate the frame, but every must_be_visible role remains visibly present unless listed in may_be_offscreen.\n"
        "- For route == first_last, first_frame_prompt and last_frame_prompt must satisfy the same shared-space visibility rule.\n"
        "- Never explicitly describe a must_be_visible role as offscreen/not visible/outside frame unless that role is listed in may_be_offscreen.\n"
        "- Lighting/atmosphere should evolve per beat within one light family with pocket-level differences in density and contrast; avoid flat repeated prose.\n"
        "- Express segment-to-segment emotional curve with compact beat language (anticipation -> peak -> release -> lingering afterglow) while preserving continuity.\n"
        "- Use descriptive framing language (intimate/nearer/opener/layered/deeper) allowed; avoid camera-tech jargon.\n"
        "- For route == first_last: prompt_notes.transition.start_state and prompt_notes.transition.end_state are required; you may also duplicate them into first_frame_prompt and last_frame_prompt.\n"
        "- For routes i2v/ia2v: prompt_notes.transition may be omitted or empty; first_frame_prompt and last_frame_prompt should be empty strings.\n\n"
        f"{feedback_block}"
        f"CONTEXT:\n{json.dumps(context, ensure_ascii=False)}"
    )


def _build_prompts_v11_compact_retry_prompt(
    *,
    prompt_rows: list[dict[str, Any]],
    global_style_anchor: str,
    package: dict[str, Any],
    validation_feedback: str = "",
) -> str:
    identity_diag = _character_1_identity_diag(package)
    gender_hint = str(identity_diag.get("current_character_1_gender_hint") or "").strip().lower()
    appearance_mode = str(identity_diag.get("current_character_1_appearance_mode") or "").strip().lower()
    presence_mode = str(identity_diag.get("current_character_1_screen_presence_mode") or "").strip().lower()
    ref_present = bool(identity_diag.get("current_character_1_ref_present"))
    lip_sync_only = appearance_mode == "lip_sync_only" or presence_mode == "lip_sync_only"
    global_audio_owner = str(_safe_dict(package.get("audio_map")).get("vocal_owner_role") or "").strip() or "character_1"
    male_lipsync_contract_block = ""
    if gender_hint == "male" and lip_sync_only:
        male_lipsync_contract_block = (
            "- current character_1 is a current male performer from connected reference.\n"
            "- character_1 appears physically only in ia2v/lip-sync scenes.\n"
            "- i2v scenes must be environment/story cutaways, no main performer visible.\n"
        )
    compact_rows: list[dict[str, Any]] = []
    for row_raw in prompt_rows:
        row = _safe_dict(row_raw)
        row_route = str(row.get("route") or "i2v").strip().lower() or "i2v"
        row_vocal_owner = str(row.get("vocal_owner_role") or global_audio_owner).strip()
        compact_rows.append(
            {
                "segment_id": str(row.get("segment_id") or "").strip(),
                "scene_id": str(row.get("scene_id") or "").strip(),
                "route": row_route,
                "primary_role": str(row.get("primary_role") or "").strip(),
                "speaker_role": str(row.get("speaker_role") or "").strip(),
                "vocal_owner_role": row_vocal_owner,
                "spoken_line": _trim_sentence(str(row.get("spoken_line") or row.get("transcript_slice") or "").strip(), max_len=120),
            }
        )
    feedback_block = f"\nVALIDATION_FEEDBACK:\n{validation_feedback}\n" if str(validation_feedback or "").strip() else ""
    return (
        "Return STRICT JSON only.\n"
        "Schema: {\"prompts_version\":\"1.1\",\"global_style_anchor\":\"\",\"segments\":[...]}\n"
        "Rules (compact retry):\n"
        "- Generate rows ONLY for listed segment_id values.\n"
        "- Keep route exactly as provided.\n"
        "- Keep prompts concise, continuity-safe, and route-aware.\n"
        "- IDENTITY CONTRACT is mandatory and comes from CURRENT package only.\n"
        "- Do not invent visual appearance details for character_1.\n"
        "- If current character_1 ref exists, describe character_1 only as: current character_1 from connected reference.\n"
        "- ia2v: audio-driven performance, readable mouth only for speaker_role.\n"
        "- If character_1 appearanceMode/screenPresenceMode is lip_sync_only: character_1 appears physically only in ia2v/lip-sync scenes.\n"
        "- For i2v under lip_sync_only: environment/story cutaway only; no main performer visible; singer remains voiceover only.\n"
        f"{male_lipsync_contract_block}"
        "- first_last: include first_frame_prompt and last_frame_prompt.\n"
        "- Do not include technical tags or renderer params.\n"
        f"{feedback_block}"
        "IDENTITY_CONTRACT_CURRENT:\n"
        f"{json.dumps({'current_character_1_gender_hint': gender_hint, 'current_character_1_identity_label': identity_diag.get('current_character_1_identity_label'), 'current_character_1_appearanceMode': appearance_mode, 'current_character_1_screenPresenceMode': presence_mode, 'current_character_1_ref_count': int(identity_diag.get('current_character_1_ref_count') or 0), 'current_character_1_ref_present': ref_present, 'vocal_owner_role': global_audio_owner, 'lip_sync_only_policy': lip_sync_only}, ensure_ascii=False)}\n"
        f"GLOBAL_STYLE_ANCHOR:\n{global_style_anchor}\n"
        f"SCENE_ROWS_COMPACT:\n{json.dumps(compact_rows, ensure_ascii=False)}"
    )


def _build_prompts_v11_fallback_payload(
    *,
    prompt_rows: list[dict[str, Any]],
    story_core: dict[str, Any],
    global_style_anchor: str,
) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    for row_raw in prompt_rows:
        row = _safe_dict(row_raw)
        route = str(row.get("route") or "i2v").strip().lower() or "i2v"
        fallback = _build_prompts_v11_segment_fallback({**row, "route": route}, story_core)
        segment = {
            "segment_id": str(row.get("segment_id") or "").strip(),
            "scene_id": str(row.get("scene_id") or "").strip(),
            "route": route,
            "photo_prompt": str(fallback.get("photo_prompt") or ""),
            "video_prompt": str(fallback.get("video_prompt") or ""),
            "negative_prompt": str(fallback.get("negative_prompt") or _GLOBAL_NEGATIVE_PROMPT),
            "first_frame_prompt": str(fallback.get("first_frame_prompt") or ""),
            "last_frame_prompt": str(fallback.get("last_frame_prompt") or ""),
            "prompt_notes": _prompt_notes_template(route),
        }
        apply_ia2v_lipsync_canon_to_prompt_row(segment, source_scene=row)
        segments.append(segment)
    return {
        "prompts_version": "1.1",
        "global_style_anchor": str(global_style_anchor or "").strip(),
        "segments": segments,
    }


def _coerce_prompts_v11_payload(raw: Any, prompt_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    prompt_route_by_segment = {
        str(_safe_dict(row).get("segment_id") or "").strip(): str(_safe_dict(row).get("route") or "i2v").strip() or "i2v"
        for row in prompt_rows
    }
    dropped_fields: set[str] = set()
    list_diag = {
        "scene_prompts_top_level_list_unwrapped": False,
        "scene_prompts_top_level_list_kind": "",
        "scene_prompts_top_level_list_length": 0,
    }
    source = raw
    if isinstance(raw, list):
        list_diag["scene_prompts_top_level_list_length"] = len(raw)
        if len(raw) == 1:
            first = _safe_dict(raw[0])
            first_keys = set(first.keys())
            if {"prompts_version", "global_style_anchor", "segments"}.issubset(first_keys):
                source = first
                list_diag["scene_prompts_top_level_list_unwrapped"] = True
                list_diag["scene_prompts_top_level_list_kind"] = "single_payload_object"
        if source is raw:
            segment_like = [
                _safe_dict(item)
                for item in raw
                if isinstance(item, dict) and str(_safe_dict(item).get("segment_id") or _safe_dict(item).get("scene_id") or "").strip()
            ]
            if segment_like and len(segment_like) == len(raw):
                source = {"prompts_version": "1.1", "segments": raw}
                list_diag["scene_prompts_top_level_list_unwrapped"] = True
                list_diag["scene_prompts_top_level_list_kind"] = "segments_array"
    data = _safe_dict(source)
    if not data:
        return {}, [], list_diag

    candidates = [data]
    for key in ("result", "data", "output", "payload", "scene_prompts"):
        nested = _safe_dict(data.get(key))
        if nested:
            candidates.append(nested)
    chosen = next((item for item in candidates if _safe_list(item.get("segments")) or _safe_list(item.get("scenes"))), data)

    normalized: dict[str, Any] = {
        "prompts_version": str(chosen.get("prompts_version") or data.get("prompts_version") or "").strip(),
        "global_style_anchor": str(chosen.get("global_style_anchor") or data.get("global_style_anchor") or "").strip(),
        "segments": [],
    }
    for key in chosen.keys():
        if key not in {"prompts_version", "global_style_anchor", "segments", "scenes"}:
            dropped_fields.add(str(key))

    raw_segments = _safe_list(chosen.get("segments"))
    if not raw_segments:
        raw_segments = _safe_list(chosen.get("scenes"))

    for item in raw_segments:
        row = _safe_dict(item)
        segment_id = str(row.get("segment_id") or row.get("scene_id") or "").strip()
        if not segment_id:
            continue
        route = str(row.get("route") or prompt_route_by_segment.get(segment_id) or "i2v").strip().lower()
        if route not in ALLOWED_ROUTES:
            route = str(prompt_route_by_segment.get(segment_id) or "i2v")
        notes = _coerce_prompt_notes(row.get("prompt_notes"))
        transition = _safe_dict(row.get("transition_description"))
        start_state = str(
            transition.get("start_state_description")
            or notes.get("start_state")
            or row.get("first_frame_prompt")
            or row.get("start_image_prompt")
            or ""
        ).strip()
        end_state = str(
            transition.get("end_state_description")
            or notes.get("end_state")
            or row.get("last_frame_prompt")
            or row.get("end_image_prompt")
            or ""
        ).strip()
        if route == "first_last":
            transition_notes = _safe_dict(notes.get("transition"))
            transition_notes["start_state"] = start_state
            transition_notes["end_state"] = end_state
            if str(transition.get("state_delta") or "").strip():
                transition_notes["state_delta"] = str(transition.get("state_delta") or "").strip()
            notes["transition"] = transition_notes
            if start_state:
                notes.setdefault("start_state", start_state)
            if end_state:
                notes.setdefault("end_state", end_state)

        segment_row = {
            "segment_id": segment_id,
            "scene_id": str(row.get("scene_id") or segment_id).strip(),
            "route": route,
            "photo_prompt": str(row.get("photo_prompt") or "").strip(),
            "video_prompt": str(row.get("video_prompt") or row.get("positive_video_prompt") or "").strip(),
            "negative_prompt": str(row.get("negative_prompt") or row.get("negative_video_prompt") or "").strip(),
            "prompt_notes": notes,
        }
        if start_state:
            segment_row["first_frame_prompt"] = start_state
        if end_state:
            segment_row["last_frame_prompt"] = end_state
        for key in row.keys():
            if key not in {
                "segment_id",
                "scene_id",
                "route",
                "photo_prompt",
                "video_prompt",
                "negative_prompt",
                "prompt_notes",
                "positive_video_prompt",
                "negative_video_prompt",
                "first_frame_prompt",
                "last_frame_prompt",
                "start_image_prompt",
                "end_image_prompt",
                "transition_description",
            }:
                dropped_fields.add(str(key))
        normalized["segments"].append(segment_row)

    if not normalized["prompts_version"]:
        normalized["prompts_version"] = "1.1"
    return normalized, sorted(dropped_fields), list_diag


def _build_prompts_v11_segment_fallback(segment_row: dict[str, Any], story_core: dict[str, Any]) -> dict[str, str]:
    route = str(segment_row.get("route") or "i2v").strip().lower() or "i2v"
    world_anchor = _trim_sentence(_build_world_lock_summary(story_core) or "the same grounded world", max_len=180)
    role_label = _trim_sentence(str(segment_row.get("primary_role") or "the main subject"), max_len=80)
    scene_goal = _trim_sentence(str(segment_row.get("scene_goal") or segment_row.get("beat_purpose") or "story beat"), max_len=140)
    narrative_function = _trim_sentence(str(segment_row.get("narrative_function") or "narrative progression"), max_len=120)
    emotion = _trim_sentence(str(segment_row.get("emotional_key") or "controlled emotional expression"), max_len=120)
    framing = _trim_sentence(str(segment_row.get("framing") or "readable cinematic framing"), max_len=120)
    subject_motion = _trim_sentence(str(segment_row.get("subject_motion") or "single clear body motion"), max_len=140)
    camera_intent = _trim_sentence(str(segment_row.get("camera_intent") or "stable camera behavior"), max_len=140)
    transcript_slice = _trim_sentence(str(segment_row.get("transcript_slice") or ""), max_len=150)
    transcript_clause = f" Lyric/moment anchor: {transcript_slice}." if transcript_slice else ""
    photo_prompt = (
        f"Route-aware still frame of {role_label} in {world_anchor}, {scene_goal}, {framing}, "
        f"emotion={emotion}, keep identity/body/wardrobe/world continuity and avoid cast/world drift.{transcript_clause}"
    ).strip()[:900]
    video_prompt = (
        f"Route-aware motion/camera prompt for {route}: {role_label} performs {subject_motion} for {narrative_function}; "
        f"camera={camera_intent}; pacing follows scene intent while preserving the same world/identity/wardrobe continuity.{transcript_clause}"
    ).strip()[:900]
    negative_prompt = (
        "identity drift, face swap, body proportion drift, wardrobe changes, world-family drift, "
        "new cast invention, style jump, unreadable motion, camera-tech tags, prompt leakage"
    )

    fallback = {
        "photo_prompt": photo_prompt,
        "video_prompt": video_prompt,
        "negative_prompt": negative_prompt,
    }
    if route == "first_last":
        fallback["first_frame_prompt"] = (
            f"Start frame in {world_anchor}: {role_label} at the beginning of one controlled transition for {scene_goal}, "
            f"same identity/outfit/lighting/framing family."
        )[:900]
        fallback["last_frame_prompt"] = (
            f"End frame in the same {world_anchor}: {role_label} after the same single transition for {scene_goal}, "
            f"same identity/outfit/lighting/framing family, one clear state delta only."
        )[:900]
    return fallback


def _repair_prompts_v11_required_fields(
    prompts_v11: dict[str, Any],
    prompt_rows: list[dict[str, Any]],
    story_core: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    repaired = dict(prompts_v11)
    expected_by_segment = {
        str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
        for row in prompt_rows
        if str(_safe_dict(row).get("segment_id") or "").strip()
    }
    segments_out: list[dict[str, Any]] = []
    empty_scene_ids: list[str] = []
    rebuilt_scene_ids: list[str] = []
    valid_scene_ids: list[str] = []

    for raw_segment in _safe_list(prompts_v11.get("segments")):
        seg = dict(_safe_dict(raw_segment))
        segment_id = str(seg.get("segment_id") or "").strip()
        prompt_row = _safe_dict(expected_by_segment.get(segment_id))
        route = str(seg.get("route") or prompt_row.get("route") or "i2v").strip().lower() or "i2v"
        seg["route"] = route
        fallback = _build_prompts_v11_segment_fallback({**prompt_row, "route": route}, story_core)

        photo_prompt = str(seg.get("photo_prompt") or "").strip()
        video_prompt = str(seg.get("video_prompt") or "").strip()
        negative_prompt = str(seg.get("negative_prompt") or "").strip()
        first_frame_prompt = str(seg.get("first_frame_prompt") or "").strip()
        last_frame_prompt = str(seg.get("last_frame_prompt") or "").strip()

        had_empty_required = (not photo_prompt) or (not video_prompt) or (not negative_prompt)
        if route == "first_last":
            had_empty_required = had_empty_required or (not first_frame_prompt) or (not last_frame_prompt)

        if had_empty_required:
            if not photo_prompt:
                seg["photo_prompt"] = str(fallback.get("photo_prompt") or "")
            if not video_prompt:
                seg["video_prompt"] = str(fallback.get("video_prompt") or "")
            if not negative_prompt:
                seg["negative_prompt"] = str(fallback.get("negative_prompt") or "")
            if route == "first_last":
                if not first_frame_prompt:
                    seg["first_frame_prompt"] = str(fallback.get("first_frame_prompt") or "")
                if not last_frame_prompt:
                    seg["last_frame_prompt"] = str(fallback.get("last_frame_prompt") or "")
            rebuilt_scene_ids.append(segment_id)

        final_photo = str(seg.get("photo_prompt") or "").strip()
        final_video = str(seg.get("video_prompt") or "").strip()
        final_negative = str(seg.get("negative_prompt") or "").strip()
        final_first = str(seg.get("first_frame_prompt") or "").strip()
        final_last = str(seg.get("last_frame_prompt") or "").strip()
        is_valid = bool(final_photo and final_video and final_negative)
        if route == "first_last":
            is_valid = bool(is_valid and final_first and final_last)
        if is_valid:
            valid_scene_ids.append(segment_id)
        else:
            empty_scene_ids.append(segment_id)
        apply_ia2v_lipsync_canon_to_prompt_row(seg, source_scene=prompt_row)
        segments_out.append(seg)

    repaired["segments"] = segments_out
    return repaired, {
        "scene_prompts_empty_count": len(empty_scene_ids),
        "scene_prompts_empty_scene_ids": empty_scene_ids,
        "scene_prompts_rebuilt_count": len(rebuilt_scene_ids),
        "scene_prompts_rebuilt_scene_ids": rebuilt_scene_ids,
        "scene_prompts_valid_count": len(valid_scene_ids),
    }


def _apply_prompts_v11_shared_space_post_repair(
    prompts_v11: dict[str, Any],
    story_core: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    patched = dict(prompts_v11)
    prompt_interface_contract = _resolve_prompt_interface_contract(story_core)
    must_be_visible_roles = [
        str(role).strip()
        for role in _safe_list(prompt_interface_contract.get("must_be_visible"))
        if str(role).strip()
    ]
    may_be_offscreen_roles = {
        str(role).strip()
        for role in _safe_list(prompt_interface_contract.get("may_be_offscreen"))
        if str(role).strip()
    }
    enforce_shared_space_rule = len(must_be_visible_roles) >= 2
    shared_space_missing_segments: list[str] = []
    offscreen_violation_segments: list[str] = []
    validation_errors: list[str] = []
    segments_out: list[dict[str, Any]] = []
    enforcement_clause = _shared_space_enforcement_clause(must_be_visible_roles)
    offscreen_pattern = r"\b(offscreen|off-screen|not visible|outside frame)\b"

    for raw_segment in _safe_list(prompts_v11.get("segments")):
        segment = dict(_safe_dict(raw_segment))
        if not enforce_shared_space_rule:
            segments_out.append(segment)
            continue

        segment_id = str(segment.get("segment_id") or "").strip()
        route = str(segment.get("route") or "i2v").strip().lower() or "i2v"
        photo_prompt = str(segment.get("photo_prompt") or "").strip()
        first_frame_prompt = str(segment.get("first_frame_prompt") or "").strip()
        last_frame_prompt = str(segment.get("last_frame_prompt") or "").strip()

        missing_roles = [role for role in must_be_visible_roles if not _text_mentions_role(photo_prompt, role)]
        if missing_roles:
            shared_space_missing_segments.append(segment_id)
            segment["photo_prompt"] = _append_prompt_clause(photo_prompt, enforcement_clause)
            photo_prompt = str(segment.get("photo_prompt") or "").strip()

        offscreen_violations = [
            role
            for role in must_be_visible_roles
            if role not in may_be_offscreen_roles
            and bool(re.search(rf"\b{re.escape(role)}\b.{0,40}{offscreen_pattern}", photo_prompt.lower()))
        ]
        if offscreen_violations:
            offscreen_violation_segments.append(segment_id)
            validation_errors.append(f"must_be_visible_offscreen_violation:{segment_id}")

        if route == "first_last":
            missing_first = [role for role in must_be_visible_roles if not _text_mentions_role(first_frame_prompt, role)]
            missing_last = [role for role in must_be_visible_roles if not _text_mentions_role(last_frame_prompt, role)]
            if missing_first:
                shared_space_missing_segments.append(f"{segment_id}:first")
                segment["first_frame_prompt"] = _append_prompt_clause(first_frame_prompt, enforcement_clause)
                first_frame_prompt = str(segment.get("first_frame_prompt") or "").strip()
            if missing_last:
                shared_space_missing_segments.append(f"{segment_id}:last")
                segment["last_frame_prompt"] = _append_prompt_clause(last_frame_prompt, enforcement_clause)
                last_frame_prompt = str(segment.get("last_frame_prompt") or "").strip()

            first_offscreen_violations = [
                role
                for role in must_be_visible_roles
                if role not in may_be_offscreen_roles
                and bool(re.search(rf"\b{re.escape(role)}\b.{0,40}{offscreen_pattern}", first_frame_prompt.lower()))
            ]
            last_offscreen_violations = [
                role
                for role in must_be_visible_roles
                if role not in may_be_offscreen_roles
                and bool(re.search(rf"\b{re.escape(role)}\b.{0,40}{offscreen_pattern}", last_frame_prompt.lower()))
            ]
            if first_offscreen_violations:
                offscreen_violation_segments.append(f"{segment_id}:first")
                validation_errors.append(f"must_be_visible_offscreen_violation:{segment_id}:first")
            if last_offscreen_violations:
                offscreen_violation_segments.append(f"{segment_id}:last")
                validation_errors.append(f"must_be_visible_offscreen_violation:{segment_id}:last")

        segments_out.append(segment)

    patched["segments"] = segments_out
    return patched, {
        "scene_prompts_shared_space_rule_applied": enforce_shared_space_rule,
        "scene_prompts_must_be_visible_roles": must_be_visible_roles,
        "scene_prompts_shared_space_missing_segments": list(dict.fromkeys([seg for seg in shared_space_missing_segments if seg])),
        "scene_prompts_offscreen_violation_segments": list(dict.fromkeys([seg for seg in offscreen_violation_segments if seg])),
    }, validation_errors


def _apply_storyboard_stage_metadata_passthrough(
    prompts_v11: dict[str, Any],
    prompt_rows: list[dict[str, Any]],
    package: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    patched = dict(prompts_v11)
    audio_map = _safe_dict(package.get("audio_map"))
    global_audio_owner = str(audio_map.get("vocal_owner_role") or "").strip()
    storyboard_by_segment = {
        str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
        for row in prompt_rows
        if str(_safe_dict(row).get("segment_id") or "").strip()
    }
    role_fields = ("primary_role", "visual_focus_role", "speaker_role")
    lipsync_fields = (
        "lip_sync_allowed",
        "lip_sync_priority",
        "mouth_visible_required",
        "listener_reaction_allowed",
        "spoken_line",
        "speaker_confidence",
    )

    segments_out: list[dict[str, Any]] = []
    role_present_count = 0
    lipsync_present_count = 0
    missing_role_segments: list[str] = []
    ia2v_audio_driven_count = 0
    lip_sync_only_policy_applied = _is_lip_sync_only_character_1(package)
    lip_sync_only_i2v_segments: list[str] = []
    lip_sync_only_ia2v_segments: list[str] = []
    repaired_owner_segments: list[str] = []
    repaired_owner_count = 0
    for raw_segment in _safe_list(prompts_v11.get("segments")):
        segment = dict(_safe_dict(raw_segment))
        segment_id = str(segment.get("segment_id") or "").strip()
        storyboard_row = _safe_dict(storyboard_by_segment.get(segment_id))
        route = str(segment.get("route") or storyboard_row.get("route") or "i2v").strip().lower() or "i2v"
        segment["route"] = route

        segment["primary_role"] = str(storyboard_row.get("primary_role") or "").strip()
        segment["visual_focus_role"] = str(storyboard_row.get("visual_focus_role") or "").strip()
        segment["speaker_role"] = str(storyboard_row.get("speaker_role") or "").strip()
        segment["vocal_owner_role"] = str(storyboard_row.get("vocal_owner_role") or "").strip()
        segment["reaction_role"] = str(storyboard_row.get("reaction_role") or "").strip()
        segment["spoken_line"] = str(storyboard_row.get("spoken_line") or "").strip()
        segment["lip_sync_priority"] = str(storyboard_row.get("lip_sync_priority") or "").strip()
        segment["speaker_confidence"] = _coerce_speaker_confidence(storyboard_row.get("speaker_confidence"))
        segment["lip_sync_allowed"] = bool(storyboard_row.get("lip_sync_allowed"))
        segment["mouth_visible_required"] = bool(storyboard_row.get("mouth_visible_required"))
        segment["listener_reaction_allowed"] = bool(storyboard_row.get("listener_reaction_allowed"))
        segment["singing_readiness_required"] = bool(storyboard_row.get("singing_readiness_required"))
        segment["object_action_allowed"] = bool(storyboard_row.get("object_action_allowed"))
        segment["foreground_performance_rule"] = str(storyboard_row.get("foreground_performance_rule") or "").strip()

        if route == "ia2v":
            lip_sync_only_ia2v_segments.append(segment_id)
            inferred_owner = str(storyboard_row.get("vocal_owner_role") or "").strip()
            if inferred_owner.lower() in {"", "unknown", "auto", "none", "null"}:
                inferred_owner = global_audio_owner
            if inferred_owner.lower() in {"", "unknown", "auto", "none", "null"}:
                inferred_owner = "character_1"
            repaired = str(storyboard_row.get("vocal_owner_role") or "").strip().lower() in {"", "unknown", "auto", "none", "null"}
            if repaired and segment_id:
                repaired_owner_segments.append(segment_id)
                repaired_owner_count += 1
            segment["speaker_role"] = inferred_owner
            segment["vocal_owner_role"] = inferred_owner
            segment["lip_sync_allowed"] = True
            segment["lip_sync_priority"] = "primary"
            segment["mouth_visible_required"] = True
            segment["singing_readiness_required"] = True
            apply_ia2v_lipsync_canon_to_prompt_row(segment, source_scene=storyboard_row)
        elif lip_sync_only_policy_applied and route == "i2v":
            lip_sync_only_i2v_segments.append(segment_id)
            segment["primary_role"] = ""
            segment["speaker_role"] = ""
            segment["vocal_owner_role"] = ""
            segment["lip_sync_allowed"] = False
            segment["lip_sync_priority"] = "none"
            segment["mouth_visible_required"] = False
            segment["singing_readiness_required"] = False
            segment["listener_reaction_allowed"] = False
            segment["visual_focus_role"] = "environment"
            segment["subject_priority"] = "environment"

        if lip_sync_only_policy_applied and route == "i2v":
            role_complete = True
            reaction_required = False
        else:
            role_complete = all(str(segment.get(field) or "").strip() for field in role_fields)
            reaction_required = bool(segment.get("listener_reaction_allowed")) or (
                str(segment.get("visual_focus_role") or "").strip() != str(segment.get("speaker_role") or "").strip()
            )
            if reaction_required and not str(segment.get("reaction_role") or "").strip():
                role_complete = False
        lipsync_complete = all(field in segment for field in lipsync_fields)
        if role_complete:
            role_present_count += 1
        else:
            missing_role_segments.append(segment_id)
        if lipsync_complete:
            lipsync_present_count += 1

        if route == "ia2v" and bool(segment.get("lip_sync_allowed")):
            ia2v_audio_driven_count += 1

        segments_out.append(segment)

    patched["segments"] = segments_out
    return patched, {
        "scene_prompts_role_metadata_present_count": role_present_count,
        "scene_prompts_lipsync_metadata_present_count": lipsync_present_count,
        "scene_prompts_missing_role_metadata_segments": [seg for seg in missing_role_segments if seg],
        "scene_prompts_ia2v_audio_driven_count": ia2v_audio_driven_count,
        "lip_sync_only_policy_applied": bool(lip_sync_only_policy_applied),
        "lip_sync_only_i2v_segments": [seg for seg in lip_sync_only_i2v_segments if seg],
        "lip_sync_only_ia2v_segments": [seg for seg in lip_sync_only_ia2v_segments if seg],
        "scene_prompts_ia2v_vocal_owner_repaired_count": repaired_owner_count,
        "scene_prompts_ia2v_vocal_owner_repaired_segments": [seg for seg in repaired_owner_segments if seg],
    }


def _build_legacy_bridge_from_v11(prompts_v11: dict[str, Any], prompt_rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Deterministic compatibility bridge only (derived from canonical v1.1; no creative authorship).
    by_segment = {str(_safe_dict(row).get("segment_id") or ""): _safe_dict(row) for row in _safe_list(prompts_v11.get("segments"))}
    scenes: list[dict[str, Any]] = []
    for row in prompt_rows:
        segment_id = str(row.get("segment_id") or "")
        route = str(row.get("route") or "i2v")
        seg = _safe_dict(by_segment.get(segment_id))
        prompt_notes = _safe_dict(seg.get("prompt_notes"))
        transition_notes = _safe_dict(prompt_notes.get("transition"))
        photo_prompt = str(seg.get("photo_prompt") or "").strip()
        video_prompt = str(seg.get("video_prompt") or "").strip()
        negative_prompt = str(seg.get("negative_prompt") or "").strip()
        start_prompt = str(seg.get("first_frame_prompt") or transition_notes.get("start_state") or "").strip()
        end_prompt = str(seg.get("last_frame_prompt") or transition_notes.get("end_state") or "").strip()
        scenes.append(
            {
                "scene_id": segment_id,
                "segment_id": segment_id,
                "route": route,
                "photo_prompt": photo_prompt,
                "video_prompt": video_prompt,
                "negative_prompt": negative_prompt,
                "primary_role": str(seg.get("primary_role") or row.get("primary_role") or "").strip(),
                "visual_focus_role": str(seg.get("visual_focus_role") or row.get("visual_focus_role") or "").strip(),
                "speaker_role": str(seg.get("speaker_role") or row.get("speaker_role") or "").strip(),
                "reaction_role": str(seg.get("reaction_role") or row.get("reaction_role") or "").strip(),
                "lip_sync_allowed": bool(seg.get("lip_sync_allowed") if "lip_sync_allowed" in seg else row.get("lip_sync_allowed")),
                "lip_sync_priority": str(seg.get("lip_sync_priority") or row.get("lip_sync_priority") or "").strip(),
                "mouth_visible_required": bool(
                    seg.get("mouth_visible_required") if "mouth_visible_required" in seg else row.get("mouth_visible_required")
                ),
                "singing_readiness_required": bool(
                    seg.get("singing_readiness_required")
                    if "singing_readiness_required" in seg
                    else row.get("singing_readiness_required")
                ),
                "listener_reaction_allowed": bool(
                    seg.get("listener_reaction_allowed")
                    if "listener_reaction_allowed" in seg
                    else row.get("listener_reaction_allowed")
                ),
                "object_action_allowed": bool(
                    seg.get("object_action_allowed") if "object_action_allowed" in seg else row.get("object_action_allowed")
                ),
                "foreground_performance_rule": str(
                    seg.get("foreground_performance_rule") or row.get("foreground_performance_rule") or ""
                ).strip(),
                "spoken_line": str(seg.get("spoken_line") or row.get("spoken_line") or "").strip(),
                "speaker_confidence": _coerce_speaker_confidence(
                    seg.get("speaker_confidence") if "speaker_confidence" in seg else row.get("speaker_confidence")
                ),
                "positive_video_prompt": video_prompt,
                "negative_video_prompt": negative_prompt,
                "start_image_prompt": start_prompt if route == "first_last" else "",
                "end_image_prompt": end_prompt if route == "first_last" else "",
                "prompt_notes": {
                    "emotion": str(prompt_notes.get("emotion") or "").strip(),
                    "world_anchor": str(prompts_v11.get("global_style_anchor") or "")[:300],
                    "legacy_bridge_from": "prompts_v1.1",
                },
            }
        )
        if str(route).strip().lower() == "ia2v":
            apply_ia2v_lipsync_canon_to_prompt_row(scenes[-1], source_scene={**seg, **row})
    return {
        "plan_version": SCENE_PROMPTS_PROMPT_VERSION,
        "mode": "clip",
        "scenes": scenes,
        "global_prompt_rules": ["legacy_bridge_from_prompts_v1.1"],
    }


def _is_generic_subject_text(text: str) -> bool:
    clean = str(text or "").strip().lower()
    if not clean:
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", clean) if w]
    if len(words) <= 2:
        return True
    return clean in _GENERIC_SUBJECT_TOKENS


def _has_role_omission(segment: dict[str, Any], role_row: dict[str, Any]) -> bool:
    primary_role = str(_safe_dict(role_row).get("primary_role") or "").strip()
    if not primary_role:
        return False
    visual = _safe_dict(segment.get("visual_description"))
    character = _safe_dict(segment.get("character_state"))
    subject = str(visual.get("subject_description") or "")
    pose = str(character.get("pose_presence") or "")
    facial = str(character.get("facial_expression") or "")
    return _is_generic_subject_text(subject) and _is_generic_subject_text(pose) and _is_generic_subject_text(facial)


def _build_drift_evidence_bundle(*, prompts_v11: dict[str, Any], segment: dict[str, Any], prompt_row: dict[str, Any], package: dict[str, Any]) -> str:
    story_core = _safe_dict(package.get("story_core"))
    connected_context_summary = _safe_dict(_safe_dict(package.get("input")).get("connected_context_summary")) or _safe_dict(
        package.get("connected_context_summary")
    )
    visual = _safe_dict(segment.get("visual_description"))
    environment = _safe_dict(segment.get("environment_details"))
    return " ".join(
        [
            str(prompts_v11.get("global_style_anchor") or ""),
            str(segment.get("photo_prompt") or visual.get("subject_description") or ""),
            str(segment.get("video_prompt") or visual.get("background_description") or ""),
            str(segment.get("negative_prompt") or ""),
            json.dumps(environment, ensure_ascii=False),
            json.dumps(_safe_dict(segment.get("prompt_notes")), ensure_ascii=False),
            str(_safe_dict(story_core.get("identity_lock")).get("summary") or ""),
            str(_safe_dict(story_core.get("world_lock")).get("summary") or ""),
            str(_safe_dict(story_core.get("style_lock")).get("summary") or ""),
            json.dumps(connected_context_summary, ensure_ascii=False),
            str(prompt_row.get("primary_role") or ""),
            " ".join(str(v) for v in _safe_list(prompt_row.get("secondary_roles"))),
        ]
    ).lower()


def _validate_prompts_v11(prompts_v11: dict[str, Any], prompt_rows: list[dict[str, Any]], package: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    expected_segment_ids = [str(_safe_dict(row).get("segment_id") or "") for row in prompt_rows]
    prompt_rows_by_segment = {str(_safe_dict(row).get("segment_id") or ""): _safe_dict(row) for row in prompt_rows}
    expected_route = {str(_safe_dict(row).get("segment_id") or ""): str(_safe_dict(row).get("route") or "i2v") for row in prompt_rows}
    actual_segments = _safe_list(prompts_v11.get("segments"))
    actual_segment_ids = [str(_safe_dict(row).get("segment_id") or "") for row in actual_segments]

    if str(prompts_v11.get("prompts_version") or "") != "1.1":
        return "PROMPTS_SCHEMA_INVALID", "prompts_version_must_be_1.1", {}
    if not str(prompts_v11.get("global_style_anchor") or "").strip() or not actual_segments:
        return "PROMPTS_SCHEMA_INVALID", "missing_global_style_anchor_or_segments", {}
    if set(actual_segment_ids) != set(expected_segment_ids):
        return "PROMPTS_SEGMENT_ID_MISMATCH", "segment_ids_mismatch", {}

    male_identity_leak_terms: set[str] = set()
    male_identity_leak_segments: list[str] = []
    male_identity_leak_fields: list[str] = []
    male_identity_leak_matches: list[dict[str, str]] = []
    lip_sync_only_i2v_violation_segments: list[str] = []
    lip_sync_only = _is_lip_sync_only_character_1(package)
    character_1_gender_hint = _get_character_1_gender_hint(package)
    transition_required_count = 0
    transition_present_count = 0
    for segment in actual_segments:
        row = _safe_dict(segment)
        segment_id = str(row.get("segment_id") or "")
        prompt_row = _safe_dict(prompt_rows_by_segment.get(segment_id))
        route = expected_route.get(segment_id, "i2v")
        if not str(row.get("scene_id") or "").strip():
            return "PROMPTS_SCHEMA_INVALID", f"missing_scene_id:{segment_id}", {}
        actual_route = str(row.get("route") or "").strip()
        if actual_route not in ALLOWED_ROUTES:
            return "PROMPTS_SCHEMA_INVALID", f"invalid_route:{segment_id}", {}
        if actual_route != route:
            return "PROMPTS_SCHEMA_INVALID", f"route_mismatch:{segment_id}", {}
        if (
            not str(row.get("photo_prompt") or "").strip()
            or not str(row.get("video_prompt") or "").strip()
            or not str(row.get("negative_prompt") or "").strip()
        ):
            return "PROMPTS_SCHEMA_INVALID", f"missing_prompt_fields:{segment_id}", {}
        if character_1_gender_hint == "male":
            for field in ("photo_prompt", "video_prompt", "negative_prompt", "first_frame_prompt", "last_frame_prompt"):
                field_text = str(row.get(field) or "")
                found = _find_stale_terms_token_safe(field_text, _MALE_STALE_VALIDATION_TERMS)
                if found:
                    male_identity_leak_terms.update(found)
                    male_identity_leak_segments.append(segment_id)
                    male_identity_leak_fields.append(f"{segment_id}:{field}")
                    for entry in _iter_stale_term_matches_with_excerpt(field_text, found):
                        male_identity_leak_matches.append(
                            {
                                "segment_id": segment_id,
                                "field": field,
                                "term": entry["term"],
                                "excerpt": entry["excerpt"],
                            }
                        )
        if lip_sync_only and route == "i2v":
            i2v_blob = " ".join(
                [
                    str(row.get("photo_prompt") or ""),
                    str(row.get("video_prompt") or ""),
                    str(row.get("positive_video_prompt") or ""),
                    str(row.get("first_frame_prompt") or ""),
                    str(row.get("last_frame_prompt") or ""),
                    str(row.get("primary_role") or ""),
                    str(row.get("visual_focus_role") or ""),
                    str(row.get("speaker_role") or ""),
                    str(row.get("vocal_owner_role") or ""),
                ]
            )
            if any(pattern.search(i2v_blob) for pattern in _LIP_SYNC_ONLY_I2V_VIOLATION_PATTERNS):
                lip_sync_only_i2v_violation_segments.append(segment_id)

        blob = " ".join(
            [
                str(prompts_v11.get("global_style_anchor") or ""),
                str(row.get("photo_prompt") or ""),
                str(row.get("video_prompt") or ""),
                str(row.get("negative_prompt") or ""),
                json.dumps(_safe_dict(row.get("prompt_notes")), ensure_ascii=False),
            ]
        ).lower()
        if any(token in blob for token in _TECHNICAL_TAG_PATTERNS):
            return "PROMPTS_TECHNICAL_TAGGING", f"technical_tagging:{segment_id}", {}
        if any(token in blob for token in _QUALITY_BUZZWORDS):
            return "PROMPTS_QUALITY_BUZZWORDS", f"quality_buzzwords:{segment_id}", {}
        if any(token in blob for token in _CAMERA_LEAK_PATTERNS) and any(regex.search(blob) for regex in _CAMERA_TECH_LEAK_REGEXES):
            return "PROMPTS_CAMERA_LEAKAGE", f"camera_leakage:{segment_id}", {}
        if any(regex.search(blob) for regex in _CAMERA_TECH_LEAK_REGEXES):
            return "PROMPTS_CAMERA_LEAKAGE", f"camera_leakage:{segment_id}", {}
        if any(token in blob for token in _ROUTE_DELIVERY_PATTERNS):
            return "PROMPTS_ROUTE_DELIVERY_LEAKAGE", f"route_delivery_leakage:{segment_id}", {}
        if _has_role_omission(
            {
                "visual_description": {"subject_description": str(row.get("photo_prompt") or "")},
                "character_state": {"pose_presence": str(row.get("video_prompt") or "")},
            },
            prompt_row,
        ):
            return "PROMPTS_ROLE_OMISSION", f"role_omission:{segment_id}", {}

        drift_blob = _build_drift_evidence_bundle(
            prompts_v11=prompts_v11,
            segment=row,
            prompt_row=prompt_row,
            package=package,
        )
        if any(token in drift_blob for token in _IDENTITY_DRIFT_TOKENS):
            return "PROMPTS_IDENTITY_DRIFT", f"identity_drift:{segment_id}", {}
        if any(token in drift_blob for token in _WORLD_DRIFT_TOKENS):
            return "PROMPTS_WORLD_DRIFT", f"world_drift:{segment_id}", {}

        if route == "first_last":
            transition_required_count += 1
            if _segment_has_transition_payload(row):
                transition_present_count += 1
            else:
                return "PROMPTS_MISSING_TRANSITION_DESCRIPTION", f"missing_transition_description:{segment_id}", {}

    if male_identity_leak_terms:
        return "PROMPTS_STALE_IDENTITY_LEAK_AFTER_SANITIZER", "stale_identity_leak_after_sanitizer", {
            "stale_identity_leak_after_sanitizer": True,
            "stale_identity_leak_terms": sorted(male_identity_leak_terms),
            "stale_identity_leak_segments": list(dict.fromkeys([seg for seg in male_identity_leak_segments if seg])),
            "stale_identity_leak_fields": list(dict.fromkeys([fld for fld in male_identity_leak_fields if fld])),
            "stale_identity_leak_matches": male_identity_leak_matches,
        }
    if lip_sync_only_i2v_violation_segments:
        return "PROMPTS_LIP_SYNC_ONLY_I2V_VISIBILITY_VIOLATION", "lip_sync_only_i2v_visibility_violation", {
            "lip_sync_only_i2v_visibility_violation": True,
            "lip_sync_only_i2v_visibility_violation_segments": list(
                dict.fromkeys([seg for seg in lip_sync_only_i2v_violation_segments if seg])
            ),
        }

    return "", "", {
        "scene_prompts_transition_required_count": transition_required_count,
        "scene_prompts_transition_present_count": transition_present_count,
    }


def _sanitize_identity_and_visibility_conflicts(
    payload: dict[str, Any],
    prompt_rows: list[dict[str, Any]],
    package: dict[str, Any],
    story_core: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    out = dict(payload)
    rows_by_id = {str(_safe_dict(r).get("segment_id") or "").strip(): _safe_dict(r) for r in prompt_rows}
    lip_sync_only = _is_lip_sync_only_character_1(package)
    stale_identity_removed = 0
    repaired_segments: list[str] = []
    i2v_visual_removed = 0
    lip_sync_only_i2v_segments: list[str] = []
    lip_sync_only_i2v_rebuilt_segments: list[str] = []
    gender_conflict_segments: list[str] = []
    gender_terms_removed: set[str] = set()
    gender_conflict_matches: list[dict[str, str]] = []
    stale_identity_removed_by_gender = 0
    stale_wardrobe_removed_by_gender = 0
    first_last_frame_rebuilt_segments: list[str] = []
    prompt_rebuilt_after_bad_cleanup_segments: list[str] = []
    prompt_rebuilt_after_bad_cleanup_fields: list[str] = []
    global_style_anchor_rebuilt_due_to_identity_conflict = False
    global_style_anchor_rebuild_source = ""
    character_1_gender_hint = _get_character_1_gender_hint(package)
    apply_male_stale_cleanup = character_1_gender_hint == "male"
    global_anchor, anchor_changed = _sanitize_global_style_anchor(str(out.get("global_style_anchor") or ""), story_core)
    if apply_male_stale_cleanup:
        removed_anchor_terms = set(_find_stale_terms_token_safe(global_anchor, _MALE_CONFLICT_STALE_TERMS))
        if removed_anchor_terms:
            global_anchor = _rebuild_global_style_anchor_from_story_core(story_core)
            global_style_anchor_rebuilt_due_to_identity_conflict = True
            global_style_anchor_rebuild_source = "story_core_world_style"
            gender_terms_removed.update(removed_anchor_terms)
            stale_identity_removed_by_gender += len([t for t in removed_anchor_terms if t in _STALE_IDENTITY_TERMS])
            stale_wardrobe_removed_by_gender += len([t for t in removed_anchor_terms if t in _STALE_WARDROBE_TERMS])
            gender_conflict_segments.append("global_style_anchor")
    out["global_style_anchor"] = global_anchor
    segments_out: list[dict[str, Any]] = []
    for raw in _safe_list(out.get("segments")):
        seg = dict(_safe_dict(raw))
        segment_id = str(seg.get("segment_id") or "").strip()
        route = str(seg.get("route") or "").strip().lower()
        prompt_row = _safe_dict(rows_by_id.get(segment_id))
        world_context = _build_current_world_context_for_fallback(
            package=package,
            story_core=story_core,
            prompt_row=prompt_row,
            global_style_anchor=str(out.get("global_style_anchor") or ""),
        )
        mutated = False
        if lip_sync_only and route == "i2v":
            seg["photo_prompt"] = (
                f"Environment cutaway in the current grounded world. {world_context}. "
                "No dominant performer; singer remains offscreen voiceover."
            )
            seg["video_prompt"] = (
                f"Environment motion cutaway in the current grounded world. {world_context}. "
                "Subtle ambient motion only; no visible vocal performance or dominant performer."
            )
            seg["positive_video_prompt"] = seg["video_prompt"]
            seg["first_frame_prompt"] = "Environment-focused cutaway in the current grounded world. No main performer visible."
            if route != "first_last":
                seg["last_frame_prompt"] = ""
            seg["primary_role"] = ""
            seg["visual_focus_role"] = "environment"
            seg["speaker_role"] = ""
            seg["vocal_owner_role"] = ""
            seg["lip_sync_allowed"] = False
            seg["lip_sync_priority"] = "none"
            seg["mouth_visible_required"] = False
            seg["singing_readiness_required"] = False
            seg["listener_reaction_allowed"] = False
            base_negative = str(seg.get("negative_prompt") or "")
            seg["negative_prompt"] = _append_prompt_clause(
                base_negative,
                "main performer visible, singer visible, lip-sync, mouth close-up, character_1 as main subject, hero close-up",
            )
            i2v_visual_removed += 1
            lip_sync_only_i2v_segments.append(segment_id)
            mutated = True

        def _rebuild_bad_cleanup_field(field_name: str) -> str:
            if lip_sync_only and route == "i2v":
                if field_name == "photo_prompt":
                    return (
                        f"Environment cutaway in the current grounded world. {world_context}. "
                        "No dominant performer; singer remains offscreen voiceover."
                    )
                if field_name in {"video_prompt", "positive_video_prompt"}:
                    return (
                        f"Environment motion cutaway in the current grounded world. {world_context}. "
                        "Subtle ambient motion only; no visible vocal performance or dominant performer."
                    )
                if field_name == "first_frame_prompt":
                    return "Environment cutaway in the current grounded world with no dominant performer."
                if field_name == "last_frame_prompt":
                    return (
                        ""
                        if route != "first_last"
                        else "Environment cutaway in the current grounded world with no dominant performer."
                    )
            if route == "ia2v":
                if field_name == "photo_prompt":
                    return (
                        "Current character_1 from connected reference, face and upper body readable, "
                        "grounded realistic current-world atmosphere, performer-first still frame for lip-sync. "
                        f"{world_context}."
                    )
                if field_name in {"video_prompt", "positive_video_prompt"}:
                    return (
                        "Current character_1 from connected reference, face and mouth clearly visible, performer-first "
                        "lip-sync, natural jaw motion, restrained head movement, steady camera, "
                        f"grounded realistic current-world atmosphere. {world_context}."
                    )
                if field_name == "first_frame_prompt":
                    return (
                        "Current character_1 from connected reference, face and mouth visible, "
                        f"grounded realistic current-world atmosphere. {world_context}."
                    )
                if field_name == "last_frame_prompt":
                    return "" if route != "first_last" else (
                        "Current character_1 from connected reference, face and mouth visible, "
                        f"grounded realistic current-world atmosphere. {world_context}."
                    )
            return "Grounded realistic frame, current package identity and world continuity preserved."
        for field in ("photo_prompt", "video_prompt", "positive_video_prompt", "first_frame_prompt", "last_frame_prompt"):
            value = str(seg.get(field) or "")
            if any(p.search(value) for p in _IDENTITY_REFERENCE_LEAK_PATTERNS):
                stale_identity_removed += 1
                mutated = True
                if lip_sync_only and route == "i2v":
                    seg[field] = str(seg.get("video_prompt") or seg.get("photo_prompt") or "")
                else:
                    seg[field] = re.sub(r"(?i)\bshow the same (woman|man|person)[^.]*\.?", " ", value).strip()
        removed_in_segment: set[str] = set()
        if apply_male_stale_cleanup:
            for field in ("first_frame_prompt", "last_frame_prompt"):
                original = str(seg.get(field) or "")
                removed_terms = set(_find_stale_terms_token_safe(original, _MALE_CONFLICT_STALE_TERMS))
                if not removed_terms:
                    continue
                seg[field] = _rebuild_bad_cleanup_field(field)
                for entry in _iter_stale_term_matches_with_excerpt(original, removed_terms):
                    gender_conflict_matches.append(
                        {"segment_id": segment_id, "field": field, "term": entry["term"], "excerpt": entry["excerpt"]}
                    )
                removed_in_segment.update(removed_terms)
                if segment_id:
                    first_last_frame_rebuilt_segments.append(segment_id)
                mutated = True

            for field in ("photo_prompt", "video_prompt", "positive_video_prompt", "first_frame_prompt", "last_frame_prompt", "negative_prompt"):
                original_value = str(seg.get(field) or "")
                clean_value, removed_terms = _remove_stale_terms_token_safe(original_value, _MALE_CONFLICT_STALE_TERMS)
                if not removed_terms:
                    continue
                seg[field] = clean_value
                for entry in _iter_stale_term_matches_with_excerpt(original_value, removed_terms):
                    gender_conflict_matches.append(
                        {"segment_id": segment_id, "field": field, "term": entry["term"], "excerpt": entry["excerpt"]}
                    )
                if _is_bad_prompt_cleanup_scene_prompts(clean_value):
                    seg[field] = _rebuild_bad_cleanup_field(field)
                    if segment_id:
                        prompt_rebuilt_after_bad_cleanup_segments.append(segment_id)
                        prompt_rebuilt_after_bad_cleanup_fields.append(f"{segment_id}:{field}")
                removed_in_segment.update(removed_terms)
                mutated = True
            prompt_notes = _safe_dict(seg.get("prompt_notes"))
            for pn_field in ("emotion", "world_anchor"):
                pn_value = str(prompt_notes.get(pn_field) or "")
                cleaned_value, removed_pn_terms = _remove_stale_terms_token_safe(pn_value, _MALE_CONFLICT_STALE_TERMS)
                if not removed_pn_terms:
                    continue
                removed_in_segment.update(removed_pn_terms)
                for entry in _iter_stale_term_matches_with_excerpt(pn_value, removed_pn_terms):
                    gender_conflict_matches.append(
                        {"segment_id": segment_id, "field": f"prompt_notes.{pn_field}", "term": entry["term"], "excerpt": entry["excerpt"]}
                    )
                if _is_bad_prompt_cleanup_scene_prompts(cleaned_value):
                    cleaned_value = _rebuild_bad_cleanup_field("first_frame_prompt")
                    if segment_id:
                        prompt_rebuilt_after_bad_cleanup_segments.append(segment_id)
                        prompt_rebuilt_after_bad_cleanup_fields.append(f"{segment_id}:prompt_notes.{pn_field}")
                prompt_notes[pn_field] = cleaned_value
                mutated = True
            notes = [str(v or "") for v in _safe_list(prompt_notes.get("notes"))]
            notes_changed = False
            for notes_idx, note_text in enumerate(notes):
                cleaned_note, removed_note_terms = _remove_stale_terms_token_safe(note_text, _MALE_CONFLICT_STALE_TERMS)
                if not removed_note_terms:
                    continue
                removed_in_segment.update(removed_note_terms)
                for entry in _iter_stale_term_matches_with_excerpt(note_text, removed_note_terms):
                    gender_conflict_matches.append(
                        {
                            "segment_id": segment_id,
                            "field": f"prompt_notes.notes[{notes_idx}]",
                            "term": entry["term"],
                            "excerpt": entry["excerpt"],
                        }
                    )
                if _is_bad_prompt_cleanup_scene_prompts(cleaned_note):
                    cleaned_note = _rebuild_bad_cleanup_field("first_frame_prompt")
                    if segment_id:
                        prompt_rebuilt_after_bad_cleanup_segments.append(segment_id)
                        prompt_rebuilt_after_bad_cleanup_fields.append(f"{segment_id}:prompt_notes.notes[{notes_idx}]")
                notes[notes_idx] = cleaned_note
                notes_changed = True
                mutated = True
            if notes_changed:
                prompt_notes["notes"] = notes
                seg["prompt_notes"] = prompt_notes
        if lip_sync_only and route == "i2v":
            rebuilt_for_i2v_anchor = False
            for field in ("photo_prompt", "video_prompt", "positive_video_prompt", "first_frame_prompt", "last_frame_prompt"):
                field_value = str(seg.get(field) or "")
                if any(pattern.search(field_value) for pattern in _LIP_SYNC_ONLY_I2V_IDENTITY_ANCHOR_PATTERNS):
                    seg[field] = _rebuild_bad_cleanup_field(field)
                    rebuilt_for_i2v_anchor = True
                    if segment_id:
                        prompt_rebuilt_after_bad_cleanup_fields.append(f"{segment_id}:{field}")
            if rebuilt_for_i2v_anchor and segment_id:
                lip_sync_only_i2v_rebuilt_segments.append(segment_id)
                prompt_rebuilt_after_bad_cleanup_segments.append(segment_id)
                mutated = True
                i2v_visual_removed += 1
        if removed_in_segment:
            gender_terms_removed.update(removed_in_segment)
            stale_identity_removed_by_gender += len([t for t in removed_in_segment if t in _STALE_IDENTITY_TERMS])
            stale_wardrobe_removed_by_gender += len([t for t in removed_in_segment if t in _STALE_WARDROBE_TERMS])
            if segment_id:
                gender_conflict_segments.append(segment_id)
        if mutated and segment_id:
            repaired_segments.append(segment_id)
        segments_out.append(seg)
    out["segments"] = segments_out
    gender_conflict_detected = bool(gender_terms_removed)
    stale_identity_removed_total = stale_identity_removed + stale_identity_removed_by_gender
    return out, {
        "scene_prompts_identity_conflict_repaired": bool(repaired_segments or anchor_changed),
        "scene_prompts_identity_conflict_repaired_segments": list(dict.fromkeys(repaired_segments)),
        "scene_prompts_identity_gender_conflict_detected": gender_conflict_detected,
        "scene_prompts_identity_gender_conflict_terms_removed": sorted(gender_terms_removed),
        "scene_prompts_identity_gender_conflict_matches": gender_conflict_matches,
        "scene_prompts_identity_gender_conflict_segments": list(dict.fromkeys([seg for seg in gender_conflict_segments if seg])),
        "scene_prompts_stale_identity_clause_removed_count": stale_identity_removed_total,
        "scene_prompts_stale_wardrobe_clause_removed_count": stale_wardrobe_removed_by_gender,
        "scene_prompts_global_style_anchor_rebuilt_due_to_identity_conflict": global_style_anchor_rebuilt_due_to_identity_conflict,
        "scene_prompts_global_style_anchor_rebuild_source": global_style_anchor_rebuild_source,
        "scene_prompts_first_last_frame_rebuilt_count": len(list(dict.fromkeys(first_last_frame_rebuilt_segments))),
        "scene_prompts_first_last_frame_rebuilt_segments": list(dict.fromkeys([seg for seg in first_last_frame_rebuilt_segments if seg])),
        "scene_prompts_prompt_rebuilt_after_bad_cleanup_count": len(
            list(dict.fromkeys(prompt_rebuilt_after_bad_cleanup_segments))
        ),
        "scene_prompts_prompt_rebuilt_after_bad_cleanup_segments": list(
            dict.fromkeys([seg for seg in prompt_rebuilt_after_bad_cleanup_segments if seg])
        ),
        "scene_prompts_prompt_rebuilt_after_bad_cleanup_fields": list(
            dict.fromkeys([fld for fld in prompt_rebuilt_after_bad_cleanup_fields if fld])
        ),
        "stale_identity_clause_removed_count": stale_identity_removed_total,
        "stale_wardrobe_clause_removed_count": stale_wardrobe_removed_by_gender,
        "lip_sync_only_i2v_hero_visual_removed_count": i2v_visual_removed,
        "lip_sync_only_i2v_segments": list(dict.fromkeys([seg for seg in lip_sync_only_i2v_segments if seg])),
        "lip_sync_only_i2v_rebuilt_segments": list(dict.fromkeys([seg for seg in lip_sync_only_i2v_rebuilt_segments if seg])),
        "lip_sync_only_policy_applied": bool(lip_sync_only),
    }


def _diagnose_ia2v_canonical_source(
    *,
    canonical_source: str,
    prompts_v11: dict[str, Any],
    normalized: dict[str, Any],
) -> dict[str, Any]:
    if canonical_source == "prompts_v1.1_segments":
        rows = _safe_list(prompts_v11.get("segments"))
    else:
        rows = _safe_list(normalized.get("scenes"))
    applied_ids: list[str] = []
    has_lipsync_prompt = True
    for row in rows:
        segment = _safe_dict(row)
        if str(segment.get("route") or "").strip().lower() != "ia2v":
            continue
        video_prompt = str(segment.get("video_prompt") or "")
        segment_id = str(segment.get("segment_id") or segment.get("scene_id") or "").strip()
        if video_prompt.startswith("Use the uploaded image as the exact first frame and identity anchor."):
            if segment_id:
                applied_ids.append(segment_id)
        else:
            has_lipsync_prompt = False
    return {
        "scene_prompts_ia2v_canon_applied_count": len(applied_ids),
        "scene_prompts_ia2v_canon_applied_segment_ids": applied_ids,
        "scene_prompts_ia2v_canonical_source_checked": True,
        "scene_prompts_ia2v_canonical_source_has_lipsync_prompt": bool(has_lipsync_prompt),
    }


def build_gemini_scene_prompts(
    *,
    api_key: str,
    package: dict[str, Any],
    validation_feedback: str = "",
    compact_retry: bool = False,
    force_rebuild_from_scene_plan: bool = False,
) -> dict[str, Any]:
    prompt_rows, aux = _build_prompt_rows(package)
    story_core = _safe_dict(aux.get("story_core"))
    global_style_anchor = _build_global_style_anchor(story_core)
    active_model_id = _resolve_active_video_model_id(package)
    route_capability_mode = "scene_prompts_engine_agnostic_guard"
    prompt_capability_guard_applied = bool(active_model_id)
    diagnostics: dict[str, Any] = {
        "scene_prompts_backend": "gemini",
        "scene_prompts_prompt_version": SCENE_PROMPTS_PROMPT_VERSION,
        "scene_prompts_stage_source": "current_package",
        "scene_count": len(prompt_rows),
        "scene_prompts_prompts_version": "1.1",
        "scene_prompts_segment_count_expected": len(prompt_rows),
        "scene_prompts_segment_count_actual": 0,
        "scene_prompts_segment_coverage_ok": False,
        "scene_prompts_uses_segment_id_canonical": bool(aux.get("uses_segment_id_canonical")),
        "scene_prompts_uses_legacy_bridge": False,
        "scene_prompts_legacy_bridge_generated": False,
        "scene_prompts_legacy_bridge_present": False,
        "scene_prompts_legacy_bridge_mode": "compatibility_derived_alias",
        "scene_prompts_canonical_source": "prompts_v1.1_segments",
        "scene_prompts_global_style_anchor_present": bool(global_style_anchor),
        "scene_prompts_transition_required_count": sum(1 for row in prompt_rows if str(row.get("route") or "") == "first_last"),
        "scene_prompts_transition_present_count": 0,
        "scene_prompts_error_code": "",
        "scene_prompts_validation_error": "",
        "scene_prompts_raw_model_response_preview": "",
        "scene_prompts_parsed_payload_preview": "",
        "scene_prompts_sanitized_payload_preview": "",
        "scene_prompts_normalized_scene_prompts_preview": "",
        "scene_prompts_dropped_non_canonical_fields": [],
        "scene_prompts_top_level_list_unwrapped": False,
        "scene_prompts_top_level_list_kind": "",
        "scene_prompts_top_level_list_length": 0,
        "scene_prompts_expected_segment_ids": [str(_safe_dict(row).get("segment_id") or "") for row in prompt_rows],
        "scene_prompts_seen_segment_ids": [],
        "scene_prompts_missing_segment_ids": [],
        "scene_prompts_extra_segment_ids": [],
        "scene_prompts_snapshot_restored": False,
        "scene_prompts_failure_reason": "",
        "scene_prompts_configured_timeout_sec": get_scenario_stage_timeout("scene_prompts"),
        "scene_prompts_timeout_stage_policy_name": scenario_timeout_policy_name("scene_prompts"),
        "scene_prompts_timed_out": False,
        "scene_prompts_timeout_retry_attempted": False,
        "scene_prompts_response_was_empty_after_timeout": False,
        "scene_prompts_empty_count": 0,
        "scene_prompts_empty_scene_ids": [],
        "scene_prompts_rebuilt_count": 0,
        "scene_prompts_rebuilt_scene_ids": [],
        "scene_prompts_valid_count": 0,
        "scene_prompts_role_metadata_present_count": 0,
        "scene_prompts_lipsync_metadata_present_count": 0,
        "scene_prompts_missing_role_metadata_segments": [],
        "scene_prompts_ia2v_audio_driven_count": 0,
        "scene_prompts_ia2v_canon_applied_count": 0,
        "scene_prompts_ia2v_canon_applied_segment_ids": [],
        "scene_prompts_ia2v_canonical_source_checked": False,
        "scene_prompts_ia2v_canonical_source_has_lipsync_prompt": False,
        "scene_prompts_shared_space_rule_applied": False,
        "scene_prompts_must_be_visible_roles": [],
        "scene_prompts_shared_space_missing_segments": [],
        "scene_prompts_offscreen_violation_segments": [],
        "scene_prompts_legacy_bridge_used": False,
        "scene_prompts_request_mode": "compact_retry" if compact_retry else "full",
        "scene_prompts_fallback_rebuild_from_scene_plan": False,
        "active_video_model_capability_profile": active_model_id,
        "active_route_capability_mode": route_capability_mode,
        "prompt_capability_guard_applied": prompt_capability_guard_applied,
        "capability_rules_source_version": get_capability_rules_source_version(),
    }
    diagnostics.update(_character_1_identity_diag(package))

    empty_canonical = {"prompts_version": "1.1", "global_style_anchor": global_style_anchor, "segments": []}
    if not prompt_rows:
        diagnostics["scene_prompts_segment_coverage_ok"] = True
        diagnostics["scene_prompts_uses_legacy_bridge"] = False
        return {
            "ok": True,
            "scene_prompts": {**empty_canonical, "legacy_bridge": {"plan_version": SCENE_PROMPTS_PROMPT_VERSION, "mode": "clip", "scenes": []}},
            "error": "",
            "validation_error": "",
            "used_fallback": False,
            "diagnostics": diagnostics,
        }

    if force_rebuild_from_scene_plan:
        raw_payload = _build_prompts_v11_fallback_payload(
            prompt_rows=prompt_rows,
            story_core=story_core,
            global_style_anchor=global_style_anchor,
        )
        diagnostics["scene_prompts_fallback_rebuild_from_scene_plan"] = True
        diagnostics["scene_prompts_used_fallback"] = True
        diagnostics["scene_prompts_failure_reason"] = "scene_prompts_fallback_rebuild_from_scene_plan"
    else:
        prompt = (
            _build_prompts_v11_compact_retry_prompt(
                prompt_rows=prompt_rows,
                global_style_anchor=global_style_anchor,
                package=package,
                validation_feedback=validation_feedback,
            )
            if compact_retry
            else _build_prompts_v11_prompt(
                prompt_rows=prompt_rows,
                global_style_anchor=global_style_anchor,
                package=package,
                validation_feedback=validation_feedback,
            )
        )
        raw_payload = {}
    error = ""
    configured_timeout = get_scenario_stage_timeout("scene_prompts")
    if not force_rebuild_from_scene_plan:
        try:
            response = post_generate_content(
                api_key=str(api_key or "").strip(),
                model="gemini-3-flash-preview",
                body={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
                },
                timeout=configured_timeout,
            )
            if isinstance(response, dict) and response.get("__http_error__"):
                raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")
            raw_text = _extract_gemini_text(response)
            diagnostics["scene_prompts_raw_model_response_preview"] = _preview_payload(raw_text)
            sanitized_text = _strip_json_code_fences(raw_text)
            parsed_payload = _extract_json_obj(sanitized_text)
            diagnostics["scene_prompts_parsed_payload_preview"] = _preview_payload(parsed_payload)
            raw_payload, dropped_fields, top_level_list_diag = _coerce_prompts_v11_payload(parsed_payload, prompt_rows)
            diagnostics["scene_prompts_sanitized_payload_preview"] = _preview_payload(raw_payload)
            diagnostics["scene_prompts_dropped_non_canonical_fields"] = dropped_fields
            diagnostics.update(top_level_list_diag)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            timeout_error = is_timeout_error(error)
            diagnostics["scene_prompts_timed_out"] = timeout_error
            diagnostics["scene_prompts_response_was_empty_after_timeout"] = timeout_error
            diagnostics["scene_prompts_failure_reason"] = (
                "scene_prompts_timeout_empty_response" if timeout_error else f"transport_or_parse_error:{error[:240]}"
            )

    if not str(raw_payload.get("global_style_anchor") or "").strip():
        raw_payload["global_style_anchor"] = global_style_anchor
    if not str(raw_payload.get("prompts_version") or "").strip():
        raw_payload["prompts_version"] = "1.1"

    raw_payload, de_technicalization_diag = _sanitize_prompts_v11_wording(raw_payload)
    diagnostics.update(de_technicalization_diag)
    raw_payload, required_fields_diag = _repair_prompts_v11_required_fields(raw_payload, prompt_rows, story_core)
    diagnostics.update(required_fields_diag)
    raw_payload, shared_space_diag, shared_space_validation_errors = _apply_prompts_v11_shared_space_post_repair(raw_payload, story_core)
    diagnostics.update(shared_space_diag)
    raw_payload, passthrough_diag = _apply_storyboard_stage_metadata_passthrough(raw_payload, prompt_rows, package)
    diagnostics.update(passthrough_diag)
    raw_payload, identity_sanitize_diag = _sanitize_identity_and_visibility_conflicts(
        raw_payload,
        prompt_rows,
        package,
        story_core,
    )
    diagnostics.update(identity_sanitize_diag)

    error_code, validation_error, validation_diag = _validate_prompts_v11(raw_payload, prompt_rows, package)
    if shared_space_validation_errors:
        error_code = "PROMPTS_SCHEMA_INVALID"
        validation_error = ";".join(dict.fromkeys(shared_space_validation_errors))
    if int(diagnostics.get("scene_prompts_empty_count") or 0) > 0:
        error_code = "PROMPTS_SCHEMA_INVALID"
        validation_error = "scene_prompts_empty_required_fields"
    diagnostics["scene_prompts_error_code"] = error_code
    diagnostics["scene_prompts_validation_error"] = validation_error
    diagnostics.update(validation_diag)
    diagnostics["scene_prompts_segment_count_actual"] = len(_safe_list(raw_payload.get("segments")))
    diagnostics["scene_prompts_seen_segment_ids"] = [
        str(_safe_dict(row).get("segment_id") or "").strip() for row in _safe_list(raw_payload.get("segments"))
    ]
    expected_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in prompt_rows]
    seen_ids = [seg for seg in diagnostics["scene_prompts_seen_segment_ids"] if seg]
    diagnostics["scene_prompts_missing_segment_ids"] = [seg for seg in expected_ids if seg not in seen_ids]
    diagnostics["scene_prompts_extra_segment_ids"] = [seg for seg in seen_ids if seg not in expected_ids]
    diagnostics["scene_prompts_segment_coverage_ok"] = (
        diagnostics["scene_prompts_segment_count_actual"] == diagnostics["scene_prompts_segment_count_expected"]
        and not diagnostics["scene_prompts_missing_segment_ids"]
        and not diagnostics["scene_prompts_extra_segment_ids"]
    )
    diagnostics["scene_prompts_normalized_scene_prompts_preview"] = _preview_payload(
        {
            "prompts_version": raw_payload.get("prompts_version"),
            "global_style_anchor": raw_payload.get("global_style_anchor"),
            "segments": _safe_list(raw_payload.get("segments"))[:2],
        }
    )
    if diagnostics.get("scene_prompts_timed_out") and diagnostics.get("scene_prompts_response_was_empty_after_timeout"):
        error_code = "scene_prompts_timeout_empty_response"
        validation_error = "scene_prompts_timeout_empty_response"
        diagnostics["scene_prompts_error_code"] = "scene_prompts_timeout_empty_response"
        diagnostics["scene_prompts_validation_error"] = "scene_prompts_timeout_empty_response"
        diagnostics["scene_prompts_failure_reason"] = "scene_prompts_timeout_empty_response"
    if error_code or validation_error:
        diagnostics["scene_prompts_failure_reason"] = str(validation_error or error_code).strip()
    if diagnostics.get("scene_prompts_timed_out"):
        diagnostics["scene_prompts_error_code"] = (
            "scene_prompts_timeout_empty_response"
            if diagnostics.get("scene_prompts_response_was_empty_after_timeout")
            else "scene_prompts_timeout"
        )
        validation_reason = str(diagnostics.get("scene_prompts_failure_reason") or "").strip()
        diagnostics["scene_prompts_failure_reason"] = (
            f"scene_prompts_timeout; downstream_validation={validation_reason}" if validation_reason else "scene_prompts_timeout"
        )

    legacy_bridge = _build_legacy_bridge_from_v11(raw_payload, prompt_rows)
    diagnostics["scene_prompts_legacy_bridge_generated"] = bool(_safe_list(legacy_bridge.get("scenes")))
    diagnostics["scene_prompts_legacy_bridge_present"] = bool(legacy_bridge)
    diagnostics["scene_prompts_uses_legacy_bridge"] = bool(_legacy_bridge_requested(package))
    diagnostics["scene_prompts_legacy_bridge_used"] = bool(_legacy_bridge_requested(package))
    scene_prompts = {
        "prompts_version": str(raw_payload.get("prompts_version") or ""),
        "global_style_anchor": str(raw_payload.get("global_style_anchor") or global_style_anchor),
        "segments": _safe_list(raw_payload.get("segments")),
        "legacy_bridge": legacy_bridge,
        "plan_version": SCENE_PROMPTS_PROMPT_VERSION,
        "mode": "clip",
        "scenes": _safe_list(legacy_bridge.get("scenes")),
        "global_prompt_rules": _safe_list(legacy_bridge.get("global_prompt_rules")),
    }
    diagnostics.update(
        _diagnose_ia2v_canonical_source(
            canonical_source=str(diagnostics.get("scene_prompts_canonical_source") or ""),
            prompts_v11=raw_payload,
            normalized=scene_prompts,
        )
    )
    used_fallback = bool(force_rebuild_from_scene_plan or diagnostics.get("scene_prompts_fallback_rebuild_from_scene_plan"))
    has_validation_error = bool(validation_error)
    has_error_code = bool(error_code)
    has_transport_error = bool(error)
    has_segments = bool(_safe_list(scene_prompts.get("segments")))
    ok = (
        (not has_transport_error)
        and (not has_error_code)
        and (not has_validation_error)
        and has_segments
        and scene_prompts.get("prompts_version") == "1.1"
        and bool(diagnostics.get("scene_prompts_segment_coverage_ok"))
    )
    if ok and used_fallback and diagnostics.get("scene_prompts_fallback_rebuild_from_scene_plan"):
        diagnostics["scene_prompts_timed_out"] = False
        diagnostics["scene_prompts_response_was_empty_after_timeout"] = False
        diagnostics["scene_prompts_error_code"] = ""
        diagnostics["scene_prompts_validation_error"] = ""
        diagnostics["scene_prompts_failure_reason"] = "scene_prompts_fallback_rebuild_from_scene_plan"
        error_code = ""
        validation_error = ""
        error = ""
    final_error = (
        (
            "scene_prompts_timeout_empty_response"
            if diagnostics.get("scene_prompts_response_was_empty_after_timeout")
            else "scene_prompts_timeout"
        )
        if diagnostics.get("scene_prompts_timed_out")
        else (
            "scene_prompts_empty_required_fields"
            if int(diagnostics.get("scene_prompts_empty_count") or 0) > 0
            else error or (error_code.lower() if error_code else "") or ("scene_prompts_empty" if not has_segments else "")
        )
    )
    return {
        "ok": ok,
        "scene_prompts": scene_prompts,
        "error": final_error,
        "error_code": (
            (
                "scene_prompts_timeout_empty_response"
                if diagnostics.get("scene_prompts_response_was_empty_after_timeout")
                else "scene_prompts_timeout"
            )
            if diagnostics.get("scene_prompts_timed_out")
            else error_code
        ),
        "validation_error": validation_error,
        "used_fallback": used_fallback,
        "diagnostics": diagnostics,
    }
