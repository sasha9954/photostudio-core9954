from __future__ import annotations

import base64
import json
import mimetypes
import socket
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.core.config import settings
from app.core.static_paths import ASSETS_DIR
from app.engine.gemini_rest import post_generate_content

COMFY_REF_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
HUMAN_ROLES = {"character_1", "character_2", "character_3", "group"}
MAX_VISION_IMAGE_BYTES = 8 * 1024 * 1024


def _resolve_reference_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        return raw
    if raw.startswith("//"):
        return f"http:{raw}"
    base = (settings.PUBLIC_BASE_URL or "http://127.0.0.1:8000").rstrip("/")
    if raw.startswith("/"):
        return f"{base}{raw}"
    return f"{base}/{raw}"


def _extract_mime_type(url: str, headers: dict[str, str], data: bytes) -> str:
    header_mime = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    if header_mime.startswith("image/"):
        return header_mime
    guessed_from_url, _ = mimetypes.guess_type(url)
    if guessed_from_url and guessed_from_url.startswith("image/"):
        return guessed_from_url
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _extract_local_static_asset_relative_path(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None

    parsed = urllib.parse.urlparse(raw)
    path = raw
    if parsed.scheme in {"http", "https"}:
        host = (parsed.hostname or "").lower()
        local_hosts = {"127.0.0.1", "localhost"}
        public_base = (settings.PUBLIC_BASE_URL or "").strip()
        if public_base:
            try:
                public_host = (urllib.parse.urlparse(public_base).hostname or "").lower()
                if public_host:
                    local_hosts.add(public_host)
            except Exception:
                pass
        if host not in local_hosts:
            return None
        path = parsed.path or ""
    elif raw.startswith("//"):
        return None

    normalized = path.lstrip("/")
    prefix = "static/assets/"
    if not normalized.startswith(prefix):
        return None

    rel_path = normalized[len(prefix) :]
    return rel_path or None


def _read_local_static_asset(url: str) -> tuple[bytes | None, str, str | None]:
    rel_path = _extract_local_static_asset_relative_path(url)
    if not rel_path:
        return None, "", None

    try:
        decoded_rel_path = urllib.parse.unquote(rel_path)
        assets_root = Path(ASSETS_DIR).resolve()
        file_path = (assets_root / decoded_rel_path).resolve()
        if assets_root not in file_path.parents:
            return None, "", "local_asset_not_found"
        if not file_path.exists() or not file_path.is_file():
            return None, "", "local_asset_not_found"
        return file_path.read_bytes(), file_path.as_uri(), None
    except OSError:
        return None, "", "local_asset_read_failed"
    except Exception:
        return None, "", "local_asset_read_failed"


def _load_image_inline_part(url: str) -> tuple[dict[str, Any] | None, str | None]:
    resolved = _resolve_reference_url(url)
    if not resolved:
        return None, "image_download_failed"
    data: bytes
    data_source_for_mime = resolved
    headers: dict[str, str] = {}
    local_data, local_source, local_error = _read_local_static_asset(resolved)
    if local_error:
        return None, local_error
    if local_data is not None:
        data = local_data
        data_source_for_mime = local_source
    else:
        req = urllib.request.Request(resolved, headers={"User-Agent": "photostudio-gemini-vision/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        except urllib.error.HTTPError:
            return None, "image_http_error"
        except (socket.timeout, TimeoutError):
            return None, "image_timeout"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                return None, "image_timeout"
            return None, "image_download_failed"
        except ValueError:
            return None, "image_download_failed"
        except Exception:
            return None, "image_download_failed"

    if not data:
        return None, "image_download_failed"

    mime_type = _extract_mime_type(data_source_for_mime, headers, data)
    if not mime_type:
        return None, "image_invalid_mime"

    if len(data) > MAX_VISION_IMAGE_BYTES:
        return None, "image_too_large"

    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }, None


def _vision_profile_prompt(role: str, entity_type: str) -> str:
    schemas = {
        "human": (
            "visualProfile must include keys: genderPresentation, ageRange, hair, face, bodyType, outfit, accessories, dominantColors"
        ),
        "animal": (
            "visualProfile must include keys: species, breedLikeAppearance, coat, bodyType, sizeClass, morphology"
        ),
        "object": (
            "visualProfile must include keys: objectCategory, silhouette, material, dominantColors, distinctiveDetails, scaleClass"
        ),
        "location": (
            "visualProfile must include keys: environmentType, architecture, surfaceState, weatherSeasonCues, worldAnchors"
        ),
        "style": (
            "visualProfile must include keys: palette, contrast, lensFeel, grade, atmosphere"
        ),
    }
    visual_profile_requirements = schemas.get(entity_type, "visualProfile must include stable identifying cues for this role")
    entity_constraints = {
        "human": "Preserve identity-level cues, not a generic fashion summary. Do not generalize hair color/style. Do not generalize outfit signature.",
        "animal": "Do not generalize species/breed-like appearance. Do not generalize coat pattern or morphology.",
        "object": "Do not generalize object category, silhouette, material, or distinctive geometry.",
    }
    strict_entity_constraints = entity_constraints.get(entity_type, "")
    return (
        "You are a strict visual profiler. Respond with JSON only (no markdown, no prose). "
        "Return exactly one object with keys: entityType, visualProfile, invariants, allowedVariations, forbiddenChanges, confidence. "
        "entityType must be one of: human, animal, object, location, style. "
        "invariants/allowedVariations/forbiddenChanges must be arrays of short strings. "
        "confidence must be one of: low, medium, high. "
        f"Role: {role}. Expected entityType: {entity_type}. "
        f"{visual_profile_requirements}. "
        f"{strict_entity_constraints} "
        "Do not include any other top-level keys."
    )


def _is_weak_visual_profile(visual_probe: dict[str, Any]) -> bool:
    visual_profile = visual_probe.get("visualProfile") if isinstance(visual_probe.get("visualProfile"), dict) else {}
    non_empty_profile_values = 0
    for value in visual_profile.values():
        if isinstance(value, str) and value.strip():
            non_empty_profile_values += 1
        elif isinstance(value, list) and any(str(v).strip() for v in value):
            non_empty_profile_values += 1
        elif isinstance(value, dict) and value:
            non_empty_profile_values += 1
        elif value not in (None, "", [], {}):
            non_empty_profile_values += 1

    invariants = visual_probe.get("invariants") if isinstance(visual_probe.get("invariants"), list) else []
    forbidden_changes = visual_probe.get("forbiddenChanges") if isinstance(visual_probe.get("forbiddenChanges"), list) else []
    confidence = str(visual_probe.get("confidence") or "").strip().lower()
    weak_confidence = confidence in {"", "low"}

    return non_empty_profile_values <= 1 and not invariants and not forbidden_changes and weak_confidence


def _extract_json_text_from_vision_response(resp: dict[str, Any]) -> str:
    chunks: list[str] = []
    candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        parts_out = (((candidate.get("content") or {}).get("parts")) or [])
        if not isinstance(parts_out, list):
            continue
        for part in parts_out:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text = part.get("text") or ""
                if text:
                    chunks.append(text)
    text = "\n".join(chunks).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


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
    entity_type = _guess_entity_type(role)
    prompt = _vision_profile_prompt(role, entity_type)
    parts: list[dict[str, Any]] = [{"text": prompt}]
    loaded_images = 0
    image_errors: list[str] = []
    for url in image_urls:
        image_part, image_error = _load_image_inline_part(url)
        if image_part:
            parts.append(image_part)
            loaded_images += 1
        elif image_error:
            image_errors.append(image_error)

    if loaded_images == 0:
        return None, model, (image_errors[0] if image_errors else "no_valid_images_loaded")

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
        text = _extract_json_text_from_vision_response(resp)
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

    parsed_entity_type = str(parsed.get("entityType") or "").strip().lower() or None
    if parsed_entity_type and parsed_entity_type not in {"human", "animal", "object", "location", "style"}:
        return None, model, "vision_invalid_payload"

    out = {
        "visualProfile": parsed.get("visualProfile") if isinstance(parsed.get("visualProfile"), dict) else {},
        "invariants": parsed.get("invariants") if isinstance(parsed.get("invariants"), list) else [],
        "allowedVariations": parsed.get("allowedVariations") if isinstance(parsed.get("allowedVariations"), list) else [],
        "forbiddenChanges": parsed.get("forbiddenChanges") if isinstance(parsed.get("forbiddenChanges"), list) else [],
        "confidence": parsed.get("confidence") if str(parsed.get("confidence") or "") in {"low", "medium", "high"} else None,
        "expectedEntityType": entity_type,
        "detectedEntityType": parsed_entity_type,
    }
    if _is_weak_visual_profile(out):
        return None, model, "weak_vision_profile"
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
            "expectedEntityType": entity_type,
            "detectedEntityType": visual_probe.get("detectedEntityType") if isinstance(visual_probe, dict) else None,
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
            "expectedEntityType": profile.get("expectedEntityType"),
            "detectedEntityType": profile.get("detectedEntityType"),
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
