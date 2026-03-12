from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.engine.comfy_brain_engine import run_comfy_plan

router = APIRouter()


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
def clip_comfy_plan(payload: ClipComfyPlanIn) -> dict[str, Any]:
    req = payload.model_dump(mode="json")
    req["refsByRole"] = {
        role: [item.model_dump(mode="json") for item in items]
        for role, items in (payload.refsByRole or {}).items()
    }
    return run_comfy_plan(req)
