from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.engine.gemini_rest import post_generate_content

COMFY_REF_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
HUMAN_ROLES = {"character_1", "character_2", "character_3", "group"}


def _guess_entity_type(role: str) -> str:
    if role in HUMAN_ROLES:
        return "human"
    if role == "animal":
        return "animal"
    if role == "props":
        return "object"
    if role == "location":
        return "location"
    if role == "style":
        return "style"
    return "unknown"


def _extract_tokens(items: list[dict[str, str]]) -> list[str]:
    tokens: list[str] = []
    for item in items:
        raw = f"{item.get('name') or ''} {item.get('url') or ''}".lower()
        for chunk in raw.replace("_", " ").replace("-", " ").split():
            clean = "".join(ch for ch in chunk if ch.isalnum())
            if clean:
                tokens.append(clean)
    return tokens


def _role_invariants(role: str, items: list[dict[str, str]]) -> list[str]:
    # Text-only extraction fallback. Stronger visual profiling can be plugged in later with VLM.
    tokens = _extract_tokens(items)
    top_tokens = sorted({t for t in tokens if len(t) >= 4})[:8]
    invariants = [f"reference_count={len(items)}"]
    if top_tokens:
        invariants.append(f"name_or_filename_tokens={', '.join(top_tokens)}")

    if role in HUMAN_ROLES:
        invariants.extend([
            "preserve same person identity",
            "preserve face structure, hair color/length/style, and outfit signature",
            "preserve age range and body type impression",
        ])
    elif role == "animal":
        invariants.extend([
            "preserve same species and breed-like appearance",
            "preserve coat color/pattern, fur length, and body size class",
            "preserve muzzle/ear/tail morphology cues",
        ])
    elif role == "props":
        invariants.extend([
            "preserve object category and silhouette",
            "preserve dominant material and color",
            "preserve distinctive parts and proportions",
        ])
    elif role == "location":
        invariants.extend([
            "preserve world/location identity",
            "preserve architecture/environment anchors",
        ])
    elif role == "style":
        invariants.extend([
            "preserve style palette, contrast behavior, and lens feel",
            "style cannot override identity locks of actors/objects",
        ])
    return invariants


def _allowed_variations(role: str) -> list[str]:
    if role in HUMAN_ROLES:
        return ["camera angle", "shot size", "pose", "facial expression", "natural cloth deformation"]
    if role == "animal":
        return ["pose", "camera angle", "distance", "micro body tension", "natural fur/light variation"]
    if role == "props":
        return ["camera angle", "frame scale", "logical placement", "scene relighting"]
    return ["framing", "lighting adaptation"]


def _forbidden_changes(role: str) -> list[str]:
    if role in HUMAN_ROLES:
        return [
            "identity swap",
            "face replacement",
            "hair recolor/hairstyle replacement",
            "outfit replacement",
            "body type or age drift",
            "gender presentation drift",
        ]
    if role == "animal":
        return [
            "species drift",
            "breed drift",
            "coat color/pattern drift",
            "size class drift",
            "muzzle/ear/tail morphology drift",
        ]
    if role == "props":
        return [
            "object type drift",
            "material drift",
            "dominant color drift",
            "geometry/proportion drift",
            "distinctive detail drift",
            "scale class drift",
        ]
    if role == "location":
        return ["location identity drift", "architecture anchor replacement"]
    return []


def _try_build_visual_profile_with_gemini(role: str, items: list[dict[str, str]]) -> tuple[dict[str, Any] | None, str | None, str | None]:
    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        return None, None, "missing_api_key"

    image_urls = [str(item.get("url") or "").strip() for item in items if str(item.get("url") or "").strip()][:3]
    if not image_urls:
        return None, None, "missing_urls"

    model = (getattr(settings, "GEMINI_VISION_MODEL", None) or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    prompt = (
        "Analyze reference images and return strict JSON only. "
        "Schema: {visualProfile: object, invariants: string[], allowedVariations: string[], forbiddenChanges: string[], confidence: 'low'|'medium'|'high'}. "
        f"Role={role}. Entity type should reflect role semantics and preserve identity continuity contracts."
    )
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for url in image_urls:
        parts.append({"fileData": {"mimeType": "image/jpeg", "fileUri": url}})

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    resp = post_generate_content(api_key, model, body, timeout=60)
    if not isinstance(resp, dict) or resp.get("__http_error__"):
        return None, model, "vision_http_error"

    text = ""
    try:
        candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
        if candidates:
            parts_out = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
            if isinstance(parts_out, list):
                text = "".join(str(p.get("text") or "") for p in parts_out if isinstance(p, dict)).strip()
    except Exception:
        text = ""

    if not text:
        return None, model, "vision_empty_response"

    try:
        parsed = json.loads(text)
    except Exception:
        return None, model, "vision_invalid_json"

    if not isinstance(parsed, dict):
        return None, model, "vision_invalid_payload"

    out = {
        "visualProfile": parsed.get("visualProfile") if isinstance(parsed.get("visualProfile"), dict) else {},
        "invariants": parsed.get("invariants") if isinstance(parsed.get("invariants"), list) else [],
        "allowedVariations": parsed.get("allowedVariations") if isinstance(parsed.get("allowedVariations"), list) else [],
        "forbiddenChanges": parsed.get("forbiddenChanges") if isinstance(parsed.get("forbiddenChanges"), list) else [],
        "confidence": parsed.get("confidence") if str(parsed.get("confidence") or "") in {"low", "medium", "high"} else None,
    }
    return out, model, None


def build_reference_profiles(refs_by_role: dict[str, Any] | None) -> dict[str, Any]:
    source = refs_by_role if isinstance(refs_by_role, dict) else {}
    profiles: dict[str, Any] = {}
    for role in COMFY_REF_ROLES:
        items = source.get(role) if isinstance(source.get(role), list) else []
        clean_items = [i for i in items if isinstance(i, dict) and str(i.get("url") or "").strip()]
        if not clean_items:
            continue
        entity_type = _guess_entity_type(role)

        visual_profile_fallback = {
            "sourceImageCount": len(clean_items),
            "sourceNames": [str(item.get("name") or "").strip() for item in clean_items if str(item.get("name") or "").strip()],
        }
        invariants_fallback = _role_invariants(role, clean_items)
        allowed_fallback = _allowed_variations(role)
        forbidden_fallback = _forbidden_changes(role)
        confidence_fallback = "medium" if len(clean_items) > 1 else "low"

        visual_probe, vision_model, vision_error = _try_build_visual_profile_with_gemini(role, clean_items)

        profiles[role] = {
            "role": role,
            "entityType": entity_type,
            "entityId": role,
            "identityLock": role in HUMAN_ROLES or role in {"animal", "props"},
            "environmentLock": role == "location",
            "styleLock": role == "style",
            "visualProfile": visual_probe.get("visualProfile") if isinstance(visual_probe, dict) else visual_profile_fallback,
            "invariants": (visual_probe.get("invariants") if isinstance(visual_probe, dict) else None) or invariants_fallback,
            "variableTraits": ["pose", "crop", "angle", "expression", "lighting"],
            "allowedVariations": (visual_probe.get("allowedVariations") if isinstance(visual_probe, dict) else None) or allowed_fallback,
            "forbiddenChanges": (visual_probe.get("forbiddenChanges") if isinstance(visual_probe, dict) else None) or forbidden_fallback,
            "confidence": (visual_probe.get("confidence") if isinstance(visual_probe, dict) else None) or confidence_fallback,
            "profilingSource": "gemini_vision" if isinstance(visual_probe, dict) else "text_fallback",
            "profilingModel": vision_model,
            "profilingError": vision_error,
        }
    return profiles


def summarize_profiles(profiles: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for role, profile in (profiles or {}).items():
        if not isinstance(profile, dict):
            continue
        out[role] = {
            "entityType": profile.get("entityType"),
            "identityLock": bool(profile.get("identityLock")),
            "invariants": profile.get("invariants") or [],
            "allowedVariations": profile.get("allowedVariations") or [],
            "forbiddenChanges": profile.get("forbiddenChanges") or [],
            "confidence": profile.get("confidence"),
            "profilingSource": profile.get("profilingSource"),
            "profilingModel": profile.get("profilingModel"),
            "profilingError": profile.get("profilingError"),
        }
    return out
