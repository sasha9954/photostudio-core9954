from __future__ import annotations

import json
import hashlib
import re
from typing import Any

from app.engine.gemini_rest import post_generate_content
from app.engine.prompt_polish_policies import (
    build_ia2v_readability_clauses,
    clean_negative_prompt_artifacts,
)
from app.engine.route_baseline_bank import ROUTE_BASELINE_BANK
from app.engine.scenario_stage_timeout_policy import (
    get_scenario_stage_timeout,
    is_timeout_error,
    scenario_timeout_policy_name,
)
from app.engine.scenario_story_guidance import story_guidance_to_notes_list

FINAL_VIDEO_PROMPT_STAGE_VERSION = "gemini_final_video_prompt_v11"
FINAL_VIDEO_PROMPT_MODEL = "gemini-2.5-flash"
FINAL_VIDEO_PROMPT_DELIVERY_VERSION = "1.1"

_ALLOWED_ROUTES = {"i2v", "ia2v", "first_last"}
_ALLOWED_MOTION_STRENGTH = {"low", "medium", "high"}
_ALLOWED_AUGMENTATION = {"low", "medium", "high"}
_ALLOWED_TRANSITION_KIND = {"none", "controlled", "bridge", "morph_guarded"}
_ALLOWED_AUDIO_SYNC = {"none", "beat_sensitive", "phrase_sensitive"}
_ALLOWED_FRAME_STRATEGY = {"single_init", "start_end"}
_HUMAN_ROLES = {"character_1", "character_2", "character_3", "group", "hero", "support", "antagonist"}
_LIP_SYNC_VARIANTS = (
    "close_singing_performance",
    "medium_singing_performance",
    "waist_up_singing_performance",
    "three_quarter_singing_performance",
    "full_body_singing_readable",
    "story_background_singing_performance",
)

GLOBAL_HERO_IDENTITY_LOCK = (
    "GLOBAL HERO IDENTITY LOCK: Keep the same current performer identity in every scene. Preserve face identity, body proportions, hairstyle, clothing silhouette and overall look from the current connected reference."
)
BODY_CONTINUITY_LOCK = (
    "BODY CONTINUITY: Keep the same body type and silhouette from the current reference; avoid body-shape drift."
)
WARDROBE_CONTINUITY_LOCK = (
    "WARDROBE CONTINUITY: Keep outfit continuity from the current reference when present; do not introduce unrelated wardrobe identity drift."
)
CONFIRMED_HERO_LOOK_REFERENCE_CLAUSE = (
    "Use the confirmed hero look reference from scene_01 to preserve the same face, body proportions, silhouette, outfit, neckline, jewelry, hairstyle and production look."
)
IA2V_BASE_PROMPT_V1 = (
    "Use the uploaded image as the exact first frame and identity anchor. "
    "A performance shot of the same performer singing an emotional line. Clear expressive lip sync, natural jaw motion, trembling lips, subtle cheek tension, visible throat effort, soft facial trembling, and small emotional eyebrow movement. "
    "Emotional eyes, controlled breathing, slight head tension, and only very small rhythmic movement. "
    "The face and mouth remain readable and important. Cinematic realism. Steady camera, very slow push-in."
)
IDENTITY_NEGATIVE_GUARD = "different person, different face, changed face, changed body type, changed silhouette, different outfit, hairstyle drift, age drift, body proportion drift"
CONTROLLED_MOTION_SAFETY_BLOCK = (
    "CONTROLLED MOTION SAFETY: smooth readable cinematic motion, grounded body movement, moderate step/sway/turn/weight shift, "
    "stable anatomy-safe motion, no jerky movement, no frantic choreography, no violent spins, no high-frequency shaking."
)
DOMESTIC_WORLD_LOCK_BLOCK = (
    "DOMESTIC WORLD LOCK: grounded domestic realism in the same small late-night apartment kitchen and hallway, "
    "same warm home practical lighting, same domestic interior family, same late-night apartment realism, "
    "tense private home atmosphere, realistic kitchen counter/table, bottle or glass as mundane conflict detail."
)

DOMESTIC_WORLD_NEGATIVE_TERMS = (
    "nightclub, club, bar, dance floor, stage, neon club ambience, crowd, concert lighting, nightlife venue"
)


_FORBIDDEN_VENUE_TERMS = ("nightclub", "night club", "club", "bar", "dance floor", "dancefloor", "stage", "crowd")
_ACTION_CONFLICT_WORDS = (
    "pour",
    "pouring",
    "drink",
    "drinking",
    "throw",
    "throwing",
    "walk",
    "walking",
    "run",
    "running",
    "open door",
    "door handle",
    "pack",
    "packing",
    "grab",
    "grabbing",
    "hold bottle",
    "holding bottle",
    "hands trembling",
    "sink",
    "glass",
    "bag",
    "suitcase",
)
_IA2V_NEGATIVE_KILLER_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mouth-syncing", re.compile(r"\bmouth[- ]syncing\b", re.IGNORECASE)),
    ("mouth syncing", re.compile(r"\bmouth syncing\b", re.IGNORECASE)),
    ("mouth sync", re.compile(r"\bmouth sync\b", re.IGNORECASE)),
    ("lip-syncing", re.compile(r"\blip[- ]syncing\b", re.IGNORECASE)),
    ("lip syncing", re.compile(r"\blip syncing\b", re.IGNORECASE)),
    ("lip sync", re.compile(r"\blip sync\b", re.IGNORECASE)),
    ("singing", re.compile(r"\bsinging\b", re.IGNORECASE)),
    ("singer", re.compile(r"\bsinger\b", re.IGNORECASE)),
    ("vocal performance", re.compile(r"\bvocal performance\b", re.IGNORECASE)),
    ("lip movement", re.compile(r"\blip movement\b", re.IGNORECASE)),
    ("jaw motion", re.compile(r"\bjaw motion\b", re.IGNORECASE)),
    ("closed mouth", re.compile(r"\bclosed mouth\b", re.IGNORECASE)),
    ("silent face", re.compile(r"\bsilent face\b", re.IGNORECASE)),
    ("not singing", re.compile(r"\bnot singing\b", re.IGNORECASE)),
    ("no lip movement", re.compile(r"\bno lip movement\b", re.IGNORECASE)),
    ("no mouth-syncing", re.compile(r"\bno mouth[- ]syncing\b", re.IGNORECASE)),
    ("mouth not synchronized", re.compile(r"\bmouth not synchronized\b", re.IGNORECASE)),
)


def _strip_literal_quoted_dialogue(text: str) -> str:
    raw = str(text or "")
    # normalize explicit quoted lip-sync fragments so alignment intent remains without literal subtitle text
    raw = re.sub(
        r'(?i)mouth\s+moving\s+in\s+sync\s+with\s+the\s+words\s*[\'"]([^\'"]){1,220}[\'"]',
        "mouth moving in sync with the provided audio phrase",
        raw,
    )
    raw = re.sub(
        r'(?i)mouth\s+moving\s+in\s+sync\s+with\s*[\'"]([^\'"]){1,220}[\'"]',
        "mouth moving in sync with the provided audio phrase",
        raw,
    )
    raw = re.sub(
        r'(?i)mouth\s+moving\s+in\s+sync\s+with(?:\s+the\s+phrase)?\s*[\'"]([^\'"]){1,220}[\'"]',
        "mouth moving in sync with the provided audio phrase",
        raw,
    )
    # remove quoted fragments to avoid subtitle rendering; keep semantic prose only
    raw = re.sub(r'["\'][^"\']{2,180}["\']', " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _clean_ia2v_negative_prompt(text: str) -> str:
    parts = [p.strip() for p in str(text or "").split(",") if p.strip()]
    kept: list[str] = []
    for part in parts:
        if any(pattern.search(part) for _, pattern in _IA2V_NEGATIVE_KILLER_TOKEN_PATTERNS):
            continue
        kept.append(part)
    return ", ".join(kept)


def _analyze_ia2v_negative_killer_tokens(text: str) -> tuple[list[str], list[str]]:
    raw_parts = [p.strip() for p in str(text or "").split(",") if p.strip()]
    found_tokens: list[str] = []
    found_segments: list[str] = []
    for token, pattern in _IA2V_NEGATIVE_KILLER_TOKEN_PATTERNS:
        if pattern.search(str(text or "")):
            found_tokens.append(token)
    for segment in raw_parts:
        if any(pattern.search(segment) for _, pattern in _IA2V_NEGATIVE_KILLER_TOKEN_PATTERNS):
            found_segments.append(segment)
    dedup_tokens = list(dict.fromkeys(found_tokens))
    dedup_segments = list(dict.fromkeys(found_segments))
    return dedup_tokens, dedup_segments


def _scene_specific_char_count(text: str) -> int:
    cleaned = _strip_positive_contract_blocks(str(text or ""))
    cleaned = re.sub(r"(?i)\b(LIP-SYNC PERFORMANCE RULES STRICT|LIP-SYNC EXPRESSIVITY LOW ENERGY|CLEAR VOCAL PERFORMANCE|OUTFIT ANCHOR|OUTFIT NEGATIVES|CONTROLLED MOTION SAFETY|DOMESTIC WORLD LOCK)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(cleaned)


def _remove_forbidden_venue_terms(positive: str, negative: str, *, apply_guard: bool) -> tuple[str, str, bool]:
    if not apply_guard:
        return positive, negative, False
    rewritten = str(positive or "")
    removed = False
    for term in _FORBIDDEN_VENUE_TERMS:
        before = rewritten
        rewritten = re.sub(rf"(?i)\b{re.escape(term)}\b", " ", rewritten)
        if rewritten != before:
            removed = True
    rewritten = re.sub(r"\s+", " ", rewritten).strip(" ,.;")
    neg = str(negative or "")
    if removed:
        neg = _append_clause(neg, ", ".join(["no nightclub", "no club", "no bar", "no dance floor", "no stage", "no crowd"]))
    return rewritten, neg, removed


_CONFIRMED_HERO_URL_KEYS = (
    "confirmed_hero_image_url",
    "confirmedHeroImageUrl",
    "confirmed_look_image_url",
    "confirmedLookImageUrl",
    "hero_reference_url",
    "heroReferenceUrl",
)
_STALE_IDENTITY_TERMS = (
    "same woman",
    "woman",
    "female",
    "girl",
    "lady",
    "light linen dress",
    "beige cropped sleeveless top",
    "cropped top",
    "open neckline",
    "crop length",
    "bust/hips",
    "bust",
    "hips",
)


def _character_1_context(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    connected = _safe_dict(input_pkg.get("connected_context_summary")) or _safe_dict(package.get("connected_context_summary"))
    role_map = _safe_dict(connected.get("role_identity_mapping"))
    char1 = _safe_dict(role_map.get("character_1"))
    refs_present = _safe_list(_safe_dict(connected.get("refsPresentByRole")).get("character_1"))
    connected_refs = _safe_list(_safe_dict(connected.get("connectedRefsPresentByRole")).get("character_1"))
    ref_character_1_inventory = _safe_dict(_safe_dict(package.get("refs_inventory")).get("ref_character_1"))
    inventory_refs = _safe_list(ref_character_1_inventory.get("refs"))
    inventory_value = str(ref_character_1_inventory.get("value") or "").strip()
    all_refs = [
        str(v).strip()
        for v in [*refs_present, *connected_refs, *inventory_refs, inventory_value]
        if str(v).strip()
    ]
    all_refs = list(dict.fromkeys(all_refs))
    ref_signature = hashlib.sha256("|".join(sorted(all_refs)).encode("utf-8")).hexdigest() if all_refs else ""
    gender_hint = str(char1.get("gender_hint") or "").strip().lower()
    identity_label = str(char1.get("identity_label") or "").strip()
    appearance = str(char1.get("appearanceMode") or char1.get("appearance_mode") or "").strip().lower()
    presence = str(char1.get("screenPresenceMode") or char1.get("screen_presence_mode") or "").strip().lower()
    return {
        "gender_hint": gender_hint,
        "identity_label": identity_label,
        "ref_count": len(all_refs),
        "ref_signature": ref_signature,
        "lip_sync_only": appearance == "lip_sync_only" or presence == "lip_sync_only",
    }


def _resolve_segment_route(row: dict[str, Any], fallback_row: dict[str, Any]) -> str:
    video_metadata = _safe_dict(row.get("video_metadata"))
    route_payload = _safe_dict(row.get("route_payload"))
    prompt_row = _safe_dict(fallback_row.get("prompt_row"))
    plan_row = _safe_dict(fallback_row.get("plan_row"))
    route = _normalize_route(
        video_metadata.get("route_type")
        or row.get("route")
        or route_payload.get("route")
        or fallback_row.get("route")
        or prompt_row.get("route")
        or plan_row.get("route")
    )
    if route not in _ALLOWED_ROUTES:
        raise RuntimeError("FINAL_VIDEO_PROMPT_ROUTE_MISSING")
    return route


def _has_url_token(value: Any) -> bool:
    if isinstance(value, str):
        token = value.strip().lower()
        return token.startswith(("http://", "https://"))
    if isinstance(value, dict):
        return any(_has_url_token(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_url_token(v) for v in value)
    return False


def _has_real_confirmed_hero_image_url(fallback_row: dict[str, Any]) -> bool:
    prompt_row = _safe_dict(fallback_row.get("prompt_row"))
    plan_row = _safe_dict(fallback_row.get("plan_row"))
    role_row = _safe_dict(fallback_row.get("role_row"))
    for src in (fallback_row, prompt_row, plan_row, role_row):
        for key in _CONFIRMED_HERO_URL_KEYS:
            if _has_url_token(_safe_dict(src).get(key)):
                return True
    for src in (prompt_row, plan_row):
        for key in ("source_image_refs", "image_refs"):
            refs = _safe_list(_safe_dict(src).get(key))
            if any(_has_url_token(v) for v in refs):
                return True
    return False


def _strip_clear_vocal_fragments(text: str) -> str:
    text = str(text or "")
    canonical = re.escape(IA2V_BASE_PROMPT_V1.strip())
    text = re.sub(canonical, " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\bCLEAR\s+VOCAL\s+PERFORMANCE\s*[:.]?", " ", text)
    text = re.sub(r"(?i)\bIA2V\s+BASE\s+PROMPT\s*[:.]?", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[\s:.,;]+", "", text).strip()
    return text


def _strip_positive_contract_blocks(text: str) -> str:
    text = str(text or "")
    text = re.sub(
        r"(?i)\bGLOBAL HERO IDENTITY LOCK,\s*BODY CONTINUITY,\s*WARDROBE CONTINUITY\.?",
        " ",
        text,
    )
    labels = [
        "GLOBAL HERO IDENTITY LOCK:",
        "BODY CONTINUITY:",
        "WARDROBE CONTINUITY:",
    ]
    for label in labels:
        pattern = rf"(?is)\b{re.escape(label)}\s*.*?(?=(GLOBAL HERO IDENTITY LOCK:|BODY CONTINUITY:|WARDROBE CONTINUITY:|Use the confirmed hero look reference|Performer remains|Shot variant:|$))"
        text = re.sub(pattern, " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_negative_positive_contract_blocks(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"(?i)\bGLOBAL HERO IDENTITY CONTRACT\.?", " ", text)
    labels = [
        "GLOBAL HERO IDENTITY LOCK:",
        "BODY CONTINUITY:",
        "WARDROBE CONTINUITY:",
    ]
    for label in labels:
        pattern = rf"(?is)\b{re.escape(label)}\s*.*?(?=(GLOBAL HERO IDENTITY LOCK:|BODY CONTINUITY:|WARDROBE CONTINUITY:|different woman|changed face|changed body|slimmer body|$))"
        text = re.sub(pattern, " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.;")
    return text


def _sanitize_prompt_text_for_current_identity(
    text: Any,
    identity_ctx: dict[str, Any],
    route: str,
    field_name: str,
) -> tuple[str, dict[str, Any]]:
    cleaned = str(text or "").strip()
    removed_terms: list[str] = []
    stale_identity_removed = 0
    stale_wardrobe_removed = 0
    if str(identity_ctx.get("gender_hint") or "").strip().lower() == "male":
        for term in _STALE_IDENTITY_TERMS:
            before = cleaned
            cleaned = re.sub(rf"(?i)\b{re.escape(term)}\b", " ", cleaned)
            if cleaned != before:
                removed_terms.append(term)
                stale_identity_removed += 1
                if term in {"light linen dress", "beige cropped sleeveless top", "cropped top", "open neckline", "crop length", "bust/hips", "bust", "hips"}:
                    stale_wardrobe_removed += 1
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;")
    return cleaned, {
        "field_name": field_name,
        "route": route,
        "identity_gender_conflict_detected": bool(removed_terms),
        "identity_gender_conflict_terms_removed": list(dict.fromkeys(removed_terms)),
        "stale_identity_clause_removed_count": stale_identity_removed,
        "stale_wardrobe_clause_removed_count": stale_wardrobe_removed,
    }


def _contains_action_conflict_words(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(word in lowered for word in _ACTION_CONFLICT_WORDS)


def _strip_ia2v_positive_noise(text: str) -> str:
    cleaned = _strip_positive_contract_blocks(str(text or ""))
    cleaned = re.sub(r"(?is)\bOUTFIT ANCHOR\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?is)\bOUTFIT NEGATIVES?\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bdo not raise neckline\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bdo not close chest coverage\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bno simultaneous dual-speaker lip movement\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bno broad gestures\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bno hand choreography\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bno foreground action event\b[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bdo not\s+[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bno\s+[^.]*\.?", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;")
    return cleaned


def _sanitize_contract_prompts(*, positive_prompt: str, negative_prompt: str, route: str) -> tuple[str, str, dict[str, bool]]:
    positive = str(positive_prompt or "").strip()
    negative = str(negative_prompt or "").strip()
    clear_vocal_fragments_removed = False
    clear_vocal_canonical_applied = False
    negative_positive_contract_blocks_removed = False

    negative_before = negative
    negative = _strip_negative_positive_contract_blocks(negative)
    negative_positive_contract_blocks_removed = negative != negative_before

    if route == "ia2v":
        body = _strip_clear_vocal_fragments(positive)
        clear_vocal_fragments_removed = body != positive
        body = _strip_ia2v_positive_noise(body)
        positive = f"{IA2V_BASE_PROMPT_V1} {body}".strip()
        clear_vocal_canonical_applied = True

    positive = re.sub(r"\s+", " ", positive).strip()
    negative = re.sub(r"\s+", " ", negative).strip(" ,")
    return positive, negative, {
        "clearVocalCanonicalApplied": clear_vocal_canonical_applied,
        "clearVocalFragmentsRemoved": clear_vocal_fragments_removed,
        "negativePositiveContractBlocksRemoved": negative_positive_contract_blocks_removed,
    }


def _rewire_shadow_continuity(previous_seg: dict[str, Any], current_seg: dict[str, Any]) -> None:
    prev_end = str(previous_seg.get("ends_with_state") or "").lower()
    if not any(token in prev_end for token in ("deeper shadows", "shadow pocket", "corridor exit", "reflective darkness")):
        return

    route_payload = _safe_dict(current_seg.get("route_payload"))
    starts_logic = str(current_seg.get("starts_from_previous_logic") or "")
    bridge_start = "Continues from the prior move into deeper shadows, beginning in a shadow pocket near the corridor exit."
    if "bar threshold" in starts_logic.lower():
        starts_logic = re.sub(r"(?i)bar threshold", "shadow pocket near corridor exit", starts_logic)
    current_seg["starts_from_previous_logic"] = _append_clause(starts_logic, bridge_start)

    positive_prompt = str(route_payload.get("positive_prompt") or "")
    if "bar threshold" in positive_prompt.lower():
        route_payload["positive_prompt"] = re.sub(r"(?i)bar threshold", "shadow pocket near corridor exit", positive_prompt)

    first_frame = str(route_payload.get("first_frame_prompt") or "")
    if first_frame:
        if "bar threshold" in first_frame.lower():
            first_frame = re.sub(r"(?i)bar threshold", "shadow pocket near corridor exit", first_frame)
        route_payload["first_frame_prompt"] = _append_clause(first_frame, bridge_start)

    current_seg["route_payload"] = route_payload


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _append_clause(base: str, clause: str) -> str:
    text = str(base or "").strip()
    part = str(clause or "").strip()
    if not part:
        return text
    if part.lower() in text.lower():
        return text
    if not text:
        return part
    return f"{text.rstrip('. ')}. {part}"


def _scene_has_human_subject(fallback_row: dict[str, Any], route: str) -> bool:
    role_row = _safe_dict(fallback_row.get("role_row"))
    prompt_row = _safe_dict(fallback_row.get("prompt_row"))
    primary_role = str(role_row.get("primary_role") or fallback_row.get("primary_role") or "").strip().lower()
    active_roles = {str(v).strip().lower() for v in _safe_list(role_row.get("active_roles")) if str(v).strip()}
    if primary_role in _HUMAN_ROLES:
        return True
    if active_roles.intersection(_HUMAN_ROLES):
        return True
    if route == "ia2v":
        return True
    for value in (
        prompt_row.get("photo_prompt"),
        prompt_row.get("video_prompt"),
        fallback_row.get("scene_id"),
    ):
        blob = str(value or "").lower()
        if any(token in blob for token in ("woman", "girl", "singer", "performer", "heroine", "character")):
            return True
    return False


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
                return {}
    return {}


def _normalize_route(route_value: Any) -> str:
    route = str(route_value or "").strip().lower()
    if route in {"f_l", "first-last"}:
        route = "first_last"
    if route in {"lip_sync", "lip_sync_music"}:
        route = "ia2v"
    if route not in _ALLOWED_ROUTES:
        route = "i2v"
    return route


def _engine_hints_defaults(route: str) -> dict[str, Any]:
    if route == "ia2v":
        return {
            "motion_strength": "medium",
            "augmentation_level": "low",
            "transition_kind": "controlled",
            "audio_sync_mode": "phrase_sensitive",
            "frame_strategy": "single_init",
        }
    if route == "first_last":
        return {
            "motion_strength": "medium",
            "augmentation_level": "medium",
            "transition_kind": "controlled",
            "audio_sync_mode": "none",
            "frame_strategy": "start_end",
        }
    return {
        "motion_strength": "low",
        "augmentation_level": "low",
        "transition_kind": "none",
        "audio_sync_mode": "none",
        "frame_strategy": "single_init",
    }


def _video_metadata_defaults(route: str) -> dict[str, Any]:
    if route == "first_last":
        return {
            "renderer_family": "ltx",
            "route_type": "first_last",
            "requires_first_frame": True,
            "requires_last_frame": True,
        }
    if route == "ia2v":
        return {
            "renderer_family": "ltx",
            "route_type": "ia2v",
            "requires_first_frame": True,
            "requires_last_frame": False,
        }
    return {
        "renderer_family": "ltx",
        "route_type": "i2v",
        "requires_first_frame": True,
        "requires_last_frame": False,
    }


def _canonical_segments(package: dict[str, Any]) -> list[dict[str, Any]]:
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    role_plan = _safe_dict(package.get("role_plan"))

    prompts_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(scene_prompts.get("segments"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    if not prompts_by_id:
        prompts_by_id = {
            str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(scene_prompts.get("scenes"))
            if str(_safe_dict(row).get("scene_id") or "").strip()
        }

    plan_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(scene_plan.get("segments"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    if not plan_by_id:
        plan_by_id = {
            str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(scene_plan.get("scenes"))
            if str(_safe_dict(row).get("scene_id") or "").strip()
        }

    role_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(role_plan.get("scene_casting"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }

    ordered_ids: list[str] = []
    for source in (plan_by_id, prompts_by_id, role_by_id):
        for segment_id in source.keys():
            if segment_id and segment_id not in ordered_ids:
                ordered_ids.append(segment_id)

    rows: list[dict[str, Any]] = []
    for idx, segment_id in enumerate(ordered_ids, start=1):
        prompt_row = _safe_dict(prompts_by_id.get(segment_id))
        plan_row = _safe_dict(plan_by_id.get(segment_id))
        role_row = _safe_dict(role_by_id.get(segment_id))
        route = _normalize_route(prompt_row.get("route") or plan_row.get("route"))
        rows.append(
            {
                "segment_id": segment_id,
                "scene_id": str(prompt_row.get("scene_id") or plan_row.get("scene_id") or segment_id).strip(),
                "sequence_index": idx,
                "route": route,
                "prompt_row": prompt_row,
                "plan_row": plan_row,
                "role_row": role_row,
            }
        )
    return rows


def _build_model_payload(package: dict[str, Any], segment_rows: list[dict[str, Any]]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    audio_map = _safe_dict(package.get("audio_map"))
    story_core = _safe_dict(package.get("story_core"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    continuity_notes = story_guidance_to_notes_list(story_core.get("story_guidance"), max_items=8)
    if not continuity_notes:
        continuity_notes = _safe_list(role_plan.get("continuity_notes"))[:8]

    segments_payload: list[dict[str, Any]] = []
    for row in segment_rows:
        prompt_row = _safe_dict(row.get("prompt_row"))
        plan_row = _safe_dict(row.get("plan_row"))
        segments_payload.append(
            {
                "segment_id": row.get("segment_id"),
                "scene_id": row.get("scene_id"),
                "route": row.get("route"),
                "duration_sec": plan_row.get("duration_sec"),
                "t0": plan_row.get("t0"),
                "t1": plan_row.get("t1"),
                "scene_goal": str(plan_row.get("scene_goal") or "").strip(),
                "scene_summary": str(plan_row.get("scene_summary") or plan_row.get("scene_description") or "").strip(),
                "primary_role": str(_safe_dict(row.get("role_row")).get("primary_role") or plan_row.get("primary_role") or "").strip(),
                "active_roles": _safe_list(_safe_dict(row.get("role_row")).get("active_roles")),
                "photo_prompt": str(prompt_row.get("photo_prompt") or "").strip(),
                "video_prompt": str(prompt_row.get("video_prompt") or "").strip(),
                "negative_prompt": str(prompt_row.get("negative_prompt") or "").strip(),
                "positive_video_prompt": str(prompt_row.get("positive_video_prompt") or "").strip(),
                "negative_video_prompt": str(prompt_row.get("negative_video_prompt") or "").strip(),
                "first_frame_prompt": str(prompt_row.get("first_frame_prompt") or prompt_row.get("start_image_prompt") or "").strip(),
                "last_frame_prompt": str(prompt_row.get("last_frame_prompt") or prompt_row.get("end_image_prompt") or "").strip(),
            }
        )

    return {
        "target_contract": {
            "delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION,
            "segments": [
                {
                    "segment_id": "string",
                    "scene_id": "string",
                    "route_payload": {
                        "positive_prompt": "string",
                        "negative_prompt": "string",
                        "first_frame_prompt": "string|null",
                        "last_frame_prompt": "string|null",
                    },
                    "engine_hints": {
                        "motion_strength": "low|medium|high",
                        "augmentation_level": "low|medium|high",
                        "transition_kind": "none|controlled|bridge|morph_guarded",
                        "audio_sync_mode": "none|beat_sensitive|phrase_sensitive",
                        "frame_strategy": "single_init|start_end",
                    },
                    "video_metadata": {
                        "renderer_family": "ltx|generic",
                        "route_type": "i2v|ia2v|first_last",
                        "requires_first_frame": True,
                        "requires_last_frame": False,
                    },
                    "audio_behavior_hints": "string",
                    "lip_sync_shot_variant": "string|null",
                    "performance_pose": "string|null",
                    "camera_angle": "string|null",
                    "gesture": "string|null",
                    "location_zone": "string|null",
                    "mouth_readability": "high|medium|low|null",
                    "why_this_lip_sync_shot_is_different": "string|null",
                    "starts_from_previous_logic": "string|null",
                    "ends_with_state": "string|null",
                    "continuity_with_next": "string|null",
                    "potential_contradiction": "string|null",
                    "fix_if_needed": "string|null",
                    "identity_lock_applied": True,
                    "body_lock_applied": True,
                    "wardrobe_lock_applied": True,
                    "confirmedHeroLookReferenceUsed": False,
                    "confirmedHeroLookReferenceClauseApplied": False,
                    "lip_sync_shot_variant_repeated_with_previous": False,
                    "continuity_warning": "string|null",
                    "continuity_fix_applied": False,
                    "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
                }
            ],
        },
        "reference_context": {
            "route_baseline_bank": ROUTE_BASELINE_BANK,
            "upstream": {
                "input": {
                    "content_type": str(input_pkg.get("content_type") or ""),
                    "director_mode": str(input_pkg.get("director_mode") or ""),
                    "format": str(input_pkg.get("format") or ""),
                },
                "audio_map": {
                    "duration_sec": audio_map.get("duration_sec"),
                    "sections": _safe_list(audio_map.get("sections"))[:12],
                },
                "story_core": {
                    "story_summary": str(story_core.get("story_summary") or "").strip(),
                    "director_summary": str(story_core.get("director_summary") or "").strip(),
                    "world_lock": _safe_dict(story_core.get("world_lock")),
                    "identity_lock": _safe_dict(story_core.get("identity_lock")),
                    "style_lock": _safe_dict(story_core.get("style_lock")),
                    "continuity_notes": continuity_notes,
                },
                "scene_plan": {
                    "route_mix_summary": _safe_dict(scene_plan.get("route_mix_summary")),
                    "scene_arc_summary": str(scene_plan.get("scene_arc_summary") or "").strip(),
                },
                "scene_prompts": {
                    "prompts_version": str(scene_prompts.get("prompts_version") or ""),
                    "global_style_anchor": str(scene_prompts.get("global_style_anchor") or "").strip(),
                },
            },
        },
        "segments": segments_payload,
    }


def _build_instruction(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "You are the only creative author for FINAL VIDEO PROMPT.",
            "Output strict JSON only.",
            "Do not add wrappers, markdown, or explanations.",
            "Do not invent extra segments and do not drop any provided segment_id.",
            "Use route semantics exactly: i2v, ia2v, first_last.",
            "GLOBAL HERO IDENTITY CONTRACT for non-ia2v human scenes must be enforced; keep lock clauses in positive and only real negative tokens in negative.",
            "For ia2v, use IA2V_BASE_PROMPT_V1 performer-first canon and avoid wardrobe/body continuity walls in positive prompt.",
            "ALLOWED VARIATION for same hero: vary only pose, camera angle, shot size, location zone, gesture, emotion, movement and lighting accent.",
            "For non-ia2v human scenes with confirmed hero reference, add confirmed look anchor clause. For ia2v, rely on uploaded image first-frame identity anchor and do not add wardrobe/body/outfit continuity walls into positive prompt.",
            "Do not replace original character references; confirmed look anchor is additional reinforcement only.",
            "WHOLE-STORY CONTINUITY: review all segments as one continuous clip and prevent action/state contradictions between adjacent segments.",
            "For each segment output starts_from_previous_logic, ends_with_state, continuity_with_next, potential_contradiction, fix_if_needed.",
            "If contradiction exists, repair the later segment before returning final JSON.",
            "For ia2v/lip-sync route, positive prompt MUST start with IA2V_BASE_PROMPT_V1 performer-first block.",
            "For ia2v/lip-sync route, output lip_sync_shot_variant, performance_pose, camera_angle, gesture, location_zone, mouth_readability, why_this_lip_sync_shot_is_different.",
            f"For ia2v/lip-sync route, lip_sync_shot_variant must be one of: {', '.join(_LIP_SYNC_VARIANTS)}.",
            "For adjacent ia2v scenes, do not repeat the same lip_sync_shot_variant.",
            "Use Route Baseline Bank only as reference.",
            "Do not mirror baseline text verbatim unless context requires it.",
            f"Set prompt_source exactly to '{FINAL_VIDEO_PROMPT_STAGE_VERSION}' for each segment.",
            "If a route is first_last, first_frame_prompt and last_frame_prompt must be meaningful non-empty strings.",
            "If route is i2v or ia2v, first_frame_prompt may be null and last_frame_prompt should be null.",
            "Return exactly one object with keys: delivery_version, segments.",
            json.dumps(payload, ensure_ascii=False),
        ]
    )


def _sanitize_segment(raw_row: Any, fallback_row: dict[str, Any], identity_ctx: dict[str, Any]) -> dict[str, Any]:
    row = _safe_dict(raw_row)
    segment_id = str(row.get("segment_id") or fallback_row.get("segment_id") or "").strip()
    scene_id = str(row.get("scene_id") or fallback_row.get("scene_id") or segment_id).strip()
    route = _resolve_segment_route(row, fallback_row)

    route_payload = _safe_dict(row.get("route_payload"))
    fallback_prompt_row = _safe_dict(fallback_row.get("prompt_row"))
    positive_prompt = str(route_payload.get("positive_prompt") or fallback_prompt_row.get("positive_video_prompt") or fallback_prompt_row.get("video_prompt") or "").strip()
    negative_prompt = str(route_payload.get("negative_prompt") or fallback_prompt_row.get("negative_video_prompt") or fallback_prompt_row.get("negative_prompt") or "").strip()
    fallback_photo_prompt = _strip_literal_quoted_dialogue(str(fallback_prompt_row.get("photo_prompt") or "").strip())
    fallback_video_prompt = _strip_literal_quoted_dialogue(str(fallback_prompt_row.get("video_prompt") or "").strip())
    scene_seq_index = int(fallback_row.get("sequence_index") or 0)
    lip_sync_only_i2v = bool(identity_ctx.get("lip_sync_only")) and route == "i2v"
    has_human_subject = False if lip_sync_only_i2v else _scene_has_human_subject(fallback_row, route)
    confirmed_look_used = bool(has_human_subject and scene_seq_index >= 2 and _has_real_confirmed_hero_image_url(fallback_row))
    confirmed_look_clause_applied = bool(confirmed_look_used)
    positive_contract_duplicates_removed = False
    positive_prompt_seed = positive_prompt
    if has_human_subject and route != "ia2v":
        positive_before_cleanup = positive_prompt
        positive_prompt = _strip_positive_contract_blocks(positive_prompt)
        positive_contract_duplicates_removed = positive_prompt != positive_before_cleanup
        positive_prompt = _append_clause(positive_prompt, GLOBAL_HERO_IDENTITY_LOCK)
        positive_prompt = _append_clause(positive_prompt, BODY_CONTINUITY_LOCK)
        positive_prompt = _append_clause(positive_prompt, WARDROBE_CONTINUITY_LOCK)
        if confirmed_look_used:
            positive_prompt = _append_clause(positive_prompt, CONFIRMED_HERO_LOOK_REFERENCE_CLAUSE)
        negative_prompt = _append_clause(negative_prompt, IDENTITY_NEGATIVE_GUARD)

    plan_row = _safe_dict(fallback_row.get("plan_row"))
    scene_specific_parts = [
        fallback_photo_prompt,
        fallback_video_prompt,
        str(fallback_prompt_row.get("world_anchor") or fallback_prompt_row.get("worldAnchor") or "").strip(),
        str(fallback_prompt_row.get("action_emotion") or fallback_prompt_row.get("actionEmotion") or "").strip(),
        str(plan_row.get("scene_goal") or "").strip(),
        str(plan_row.get("scene_summary") or plan_row.get("scene_description") or "").strip(),
        str(plan_row.get("emotional_intent") or "").strip(),
    ]
    scene_specific_payload = ". ".join(part for part in scene_specific_parts if part).strip()
    if route == "ia2v":
        ia2v_scene_specific_parts = [
            str(plan_row.get("emotional_intent") or "").strip(),
            str(plan_row.get("narrative_function") or "").strip(),
            str(plan_row.get("scene_goal") or "").strip(),
        ]
        ia2v_scene_specific_parts = [
            part for part in ia2v_scene_specific_parts if part and not _contains_action_conflict_words(part)
        ]
        scene_specific_payload = ". ".join(ia2v_scene_specific_parts).strip()
        if scene_specific_payload:
            positive_prompt = ". ".join(part for part in [scene_specific_payload, positive_prompt] if part).strip(". ")
        elif positive_prompt_seed:
            positive_prompt = positive_prompt_seed
    elif scene_specific_payload:
        positive_prompt = ". ".join(part for part in [scene_specific_payload, positive_prompt] if part).strip(". ")
    elif positive_prompt_seed:
        positive_prompt = positive_prompt_seed

    scene_specific_chars_after_bootstrap = _scene_specific_char_count(positive_prompt)
    final_prompt_scene_specific_missing = scene_specific_chars_after_bootstrap < 80
    final_prompt_rebuilt_from_scene_prompts = False
    if route != "ia2v" and final_prompt_scene_specific_missing and scene_specific_payload:
        rebuild_parts = [
            fallback_photo_prompt,
            fallback_video_prompt,
            str(fallback_prompt_row.get("world_anchor") or fallback_prompt_row.get("worldAnchor") or "").strip(),
            str(_safe_dict(fallback_row.get("plan_row")).get("emotional_intent") or "").strip(),
            positive_prompt,
        ]
        positive_prompt = ". ".join(part for part in rebuild_parts if part).strip(". ")
        final_prompt_rebuilt_from_scene_prompts = True
        final_prompt_scene_specific_missing = _scene_specific_char_count(positive_prompt) < 80
    if lip_sync_only_i2v:
        env_still_focus = str(plan_row.get("scene_goal") or fallback_prompt_row.get("background_story_evidence") or fallback_photo_prompt or "grounded world continuity").strip()
        env_motion_focus = str(fallback_prompt_row.get("ltx_video_goal") or plan_row.get("scene_goal") or fallback_video_prompt or "grounded world continuity").strip()
        positive_prompt = (
            f"Environment-focused motion shot: {env_motion_focus}. "
            "Subtle atmosphere/city/people/world motion only. No main performer visible. No lip-sync. Singer remains voiceover only."
        )
        fallback_photo_prompt = (
            f"Environment-focused still frame: {env_still_focus}. "
            "Grounded realistic world, no main performer visible, singer remains voiceover only."
        )

    lower_scene_semantics = " ".join(scene_specific_parts + [positive_prompt]).lower()
    domestic_scene = any(
        token in lower_scene_semantics
        for token in ("domestic", "apartment", "kitchen", "home interior", "argument", "breakup", "hallway", "late-night")
    )
    has_character_1 = "character_1" in json.dumps(fallback_row, ensure_ascii=False).lower()

    if route == "ia2v":
        lip_sync_shot_variant = str(
            row.get("lip_sync_shot_variant")
            or fallback_prompt_row.get("lip_sync_shot_variant")
            or _LIP_SYNC_VARIANTS[(scene_seq_index - 1) % len(_LIP_SYNC_VARIANTS)]
        ).strip()
        if lip_sync_shot_variant not in _LIP_SYNC_VARIANTS:
            lip_sync_shot_variant = _LIP_SYNC_VARIANTS[(scene_seq_index - 1) % len(_LIP_SYNC_VARIANTS)]
        performance_pose = str(row.get("performance_pose") or fallback_prompt_row.get("performance_pose") or "").strip()
        camera_angle = str(row.get("camera_angle") or fallback_prompt_row.get("camera_angle") or "").strip()
        gesture = str(row.get("gesture") or fallback_prompt_row.get("gesture") or "").strip()
        location_zone = str(row.get("location_zone") or fallback_prompt_row.get("location_zone") or "").strip()
        mouth_readability = str(row.get("mouth_readability") or fallback_prompt_row.get("mouth_readability") or "high").strip().lower() or "high"
        role_row = _safe_dict(fallback_row.get("role_row"))
        semantic_context = " ".join(
            [
                str(plan_row.get("narrative_function") or plan_row.get("scene_function") or ""),
                str(plan_row.get("scene_goal") or ""),
                str(plan_row.get("emotional_intent") or ""),
                str(plan_row.get("subject_priority") or ""),
                str(plan_row.get("framing") or ""),
                str(role_row.get("primary_role") or ""),
            ]
        )
        clauses = build_ia2v_readability_clauses(existing_text=positive_prompt, semantic_context=semantic_context)
        for clause in clauses:
            if clause.lower() in positive_prompt.lower():
                continue
            positive_prompt = f"{positive_prompt.rstrip('. ')}. {clause}".strip() if positive_prompt else clause
        positive_prompt = _append_clause(
            positive_prompt,
            f"Shot variant: {lip_sync_shot_variant}. performance_pose: {performance_pose or 'camera-readable vocal delivery'}. camera_angle: {camera_angle or 'eye-level readable performance view'}. gesture: {gesture or 'minimal performance motion'}. location_zone: {location_zone or 'same venue, different local zone'}. mouth_readability: {mouth_readability}.",
        )
        if not positive_prompt.startswith("Use the uploaded image as the exact first frame and identity anchor."):
            positive_prompt = f"{IA2V_BASE_PROMPT_V1} {positive_prompt}".strip()
    else:
        lip_sync_shot_variant = ""
        performance_pose = ""
        camera_angle = ""
        gesture = ""
        location_zone = ""
        mouth_readability = ""
    route_behavior_template = ""
    route_template_source = "route_default_template"
    if route == "i2v":
        route_behavior_template = CONTROLLED_MOTION_SAFETY_BLOCK
        positive_prompt = _append_clause(positive_prompt, route_behavior_template)
        if domestic_scene:
            route_template_source = "i2v_domestic_safety_template"
            positive_prompt = _append_clause(positive_prompt, DOMESTIC_WORLD_LOCK_BLOCK)
    elif route == "ia2v":
        route_behavior_template = "Performer-first singing mechanics only; minimal movement and steady camera."
        route_template_source = "ia2v_lipsync_template"
        if positive_prompt:
            positive_prompt = f"{positive_prompt.rstrip('. ')}. {route_behavior_template}"
    positive_prompt, negative_prompt, contract_debug = _sanitize_contract_prompts(
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        route=route,
    )
    if route == "ia2v":
        body = _strip_clear_vocal_fragments(positive_prompt)
        body = _strip_ia2v_positive_noise(body)
        positive_prompt = f"{IA2V_BASE_PROMPT_V1} {body}".strip()
        positive_prompt = re.sub(r"\s+", " ", positive_prompt).strip(" ,.;")

    # apply literal dialogue cleanup after all append/rebuild steps and before venue-term guard.
    positive_prompt = _strip_literal_quoted_dialogue(positive_prompt)

    positive_prompt, negative_prompt, final_prompt_forbidden_venue_terms_removed = _remove_forbidden_venue_terms(
        positive_prompt,
        negative_prompt,
        apply_guard=domestic_scene,
    )
    if domestic_scene:
        negative_prompt = _append_clause(negative_prompt, DOMESTIC_WORLD_NEGATIVE_TERMS)

    if route == "ia2v":
        negative_prompt = _clean_ia2v_negative_prompt(negative_prompt)
    negative_prompt = clean_negative_prompt_artifacts(negative_prompt)

    positive_prompt, positive_diag = _sanitize_prompt_text_for_current_identity(
        positive_prompt, identity_ctx, route, "route_payload.positive_prompt"
    )
    negative_prompt, negative_diag = _sanitize_prompt_text_for_current_identity(
        negative_prompt, identity_ctx, route, "route_payload.negative_prompt"
    )

    first_frame_raw = route_payload.get("first_frame_prompt")
    last_frame_raw = route_payload.get("last_frame_prompt")
    first_frame = str(first_frame_raw).strip() if first_frame_raw is not None else ""
    last_frame = str(last_frame_raw).strip() if last_frame_raw is not None else ""
    if route == "first_last":
        if not first_frame:
            first_frame = str(fallback_prompt_row.get("first_frame_prompt") or fallback_prompt_row.get("start_image_prompt") or "").strip()
        if not last_frame:
            last_frame = str(fallback_prompt_row.get("last_frame_prompt") or fallback_prompt_row.get("end_image_prompt") or "").strip()
        if has_human_subject:
            first_frame = _strip_positive_contract_blocks(first_frame)
            last_frame = _strip_positive_contract_blocks(last_frame)
            for lock_clause in (GLOBAL_HERO_IDENTITY_LOCK, BODY_CONTINUITY_LOCK, WARDROBE_CONTINUITY_LOCK):
                first_frame = _append_clause(first_frame, lock_clause)
                last_frame = _append_clause(last_frame, lock_clause)
            if confirmed_look_clause_applied:
                first_frame = _append_clause(first_frame, CONFIRMED_HERO_LOOK_REFERENCE_CLAUSE)
                last_frame = _append_clause(last_frame, CONFIRMED_HERO_LOOK_REFERENCE_CLAUSE)
    first_frame, first_frame_diag = _sanitize_prompt_text_for_current_identity(
        first_frame, identity_ctx, route, "route_payload.first_frame_prompt"
    )
    last_frame, last_frame_diag = _sanitize_prompt_text_for_current_identity(
        last_frame, identity_ctx, route, "route_payload.last_frame_prompt"
    )

    first_frame_has_identity_lock = "GLOBAL HERO IDENTITY LOCK:" in first_frame if first_frame else False
    last_frame_has_identity_lock = "GLOBAL HERO IDENTITY LOCK:" in last_frame if last_frame else False
    negative_contains_positive_identity_block = bool(
        re.search(r"(?i)\b(GLOBAL HERO IDENTITY LOCK:|BODY CONTINUITY:|WARDROBE CONTINUITY:|GLOBAL HERO IDENTITY CONTRACT\.?)", negative_prompt)
    )

    if not segment_id or not positive_prompt or not negative_prompt:
        raise RuntimeError(f"final_video_prompt_invalid_segment:{segment_id or 'unknown'}")
    if route == "first_last" and (not first_frame or not last_frame):
        raise RuntimeError("FINAL_VIDEO_PROMPT_FIRST_LAST_INCOMPLETE")
    if route == "ia2v" and not positive_prompt:
        raise RuntimeError("FINAL_VIDEO_PROMPT_IA2V_INCOMPLETE")

    engine_hints = _safe_dict(row.get("engine_hints"))
    engine_defaults = _engine_hints_defaults(route)
    motion_strength = str(engine_hints.get("motion_strength") or engine_defaults["motion_strength"]).strip().lower()
    augmentation_level = str(engine_hints.get("augmentation_level") or engine_defaults["augmentation_level"]).strip().lower()
    transition_kind = str(engine_hints.get("transition_kind") or engine_defaults["transition_kind"]).strip().lower()
    audio_sync_mode = str(engine_hints.get("audio_sync_mode") or engine_defaults["audio_sync_mode"]).strip().lower()
    frame_strategy = str(engine_hints.get("frame_strategy") or engine_defaults["frame_strategy"]).strip().lower()

    if motion_strength not in _ALLOWED_MOTION_STRENGTH:
        motion_strength = engine_defaults["motion_strength"]
    if augmentation_level not in _ALLOWED_AUGMENTATION:
        augmentation_level = engine_defaults["augmentation_level"]
    if transition_kind not in _ALLOWED_TRANSITION_KIND:
        transition_kind = engine_defaults["transition_kind"]
    if audio_sync_mode not in _ALLOWED_AUDIO_SYNC:
        audio_sync_mode = engine_defaults["audio_sync_mode"]
    if frame_strategy not in _ALLOWED_FRAME_STRATEGY:
        frame_strategy = engine_defaults["frame_strategy"]

    video_metadata = _safe_dict(row.get("video_metadata"))
    metadata_defaults = _video_metadata_defaults(route)
    renderer_family = str(video_metadata.get("renderer_family") or metadata_defaults["renderer_family"]).strip().lower()
    if renderer_family not in {"ltx", "generic"}:
        renderer_family = metadata_defaults["renderer_family"]
    plan_row = _safe_dict(fallback_row.get("plan_row"))
    role_row = _safe_dict(fallback_row.get("role_row"))
    speaker_role = str(
        row.get("speaker_role")
        or fallback_prompt_row.get("speaker_role")
        or plan_row.get("speaker_role")
        or role_row.get("speaker_role")
        or ""
    ).strip()
    vocal_owner_role = str(
        row.get("vocal_owner_role")
        or fallback_prompt_row.get("vocal_owner_role")
        or plan_row.get("vocal_owner_role")
        or ""
    ).strip()
    lip_sync_allowed = bool(
        row.get("lip_sync_allowed")
        if "lip_sync_allowed" in row
        else (
            fallback_prompt_row.get("lip_sync_allowed")
            if "lip_sync_allowed" in fallback_prompt_row
            else plan_row.get("lip_sync_allowed")
        )
    )
    requires_audio = route == "ia2v"
    if bool(identity_ctx.get("lip_sync_only")) and route == "i2v":
        speaker_role = ""
        vocal_owner_role = ""
        lip_sync_allowed = False
        requires_audio = False
    alias_audio_sync_mode = "lip_sync" if route == "ia2v" and lip_sync_allowed else "none"
    alias_frame_strategy = "first_last" if route == "first_last" else "single_image"
    image_prompt = ". ".join(
        part
        for part in [
            fallback_photo_prompt,
            GLOBAL_HERO_IDENTITY_LOCK if has_human_subject and route != "ia2v" else "",
            BODY_CONTINUITY_LOCK if has_human_subject and route != "ia2v" else "",
            WARDROBE_CONTINUITY_LOCK if has_human_subject and route != "ia2v" else "",
        ]
        if str(part or "").strip()
    ).strip()
    image_prompt = _append_clause(image_prompt, DOMESTIC_WORLD_LOCK_BLOCK if domestic_scene else "")
    image_prompt = _strip_literal_quoted_dialogue(image_prompt)
    if lip_sync_only_i2v:
        image_prompt = fallback_photo_prompt
    image_prompt, image_prompt_diag = _sanitize_prompt_text_for_current_identity(
        image_prompt, identity_ctx, route, "route_payload.image_prompt"
    )
    video_prompt_output, video_prompt_diag = _sanitize_prompt_text_for_current_identity(
        _strip_literal_quoted_dialogue(positive_prompt), identity_ctx, route, "route_payload.video_prompt"
    )
    scene_chars = len(scene_specific_payload)
    route_chars = len(route_behavior_template)
    ratio = round(scene_chars / route_chars, 4) if route_chars > 0 else None
    final_hash = hashlib.sha256(positive_prompt.encode("utf-8")).hexdigest()[:16]
    lower_positive = positive_prompt.lower()
    lower_negative = negative_prompt.lower()
    ia2v_positive_has_wardrobe_noise = any(token in lower_positive for token in ("wardrobe", "outfit", "neckline", "collar", "body proportions"))
    ia2v_negative_killer_tokens_found, ia2v_negative_killer_token_segments = _analyze_ia2v_negative_killer_tokens(negative_prompt if route == "ia2v" else "")
    ia2v_negative_has_singing_killer_tokens = bool(ia2v_negative_killer_tokens_found)
    ia2v_video_prompt_has_singing_mechanics = all(token in lower_positive for token in ("lip sync", "jaw", "mouth"))
    sanitize_diags = [
        positive_diag,
        negative_diag,
        first_frame_diag,
        last_frame_diag,
        image_prompt_diag,
        video_prompt_diag,
    ]
    stale_identity_removed_total = sum(int(_safe_dict(d).get("stale_identity_clause_removed_count") or 0) for d in sanitize_diags)
    stale_wardrobe_removed_total = sum(int(_safe_dict(d).get("stale_wardrobe_clause_removed_count") or 0) for d in sanitize_diags)
    removed_terms_all: list[str] = []
    for diag in sanitize_diags:
        removed_terms_all.extend(_safe_list(_safe_dict(diag).get("identity_gender_conflict_terms_removed")))
    identity_conflict_detected = bool(removed_terms_all)

    return {
        "segment_id": segment_id,
        "scene_id": scene_id,
        "route": route,
        "route_payload": {
            "route": route,
            "positive_prompt": _strip_literal_quoted_dialogue(positive_prompt),
            "negative_prompt": negative_prompt,
            "first_frame_prompt": first_frame if first_frame else None,
            "last_frame_prompt": last_frame if last_frame else None,
            "image_prompt": image_prompt,
            "video_prompt": video_prompt_output,
        },
        "image_prompt": image_prompt,
        "video_prompt": video_prompt_output,
        "requires_first_frame": route == "first_last",
        "requires_last_frame": route == "first_last",
        "requires_audio": requires_audio,
        "audio_sync_mode": alias_audio_sync_mode,
        "frame_strategy": alias_frame_strategy,
        "engine_hints": {
            "motion_strength": motion_strength,
            "augmentation_level": augmentation_level,
            "transition_kind": transition_kind,
            "audio_sync_mode": audio_sync_mode,
            "frame_strategy": frame_strategy,
        },
        "video_metadata": {
            "renderer_family": renderer_family,
            "route_type": route,
            "requires_first_frame": bool(metadata_defaults["requires_first_frame"]),
            "requires_last_frame": bool(metadata_defaults["requires_last_frame"]),
        },
        "audio_behavior_hints": str(row.get("audio_behavior_hints") or "").strip(),
        "speaker_role": speaker_role or None,
        "vocal_owner_role": vocal_owner_role or None,
        "lip_sync_shot_variant": lip_sync_shot_variant or None,
        "performance_pose": performance_pose or None,
        "camera_angle": camera_angle or None,
        "gesture": gesture or None,
        "location_zone": location_zone or None,
        "mouth_readability": mouth_readability or None,
        "why_this_lip_sync_shot_is_different": str(
            row.get("why_this_lip_sync_shot_is_different")
            or fallback_prompt_row.get("why_this_lip_sync_shot_is_different")
            or ""
        ).strip()
        or None,
        "starts_from_previous_logic": str(row.get("starts_from_previous_logic") or "").strip() or None,
        "ends_with_state": str(row.get("ends_with_state") or "").strip() or None,
        "continuity_with_next": str(row.get("continuity_with_next") or "").strip() or None,
        "potential_contradiction": str(row.get("potential_contradiction") or "").strip() or None,
        "fix_if_needed": str(row.get("fix_if_needed") or "").strip() or None,
        "identity_lock_applied": bool(has_human_subject and route != "ia2v"),
        "body_lock_applied": bool(has_human_subject and route != "ia2v"),
        "wardrobe_lock_applied": bool(has_human_subject and route != "ia2v"),
        "confirmedHeroLookReferenceUsed": bool(confirmed_look_used),
        "confirmedHeroLookReferenceClauseApplied": bool(confirmed_look_used and confirmed_look_clause_applied),
        "confirmedHeroLookReferenceSkippedReason": None if confirmed_look_used else "signature_or_gender_mismatch",
        "clearVocalCanonicalApplied": bool(contract_debug.get("clearVocalCanonicalApplied")),
        "clearVocalFragmentsRemoved": bool(contract_debug.get("clearVocalFragmentsRemoved")),
        "positiveContractDuplicatesRemoved": bool(positive_contract_duplicates_removed),
        "negativePositiveContractBlocksRemoved": bool(contract_debug.get("negativePositiveContractBlocksRemoved")),
        "firstFrameHasIdentityLock": bool(first_frame_has_identity_lock),
        "lastFrameHasIdentityLock": bool(last_frame_has_identity_lock),
        "negativeContainsPositiveIdentityBlock": bool(negative_contains_positive_identity_block),
        "lip_sync_shot_variant_repeated_with_previous": False,
        "continuity_warning": str(row.get("continuity_warning") or "").strip() or None,
        "continuity_fix_applied": bool(row.get("continuity_fix_applied") or False),
        "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
        "scene_specific_prompt_present": bool(scene_specific_payload),
        "scene_specific_prompt_chars": scene_chars,
        "route_template_chars": route_chars,
        "scene_prompt_to_route_ratio": ratio,
        "final_prompt_hash": final_hash,
        "final_prompt_similarity_flag": False,
        "duplicate_final_prompt_detected": False,
        "duplicate_prompt_segments": [],
        "route_template_source": route_template_source,
        "scene_specific_payload_source": "scene_prompts.prompt_row+scene_plan",
        "final_prompt_scene_specific_missing": bool(final_prompt_scene_specific_missing),
        "final_prompt_rebuilt_from_scene_prompts": bool(final_prompt_rebuilt_from_scene_prompts),
        "final_prompt_forbidden_venue_terms_removed": bool(final_prompt_forbidden_venue_terms_removed),
        "final_image_prompt_chars": len(image_prompt),
        "final_video_prompt_chars": len(str(_safe_dict({"p":positive_prompt}).get("p") or "")),
        "final_scene_specific_chars": _scene_specific_char_count(positive_prompt),
        "ia2vVideoPromptHasSingingMechanics": bool(route != "ia2v" or ia2v_video_prompt_has_singing_mechanics),
        "ia2vPositiveHasWardrobeNoise": bool(route == "ia2v" and ia2v_positive_has_wardrobe_noise),
        "ia2vNegativeHasSingingKillerTokens": bool(route == "ia2v" and ia2v_negative_has_singing_killer_tokens),
        "ia2vNegativeKillerTokensFound": ia2v_negative_killer_tokens_found if route == "ia2v" else [],
        "ia2vNegativeKillerTokenSegments": ia2v_negative_killer_token_segments if route == "ia2v" else [],
        "identity_gender_conflict_detected": identity_conflict_detected,
        "identity_gender_conflict_terms_removed": list(dict.fromkeys(str(term).strip() for term in removed_terms_all if str(term).strip())),
        "identity_gender_conflict_segments": [segment_id] if identity_conflict_detected and segment_id else [],
        "stale_identity_clause_removed_count": stale_identity_removed_total,
        "stale_wardrobe_clause_removed_count": stale_wardrobe_removed_total,
    }


def _sanitize_output(raw: Any, segment_rows: list[dict[str, Any]], package: dict[str, Any]) -> dict[str, Any]:
    data = _safe_dict(raw)
    model_segments = _safe_list(data.get("segments"))
    by_segment_id = {
        str(_safe_dict(item).get("segment_id") or "").strip(): _safe_dict(item)
        for item in model_segments
        if str(_safe_dict(item).get("segment_id") or "").strip()
    }

    normalized: list[dict[str, Any]] = []
    previous_lip_variant = ""
    identity_ctx = _character_1_context(package)
    for fallback_row in segment_rows:
        segment_id = str(fallback_row.get("segment_id") or "").strip()
        seg = _sanitize_segment(by_segment_id.get(segment_id), fallback_row, identity_ctx)
        if normalized:
            _rewire_shadow_continuity(normalized[-1], seg)
        if str(seg.get("video_metadata", {}).get("route_type") or "") == "ia2v":
            current_variant = str(seg.get("lip_sync_shot_variant") or "").strip()
            repeated = bool(current_variant and previous_lip_variant and current_variant == previous_lip_variant)
            seg["lip_sync_shot_variant_repeated_with_previous"] = repeated
            if repeated and not seg.get("continuity_warning"):
                seg["continuity_warning"] = "adjacent_lip_sync_variant_repeated"
            previous_lip_variant = current_variant or previous_lip_variant
        normalized.append(seg)

    duplicate_segments: list[str] = []
    for idx in range(1, len(normalized)):
        prev_seg = _safe_dict(normalized[idx - 1])
        cur_seg = _safe_dict(normalized[idx])
        if str(prev_seg.get("final_prompt_hash") or "") and str(prev_seg.get("final_prompt_hash")) == str(cur_seg.get("final_prompt_hash")):
            prev_id = str(prev_seg.get("segment_id") or "")
            cur_id = str(cur_seg.get("segment_id") or "")
            duplicate_segments = [prev_id, cur_id]
            normalized[idx - 1]["final_prompt_similarity_flag"] = True
            normalized[idx]["final_prompt_similarity_flag"] = True
            normalized[idx - 1]["duplicate_final_prompt_detected"] = True
            normalized[idx]["duplicate_final_prompt_detected"] = True
            normalized[idx - 1]["duplicate_prompt_segments"] = duplicate_segments
            normalized[idx]["duplicate_prompt_segments"] = duplicate_segments

    return {
        "delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION,
        "segments": normalized,
        "scenes": [dict(row) for row in normalized],
    }


def _build_route_diagnostics(segments: list[dict[str, Any]]) -> dict[str, Any]:
    route_by_segment: dict[str, str] = {}
    missing_route_segments: list[str] = []
    first_last_ready_segments: list[str] = []
    ia2v_ready_segments: list[str] = []
    for seg in segments:
        row = _safe_dict(seg)
        segment_id = str(row.get("segment_id") or "").strip()
        route = str(row.get("route") or "").strip()
        route_by_segment[segment_id] = route
        if route not in _ALLOWED_ROUTES:
            missing_route_segments.append(segment_id)
        route_payload = _safe_dict(row.get("route_payload"))
        if route == "first_last" and str(route_payload.get("first_frame_prompt") or "").strip() and str(route_payload.get("last_frame_prompt") or "").strip():
            first_last_ready_segments.append(segment_id)
        if route == "ia2v" and str(route_payload.get("positive_prompt") or "").strip():
            ia2v_ready_segments.append(segment_id)
    return {
        "final_video_prompt_route_alias_applied": True,
        "final_video_prompt_route_by_segment": route_by_segment,
        "final_video_prompt_missing_route_segments": missing_route_segments,
        "final_video_prompt_first_last_ready_segments": first_last_ready_segments,
        "final_video_prompt_ia2v_ready_segments": ia2v_ready_segments,
    }


def generate_ltx_video_prompt_metadata(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    segment_rows = _canonical_segments(package)
    identity_ctx = _character_1_context(package)
    if not segment_rows:
        return {
            "ok": False,
            "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
            "diagnostics": {
                "final_video_prompt_prompt_version": FINAL_VIDEO_PROMPT_STAGE_VERSION,
                "final_video_prompt_segment_count": 0,
                "final_video_prompt_backend": "gemini",
                "final_video_prompt_attempts": 0,
                "final_video_prompt_used_fallback": False,
                "final_video_prompt_error": "final_video_prompt_missing_segments",
            },
            "error": "final_video_prompt_missing_segments",
        }
    if not str(api_key or "").strip():
        return {
            "ok": False,
            "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
            "diagnostics": {
                "final_video_prompt_prompt_version": FINAL_VIDEO_PROMPT_STAGE_VERSION,
                "final_video_prompt_segment_count": 0,
                "final_video_prompt_backend": "gemini",
                "final_video_prompt_attempts": 0,
                "final_video_prompt_used_fallback": False,
                "final_video_prompt_error": "gemini_api_key_missing",
            },
            "error": "gemini_api_key_missing",
        }

    instruction = _build_instruction(_build_model_payload(package, segment_rows))
    last_error = ""
    attempts = 0
    normalized_payload: dict[str, Any] = {}
    configured_timeout = get_scenario_stage_timeout("final_video_prompt")
    timed_out = False
    response_was_empty_after_timeout = False

    for _ in range(2):
        attempts += 1
        try:
            response = post_generate_content(
                api_key=str(api_key or "").strip(),
                model=FINAL_VIDEO_PROMPT_MODEL,
                body={
                    "contents": [{"role": "user", "parts": [{"text": instruction}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
                },
                timeout=configured_timeout,
            )
            if isinstance(response, dict) and response.get("__http_error__"):
                raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")
            parsed = _extract_json_obj(_extract_gemini_text(response))
            normalized_payload = _sanitize_output(parsed, segment_rows, package)
            last_error = ""
            break
        except Exception as exc:
            last_error = str(exc)[:220] or "final_video_prompt_generation_failed"
            if is_timeout_error(last_error):
                timed_out = True
                response_was_empty_after_timeout = True
            normalized_payload = {}

    ok = bool(normalized_payload and _safe_list(normalized_payload.get("segments")))
    scene_contract_logs = []
    route_diagnostics: dict[str, Any] = {
        "final_video_prompt_route_alias_applied": True,
        "final_video_prompt_route_by_segment": {},
        "final_video_prompt_missing_route_segments": [],
        "final_video_prompt_first_last_ready_segments": [],
        "final_video_prompt_ia2v_ready_segments": [],
    }
    if ok:
        route_diagnostics = _build_route_diagnostics(_safe_list(normalized_payload.get("segments")))
        for seg in _safe_list(normalized_payload.get("segments")):
            row = _safe_dict(seg)
            route_payload = _safe_dict(row.get("route_payload"))
            scene_contract_logs.append(
                {
                    "sceneId": str(row.get("scene_id") or row.get("segment_id") or ""),
                    "route": str(_safe_dict(row.get("video_metadata")).get("route_type") or ""),
                    "hasIdentityLock": bool(row.get("identity_lock_applied")),
                    "hasBodyLock": bool(row.get("body_lock_applied")),
                    "hasWardrobeLock": bool(row.get("wardrobe_lock_applied")),
                    "lipSyncShotVariant": str(row.get("lip_sync_shot_variant") or ""),
                    "confirmedHeroLookReferenceUsed": bool(row.get("confirmedHeroLookReferenceUsed")),
                    "confirmedHeroLookReferenceClauseApplied": bool(row.get("confirmedHeroLookReferenceClauseApplied")),
                    "clearVocalCanonicalApplied": bool(row.get("clearVocalCanonicalApplied")),
                    "clearVocalFragmentsRemoved": bool(row.get("clearVocalFragmentsRemoved")),
                    "positiveContractDuplicatesRemoved": bool(row.get("positiveContractDuplicatesRemoved")),
                    "negativePositiveContractBlocksRemoved": bool(row.get("negativePositiveContractBlocksRemoved")),
                    "firstFrameHasIdentityLock": bool(row.get("firstFrameHasIdentityLock")),
                    "lastFrameHasIdentityLock": bool(row.get("lastFrameHasIdentityLock")),
                    "negativeContainsPositiveIdentityBlock": bool(row.get("negativeContainsPositiveIdentityBlock")),
                    "positivePromptPreview": str(route_payload.get("positive_prompt") or "")[:220],
                    "negativePromptPreview": str(route_payload.get("negative_prompt") or "")[:220],
                }
            )
    stale_identity_removed_total = sum(int(_safe_dict(seg).get("stale_identity_clause_removed_count") or 0) for seg in _safe_list(normalized_payload.get("segments")))
    return {
        "ok": ok,
        "final_video_prompt": normalized_payload if ok else {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
        "diagnostics": {
            "final_video_prompt_prompt_version": FINAL_VIDEO_PROMPT_STAGE_VERSION,
            "final_video_prompt_segment_count": len(_safe_list(normalized_payload.get("segments"))) if ok else 0,
            "final_video_prompt_backend": "gemini",
            "final_video_prompt_attempts": attempts,
            "final_video_prompt_used_fallback": False,
            "final_video_prompt_error": "" if ok else ("final_video_prompt_timeout" if timed_out else (last_error or "final_video_prompt_generation_failed")),
            "final_video_prompt_segment_ids": [str(_safe_dict(row).get("segment_id") or "") for row in segment_rows],
            "final_video_prompt_configured_timeout_sec": configured_timeout,
            "final_video_prompt_timeout_stage_policy_name": scenario_timeout_policy_name("final_video_prompt"),
            "final_video_prompt_timed_out": timed_out,
            "final_video_prompt_timeout_retry_attempted": bool(timed_out and attempts > 1),
            "final_video_prompt_response_was_empty_after_timeout": response_was_empty_after_timeout,
            "final_video_prompt_scene_contract_logs": scene_contract_logs,
            "current_character_1_gender_hint": str(identity_ctx.get("gender_hint") or ""),
            "current_character_1_identity_label": str(identity_ctx.get("identity_label") or ""),
            "current_character_1_ref_count": int(identity_ctx.get("ref_count") or 0),
            "current_character_1_ref_signature": str(identity_ctx.get("ref_signature") or ""),
            "current_identity_source": "current_connected_ref",
            "stale_identity_clause_removed_count": stale_identity_removed_total if ok else 0,
            "stale_wardrobe_clause_removed_count": 0,
            **route_diagnostics,
        },
        "error": "" if ok else ("final_video_prompt_timeout" if timed_out else (last_error or "final_video_prompt_generation_failed")),
    }
