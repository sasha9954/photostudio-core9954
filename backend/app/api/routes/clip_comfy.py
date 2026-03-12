from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.engine.comfy_brain_engine import run_comfy_plan

import json
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


class RefItemIn(BaseModel):
    url: str = ""
    name: str = ""


class ClipComfyPlanIn(BaseModel):
    mode: str = "clip"
    output: str = "comfy image"
    stylePreset: str = "realism"
    freezeStyle: bool = False
    text: str = ""
    audioUrl: str = ""
    refsByRole: dict[str, list[RefItemIn]] = Field(default_factory=dict)
    storyControlMode: str = ""
    storyMissionSummary: str = ""
    timelineSource: str = ""
    narrativeSource: str = ""


@router.post("/clip/comfy/plan")
async def clip_comfy_plan(request: Request, payload: ClipComfyPlanIn) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    raw_body_bytes = await request.body()
    raw_body_text = raw_body_bytes.decode("utf-8", errors="replace")

    parsed_json: Any = None
    json_parse_error: str | None = None
    try:
        parsed_json = json.loads(raw_body_text) if raw_body_text else None
    except Exception as exc:  # diagnostic logging only
        json_parse_error = str(exc)

    logger.info("[clip_comfy_plan] content-type=%s", content_type)
    logger.info("[clip_comfy_plan] raw-body=%s", raw_body_text)
    if json_parse_error:
        logger.info("[clip_comfy_plan] parsed-json-error=%s", json_parse_error)
    else:
        logger.info("[clip_comfy_plan] parsed-json=%s", parsed_json)

    req = payload.model_dump(mode="json")
    req["refsByRole"] = {
        role: [item.model_dump(mode="json") for item in items]
        for role, items in (payload.refsByRole or {}).items()
    }
    return run_comfy_plan(req)
