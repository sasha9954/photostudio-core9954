from __future__ import annotations

from typing import Any

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


def build_reference_profiles(refs_by_role: dict[str, Any] | None) -> dict[str, Any]:
    source = refs_by_role if isinstance(refs_by_role, dict) else {}
    profiles: dict[str, Any] = {}
    for role in COMFY_REF_ROLES:
        items = source.get(role) if isinstance(source.get(role), list) else []
        clean_items = [i for i in items if isinstance(i, dict) and str(i.get("url") or "").strip()]
        if not clean_items:
            continue
        entity_type = _guess_entity_type(role)
        profiles[role] = {
            "role": role,
            "entityType": entity_type,
            "entityId": role,
            "identityLock": role in HUMAN_ROLES or role in {"animal", "props"},
            "environmentLock": role == "location",
            "styleLock": role == "style",
            "visualProfile": {
                "sourceImageCount": len(clean_items),
                "sourceNames": [str(item.get("name") or "").strip() for item in clean_items if str(item.get("name") or "").strip()],
            },
            "invariants": _role_invariants(role, clean_items),
            "variableTraits": ["pose", "crop", "angle", "expression", "lighting"],
            "allowedVariations": _allowed_variations(role),
            "forbiddenChanges": _forbidden_changes(role),
            "confidence": "medium" if len(clean_items) > 1 else "low",
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
        }
    return out
