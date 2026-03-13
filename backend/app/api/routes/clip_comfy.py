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


def _build_character_1_label(profile: dict[str, Any] | None) -> str:
    source = profile if isinstance(profile, dict) else {}
    visual_profile = source.get("visualProfile") if isinstance(source.get("visualProfile"), dict) else {}
    gender = str(visual_profile.get("genderPresentation") or "").strip().lower()
    age = str(visual_profile.get("ageRange") or "").strip()

    male_tokens = {"male", "man", "masculine", "муж", "мужчина", "парень"}
    female_tokens = {"female", "woman", "feminine", "жен", "женщина", "девушка"}

    if any(token in gender for token in male_tokens):
        base = "мужчина"
    elif any(token in gender for token in female_tokens):
        base = "женщина"
    else:
        base = "персонаж"

    if age:
        return f"{base}, {age}"
    return base


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
    character_1_items = refs_by_role.get("character_1") if isinstance(refs_by_role.get("character_1"), list) else []
    character_1_items = [item for item in character_1_items if isinstance(item, dict) and str(item.get("url") or "").strip()]

    if not character_1_items:
        return {
            "ok": True,
            "connectedRefsSummary": [],
            "referenceProfiles": {},
        }

    reference_profiles = build_reference_profiles({"character_1": character_1_items})
    character_1_profile = reference_profiles.get("character_1") if isinstance(reference_profiles.get("character_1"), dict) else {}
    return {
        "ok": True,
        "connectedRefsSummary": [
            {
                "role": "character_1",
                "label": _build_character_1_label(character_1_profile),
            }
        ],
        "referenceProfiles": {
            "character_1": character_1_profile,
        },
    }
