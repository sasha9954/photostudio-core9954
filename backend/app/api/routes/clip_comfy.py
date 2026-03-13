from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from app.engine.comfy_brain_engine import run_comfy_plan, run_comfy_prompt_sync
from app.engine.comfy_reference_profile import build_reference_profiles

import json
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


class RefItemIn(BaseModel):
    url: str = ""
    name: str = ""




class ClipComfyPromptSyncIn(BaseModel):
    sourceText: str = ""
    sourceLang: str = "ru"
    targetLang: str = "en"
    promptType: str = "image"
    sceneContext: dict[str, Any] = Field(default_factory=dict)
    stylePreset: str = ""
    mode: str = ""

class ClipComfyPlanIn(BaseModel):
    mode: str = "clip"
    output: str = "comfy image"
    stylePreset: str = "realism"
    freezeStyle: bool = False
    text: str = ""
    audioUrl: str = ""
    audioDurationSec: float | None = None
    refsByRole: dict[str, list[RefItemIn]] = Field(default_factory=dict)
    storyControlMode: str = ""
    storyMissionSummary: str = ""
    timelineSource: str = ""
    narrativeSource: str = ""


class ClipComfyConnectRefsIn(BaseModel):
    refsByRole: dict[str, list[RefItemIn]] = Field(default_factory=dict)


CONNECT_REFS_MAIN_ROLES = ["character_1", "character_2", "character_3", "animal", "props", "location", "style"]


def _extract_profile_tokens(profile: dict[str, Any] | None) -> str:
    source = profile if isinstance(profile, dict) else {}
    visual_profile = source.get("visualProfile") if isinstance(source.get("visualProfile"), dict) else {}
    fields: list[Any] = [
        source.get("entityType"),
        source.get("detectedEntityType"),
        source.get("expectedEntityType"),
        source.get("invariants"),
        source.get("forbiddenChanges"),
    ]
    fields.extend(list(visual_profile.values()))

    tokens: list[str] = []
    for value in fields:
        if isinstance(value, str):
            tokens.append(value)
        elif isinstance(value, list):
            tokens.extend([str(v) for v in value if isinstance(v, (str, int, float))])
        elif isinstance(value, dict):
            tokens.extend([str(v) for v in value.values() if isinstance(v, (str, int, float))])
        elif isinstance(value, (int, float)):
            tokens.append(str(value))
    return " ".join(tokens).strip().lower()


def _build_human_label(profile: dict[str, Any] | None) -> str:
    source = profile if isinstance(profile, dict) else {}
    visual_profile = source.get("visualProfile") if isinstance(source.get("visualProfile"), dict) else {}
    raw_gender = (
        visual_profile.get("genderPresentation")
        or source.get("genderPresentation")
        or visual_profile.get("gender")
        or source.get("gender")
        or ""
    )
    gender = str(raw_gender).strip().lower()

    female_tokens = {"female", "woman", "girl", "feminine", "жен", "женщина", "девушка"}
    male_tokens = {"male", "man", "boy", "masculine", "муж", "мужчина", "парень"}

    if any(token in gender for token in female_tokens):
        base = "женщина"
    elif any(token in gender for token in male_tokens):
        base = "мужчина"
    else:
        base = "персонаж"

    return base


def _build_animal_label(profile: dict[str, Any] | None) -> str:
    tokens = _extract_profile_tokens(profile)
    if any(token in tokens for token in ["кот", "cat", "feline"]):
        return "кот"
    if any(token in tokens for token in ["собак", "dog", "canine"]):
        return "собака"
    if any(token in tokens for token in ["волк", "wolf"]):
        return "волк"
    return "животное"


def _build_props_label(profile: dict[str, Any] | None) -> str:
    tokens = _extract_profile_tokens(profile)
    if any(token in tokens for token in ["машин", "car", "automobile", "sedan", "suv"]):
        return "машина"
    if any(token in tokens for token in ["мото", "motorcycle", "bike"]):
        return "мотоцикл"
    if any(token in tokens for token in ["camera", "камера"]):
        return "камера"
    if any(token in tokens for token in ["телефон", "phone", "smartphone", "iphone", "android"]):
        return "телефон"
    if any(token in tokens for token in ["tech", "device", "gadget", "техник"]):
        return "техника"
    return "предмет"


def _build_location_label(profile: dict[str, Any] | None) -> str:
    tokens = _extract_profile_tokens(profile)
    if any(token in tokens for token in ["город", "city", "urban", "downtown"]):
        return "город"
    if any(token in tokens for token in ["квартир", "apartment", "flat", "interior"]):
        return "квартира"
    if any(token in tokens for token in ["лес", "forest", "woodland", "jungle"]):
        return "лес"
    if any(token in tokens for token in ["марс", "mars"]):
        return "Марс"
    return "локация"


def _build_style_label(profile: dict[str, Any] | None) -> str:
    tokens = _extract_profile_tokens(profile)
    if any(token in tokens for token in ["реал", "realism", "photoreal", "naturalistic"]):
        return "реализм"
    if any(token in tokens for token in ["кино", "cinema", "cinematic", "film"]):
        return "кино"
    if any(token in tokens for token in ["неон", "neon", "cyberpunk", "glow"]):
        return "неон"
    return "стиль"


def _build_short_label_for_role(role: str, profile: dict[str, Any] | None) -> str:
    if role in {"character_1", "character_2", "character_3"}:
        return _build_human_label(profile)
    if role == "animal":
        return _build_animal_label(profile)
    if role == "props":
        return _build_props_label(profile)
    if role == "location":
        return _build_location_label(profile)
    if role == "style":
        return _build_style_label(profile)
    return "реф"


@router.post("/clip/comfy/plan")
async def clip_comfy_plan(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    raw_body_bytes = await request.body()
    raw_body_text = raw_body_bytes.decode("utf-8", errors="replace")

    logger.info("[clip_comfy_plan] content-type=%s", content_type)
    logger.info("[clip_comfy_plan] raw-body=%s", raw_body_text)

    parsed_json: Any = None
    try:
        parsed_json = json.loads(raw_body_text)
        logger.info("[clip_comfy_plan] parsed-json=%s", parsed_json)
    except Exception as exc:
        logger.exception("[clip_comfy_plan] json-parse-error=%s", exc)
        raise HTTPException(status_code=422, detail=f"Invalid JSON body: {exc}") from exc

    try:
        payload = ClipComfyPlanIn.model_validate(parsed_json or {})
    except ValidationError as exc:
        logger.exception("[clip_comfy_plan] pydantic-validation-error=%s", exc)
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    req = payload.model_dump(mode="json")
    req["refsByRole"] = {
        role: [item.model_dump(mode="json") for item in items]
        for role, items in (payload.refsByRole or {}).items()
    }
    return run_comfy_plan(req)


@router.post("/clip/comfy/prompt-sync")
async def clip_comfy_prompt_sync(payload: ClipComfyPromptSyncIn) -> dict[str, Any]:
    req = payload.model_dump(mode="json")
    return run_comfy_prompt_sync(req)


@router.post("/clip/comfy/connect-refs")
async def clip_comfy_connect_refs(payload: ClipComfyConnectRefsIn) -> dict[str, Any]:
    refs_by_role = {
        role: [item.model_dump(mode="json") for item in items]
        for role, items in (payload.refsByRole or {}).items()
    }
    filtered_refs_by_role: dict[str, list[dict[str, Any]]] = {}
    for role in CONNECT_REFS_MAIN_ROLES:
        role_items = refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []
        clean_items = [item for item in role_items if isinstance(item, dict) and str(item.get("url") or "").strip()]
        if clean_items:
            filtered_refs_by_role[role] = clean_items

    if not filtered_refs_by_role:
        return {
            "ok": True,
            "connectedRefsSummary": [],
            "referenceProfiles": {},
        }

    reference_profiles = build_reference_profiles(filtered_refs_by_role)
    connected_refs_summary: list[dict[str, str]] = []
    for role in CONNECT_REFS_MAIN_ROLES:
        role_profile = reference_profiles.get(role) if isinstance(reference_profiles.get(role), dict) else None
        if not role_profile:
            continue
        connected_refs_summary.append(
            {
                "role": role,
                "label": _build_short_label_for_role(role, role_profile),
            }
        )
    logger.info("[clip_comfy_connect_refs] connected roles=%s", [item.get("role") for item in connected_refs_summary])

    return {
        "ok": True,
        "connectedRefsSummary": connected_refs_summary,
        "referenceProfiles": reference_profiles,
    }
