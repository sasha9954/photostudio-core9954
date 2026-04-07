import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings
from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)

ALLOWED_CLIP_ROUTES = ("i2v", "ia2v", "first_last")
CLIP_PIPELINE_MODEL = "gemini-3.1-pro-preview"


class ClipPipelineError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 422, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class WholeTrackSection(BaseModel):
    section_id: str
    t0: float
    t1: float
    section_type: str
    energy: int = 5
    recurring_group_id: str | None = None
    suggested_visual_role: str = ""


class TimeRangeReason(BaseModel):
    t0: float
    t1: float
    reason: str = ""


class ChunkBoundary(BaseModel):
    chunk_id: str
    t0: float
    t1: float


class WholeTrackMapResponse(BaseModel):
    track_id: str
    mode: str
    duration_sec: float
    global_arc: str
    world_lock: dict[str, Any] = Field(default_factory=dict)
    identity_lock: dict[str, Any] = Field(default_factory=dict)
    style_lock: dict[str, Any] = Field(default_factory=dict)
    sections: list[WholeTrackSection] = Field(default_factory=list)
    no_split_ranges: list[TimeRangeReason] = Field(default_factory=list)
    suggested_chunk_boundaries: list[ChunkBoundary] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_clip_mode(self) -> "WholeTrackMapResponse":
        if self.mode != "clip":
            raise ValueError("mode must be clip")
        if self.duration_sec <= 0:
            raise ValueError("duration_sec must be > 0")
        return self


class ContinuityTailState(BaseModel):
    hero_state: str = ""
    location_state: str = ""
    camera_state: str = ""
    continuity_tokens: list[str] = Field(default_factory=list)


class ContinuityIn(BaseModel):
    previous_chunk_id: str | None = None
    tail_state: ContinuityTailState = Field(default_factory=ContinuityTailState)


class ChunkMapRef(BaseModel):
    section_ids: list[str] = Field(default_factory=list)
    recurring_group_ids: list[str] = Field(default_factory=list)


class ChunkStoryboardRequest(BaseModel):
    track_id: str
    mode: str = "clip"
    chunk_id: str
    t0: float
    t1: float
    allowed_scene_routes: list[str] = Field(default_factory=lambda: list(ALLOWED_CLIP_ROUTES))
    global_map_ref: ChunkMapRef = Field(default_factory=ChunkMapRef)
    continuity_in: ContinuityIn = Field(default_factory=ContinuityIn)
    creative_note: str = ""
    identity_lock: bool = True
    world_lock: bool = True
    style_lock: bool = True


class ClipScene(BaseModel):
    scene_id: str
    t0: float
    t1: float
    section_type: str
    route: str
    goal: str
    continuity_tokens: list[str] = Field(default_factory=list)
    is_boundary_scene: bool = False
    recurring_group_id: str | None = None
    frame_prompt: str | None = None
    camera_prompt: str | None = None
    motion_prompt: str | None = None
    first_frame_prompt: str | None = None
    last_frame_prompt: str | None = None
    transition_prompt: str | None = None


class ChunkStoryboardResponse(BaseModel):
    track_id: str
    mode: str
    chunk_id: str
    t0: float
    t1: float
    continuity_out: dict[str, Any] = Field(default_factory=dict)
    scenes: list[ClipScene] = Field(default_factory=list)


@dataclass
class MergeIssue:
    code: str
    message: str
    chunk_left: str | None = None
    chunk_right: str | None = None


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
    if not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
    out = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            out.append(part.get("text") or "")
    return "\n".join(out).strip()


def _extract_json(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    first, last = text.find("{"), text.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except Exception:
            return None
    return None


def _call_gemini_json(*, api_key: str, prompt: str, retry_count: int = 3) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error = "gemini_invalid_json"
    diagnostics: dict[str, Any] = {"model": CLIP_PIPELINE_MODEL, "retries": 0}
    for attempt in range(retry_count):
        body = {
            "systemInstruction": {
                "parts": [{"text": "You are a production clip storyboard planner. Return strict JSON only."}]
            },
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 8192},
        }
        resp = post_generate_content(api_key, CLIP_PIPELINE_MODEL, body, timeout=120)
        diagnostics["retries"] = attempt
        if not isinstance(resp, dict) or resp.get("status") not in {None, 200}:
            last_error = f"gemini_http_error:{resp.get('status') if isinstance(resp, dict) else 'unknown'}"
            continue
        raw = _extract_gemini_text(resp)
        parsed = _extract_json(raw)
        if isinstance(parsed, dict):
            return parsed, diagnostics
        last_error = "gemini_invalid_or_truncated_json"
    raise ClipPipelineError("retryable_fail", "Gemini returned invalid JSON.", status_code=502, details={"reason": last_error, **diagnostics})


def _build_context(payload: dict[str, Any]) -> dict[str, Any]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    return {
            "state": "uploaded/cached",
        "audio_source": "master_track",
        "audio_url": str(source.get("source_value") or payload.get("audioUrl") or "").strip(),
        "refs_by_role": {
            role: ((refs.get(role) or {}).get("refs") if isinstance(refs.get(role), dict) else [])
            for role in ("character_1", "character_2", "character_3", "animal", "group", "location", "style", "props")
        },
        "system_instruction": "clip mode music video production storyboard",
    }


def _validate_chunk_response(chunk: ChunkStoryboardResponse) -> None:
    if chunk.mode != "clip":
        raise ClipPipelineError("retryable_fail", "chunk mode is not clip", details={"chunk_id": chunk.chunk_id})
    if not chunk.scenes:
        raise ClipPipelineError("retryable_fail", "empty scenes", details={"chunk_id": chunk.chunk_id})
    for scene in chunk.scenes:
        if scene.route not in ALLOWED_CLIP_ROUTES:
            raise ClipPipelineError("retryable_fail", "invalid scene route", details={"route": scene.route, "chunk_id": chunk.chunk_id})
        if scene.t1 <= scene.t0:
            raise ClipPipelineError("retryable_fail", "invalid scene timestamps", details={"scene_id": scene.scene_id, "chunk_id": chunk.chunk_id})
        if scene.route in {"i2v", "ia2v"}:
            if not (str(scene.frame_prompt or "").strip() and str(scene.camera_prompt or "").strip() and str(scene.motion_prompt or "").strip()):
                raise ClipPipelineError("retryable_fail", "missing i2v/ia2v prompts", details={"scene_id": scene.scene_id, "chunk_id": chunk.chunk_id})
        if scene.route == "first_last":
            if not (str(scene.first_frame_prompt or "").strip() and str(scene.last_frame_prompt or "").strip() and str(scene.transition_prompt or "").strip()):
                raise ClipPipelineError("retryable_fail", "missing first_last prompts", details={"scene_id": scene.scene_id, "chunk_id": chunk.chunk_id})


def _build_whole_track_map_prompt(payload: dict[str, Any], context: dict[str, Any]) -> str:
    return (
        "Return WholeTrackMapResponse JSON for clip mode only. "
        "No giant transcript. Keep lean map. Required keys: track_id, mode='clip', duration_sec, global_arc, "
        "world_lock, identity_lock, style_lock, sections[], no_split_ranges[], suggested_chunk_boundaries[]. "
        f"Runtime={json.dumps({'content_type': ((payload.get('director_controls') or {}).get('contentType') if isinstance(payload.get('director_controls'), dict) else ''), 'audio_url': context.get('audio_url'), 'audio_duration_sec': payload.get('audioDurationSec'), 'refs_by_role': context.get('refs_by_role')}, ensure_ascii=False)}"
    )


def _build_chunk_prompt(*, req: ChunkStoryboardRequest, whole_map: WholeTrackMapResponse, context: dict[str, Any]) -> str:
    runtime = {
            "context": {"audio_url": context.get("audio_url"), "system_instruction": context.get("system_instruction")},
            "whole_track_map": whole_map.model_dump(mode="json"),
        "chunk_request": req.model_dump(mode="json"),
    }
    return (
        "Return ChunkStoryboardResponse JSON for CLIP mode only. "
        "Allowed route only: i2v, ia2v, first_last. No transcript/audioStructure/semanticTimeline. "
        "Return keys: track_id, mode='clip', chunk_id, t0, t1, continuity_out, scenes[]. "
        f"Runtime={json.dumps(runtime, ensure_ascii=False)}"
    )


def _plan_chunks(whole_map: WholeTrackMapResponse) -> list[ChunkBoundary]:
    if whole_map.suggested_chunk_boundaries:
        return whole_map.suggested_chunk_boundaries
    step = 30.0
    chunks: list[ChunkBoundary] = []
    t = 0.0
    idx = 1
    while t < whole_map.duration_sec:
        end = min(whole_map.duration_sec, t + step)
        chunks.append(ChunkBoundary(chunk_id=f"ch_{idx:03d}", t0=round(t, 3), t1=round(end, 3)))
        t = max(t + step - 3.0, end)
        idx += 1
    return chunks


def _local_merge(track_id: str, responses: list[ChunkStoryboardResponse]) -> tuple[dict[str, Any], list[MergeIssue]]:
    issues: list[MergeIssue] = []
    ordered = sorted(responses, key=lambda x: (x.t0, x.t1, x.chunk_id))
    merged_scenes: list[dict[str, Any]] = []
    chunk_scene_map: dict[str, list[str]] = {}
    prev_end = 0.0
    for idx, chunk in enumerate(ordered):
        chunk_scene_map[chunk.chunk_id] = []
        if chunk.t0 > prev_end + 0.25 and idx > 0:
            issues.append(MergeIssue(code="gap", message="gap between chunks", chunk_left=ordered[idx - 1].chunk_id, chunk_right=chunk.chunk_id))
        if chunk.t0 < prev_end - 0.25 and idx > 0:
            issues.append(MergeIssue(code="overlap", message="overlap between chunks", chunk_left=ordered[idx - 1].chunk_id, chunk_right=chunk.chunk_id))
        for scene in sorted(chunk.scenes, key=lambda s: (s.t0, s.t1, s.scene_id)):
            if merged_scenes:
                prev = merged_scenes[-1]
                if abs(float(prev.get("t0", 0.0)) - scene.t0) < 0.01 and abs(float(prev.get("t1", 0.0)) - scene.t1) < 0.01:
                    continue
            scene_payload = scene.model_dump(mode="json", exclude_none=True)
            merged_scenes.append(scene_payload)
            chunk_scene_map[chunk.chunk_id].append(scene.scene_id)
        prev_end = max(prev_end, chunk.t1)
    merged = {
        "track_id": track_id,
            "mode": "clip",
        "scenes": merged_scenes,
        "chunk_scene_map": chunk_scene_map,
    }
    return merged, issues


def _build_repair_prompt(*, merged: dict[str, Any], issues: list[MergeIssue]) -> str:
    issue_rows = [issue.__dict__ for issue in issues]
    return (
        "Repair only chunk-boundary logic. Do not rewrite full storyboard. "
        "First rewrite adjacent edge scenes, suggest transition scene only if strictly required. "
        "Return JSON with keys: repaired_chunk_edges[] and optional transition_scene. "
        f"Runtime={json.dumps({'issues': issue_rows, 'merged': merged}, ensure_ascii=False)}"
    )


def _run_optional_repair(*, api_key: str, merged: dict[str, Any], issues: list[MergeIssue]) -> dict[str, Any]:
    if not issues:
        return {"applied": False, "issues": []}
    parsed, _ = _call_gemini_json(api_key=api_key, prompt=_build_repair_prompt(merged=merged, issues=issues), retry_count=2)
    return {"applied": True, "issues": [issue.__dict__ for issue in issues], "result": parsed}


def run_clip_storyboard_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ClipPipelineError("fatal_fail", "GEMINI_API_KEY is missing for clip pipeline.", status_code=503)

    context = _build_context(payload)
    state_history = ["uploaded/cached"]
    try:
        whole_map_raw, map_diag = _call_gemini_json(api_key=api_key, prompt=_build_whole_track_map_prompt(payload, context))
        try:
            whole_map = WholeTrackMapResponse.model_validate(whole_map_raw)
        except ValidationError as exc:
            raise ClipPipelineError("retryable_fail", "invalid whole track map", details={"errors": exc.errors()}) from exc

        state_history.append("track_mapped")
        chunks = _plan_chunks(whole_map)
        state_history.append("chunks_planned")

        chunk_results: list[ChunkStoryboardResponse] = []
        continuity_tail = ContinuityTailState()
        for boundary in chunks:
            state_history.append("chunk_running")
            section_ids = [sec.section_id for sec in whole_map.sections if not (sec.t1 <= boundary.t0 or sec.t0 >= boundary.t1)]
            recurring_ids = list(dict.fromkeys([sec.recurring_group_id for sec in whole_map.sections if sec.recurring_group_id]))
            req = ChunkStoryboardRequest(
                track_id=whole_map.track_id,
                chunk_id=boundary.chunk_id,
                t0=boundary.t0,
                t1=boundary.t1,
                global_map_ref=ChunkMapRef(section_ids=section_ids, recurring_group_ids=[x for x in recurring_ids if x]),
                continuity_in=ContinuityIn(previous_chunk_id=chunk_results[-1].chunk_id if chunk_results else None, tail_state=continuity_tail),
                creative_note=str((payload.get("metadata") or {}).get("creativeNote") or "") if isinstance(payload.get("metadata"), dict) else "",
            )
            parsed_chunk, _ = _call_gemini_json(api_key=api_key, prompt=_build_chunk_prompt(req=req, whole_map=whole_map, context=context))
            try:
                chunk = ChunkStoryboardResponse.model_validate(parsed_chunk)
            except ValidationError as exc:
                raise ClipPipelineError("retryable_fail", "invalid chunk contract", details={"chunk_id": boundary.chunk_id, "errors": exc.errors()}) from exc
            _validate_chunk_response(chunk)
            continuity_tail = ContinuityTailState.model_validate(chunk.continuity_out if isinstance(chunk.continuity_out, dict) else {})
            chunk_results.append(chunk)
            state_history.append("chunk_done")

        state_history.append("merging")
        merged, issues = _local_merge(whole_map.track_id, chunk_results)
        repair_data = {"applied": False, "issues": []}
        if issues:
            state_history.append("repairing")
            repair_data = _run_optional_repair(api_key=api_key, merged=merged, issues=issues)

        state_history.append("complete")
        return {
            "ok": True,
            "mode": "clip",
            "pipeline": "clip_chunked_v1",
            "job": {
                "job_type": "storyboard_generation",
                "mode": "clip",
                "content_type": "music_video",
                "audio_source": "master_track",
                "allowed_scene_routes": list(ALLOWED_CLIP_ROUTES),
            },
            "state": state_history[-1],
            "state_history": state_history,
            "context": context,
            "whole_track_map": whole_map.model_dump(mode="json"),
            "chunks": [chunk.model_dump(mode="json", exclude_none=True) for chunk in chunk_results],
            "merged_storyboard": merged,
            "repair": repair_data,
            "meta": {"model": CLIP_PIPELINE_MODEL, "mapDiagnostics": map_diag},
        }
    except ClipPipelineError:
        state_history.append("retryable_fail")
        raise
    except Exception as exc:
        state_history.append("fatal_fail")
        raise ClipPipelineError("fatal_fail", "clip pipeline failed", status_code=500, details={"error": str(exc), "state_history": state_history}) from exc


def regenerate_clip_chunk(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ClipPipelineError("gemini_api_key_missing", "GEMINI_API_KEY is missing for clip regenerate.", status_code=503)

    global_map_payload = payload.get("whole_track_map") if isinstance(payload.get("whole_track_map"), dict) else {}
    chunk_payload = payload.get("chunk") if isinstance(payload.get("chunk"), dict) else {}
    if not global_map_payload:
        raise ClipPipelineError("whole_track_map_missing", "whole_track_map is required.")
    if not chunk_payload:
        raise ClipPipelineError("chunk_missing", "chunk is required.")

    whole_map = WholeTrackMapResponse.model_validate(global_map_payload)
    chunk_id = str(chunk_payload.get("chunk_id") or "").strip()
    if not chunk_id:
        raise ClipPipelineError("chunk_id_missing", "chunk.chunk_id is required.")
    t0 = float(chunk_payload.get("t0") or 0.0)
    t1 = float(chunk_payload.get("t1") or t0)
    continuity_in = chunk_payload.get("continuity_in") if isinstance(chunk_payload.get("continuity_in"), dict) else {}
    creative_note = str(chunk_payload.get("creative_note") or "").strip()

    req = ChunkStoryboardRequest(
        track_id=whole_map.track_id,
        chunk_id=chunk_id,
        t0=t0,
        t1=t1,
        continuity_in=ContinuityIn.model_validate(continuity_in),
        creative_note=creative_note,
        global_map_ref=ChunkMapRef(
            section_ids=[sec.section_id for sec in whole_map.sections if not (sec.t1 <= t0 or sec.t0 >= t1)],
            recurring_group_ids=list(dict.fromkeys([sec.recurring_group_id for sec in whole_map.sections if sec.recurring_group_id])),
        ),
    )
    parsed_chunk, diag = _call_gemini_json(api_key=api_key, prompt=_build_chunk_prompt(req=req, whole_map=whole_map, context=payload.get("context") if isinstance(payload.get("context"), dict) else {}))
    chunk = ChunkStoryboardResponse.model_validate(parsed_chunk)
    _validate_chunk_response(chunk)
    return {
        "ok": True,
            "mode": "clip",
            "state": "chunk_done",
        "chunk": chunk.model_dump(mode="json", exclude_none=True),
            "meta": {"model": CLIP_PIPELINE_MODEL, "diagnostics": diag},
    }
