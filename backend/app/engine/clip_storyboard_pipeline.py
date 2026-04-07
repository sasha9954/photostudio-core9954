import json
import logging
import os
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings
from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)

ALLOWED_CLIP_ROUTES = ("i2v", "ia2v", "first_last")
CLIP_PIPELINE_MODEL = "gemini-3.1-pro-preview"
WHOLE_MAP_RETRY_COUNT = 3
CHUNK_RETRY_COUNT = 3
REPAIR_RETRY_COUNT = 2
CLIP_REF_ROLES = ("character_1", "character_2", "character_3", "animal", "group", "location", "style", "props")


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


def _call_gemini_json(*, api_key: str, body: dict[str, Any], retry_count: int = 3) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error = "gemini_invalid_json"
    diagnostics: dict[str, Any] = {"model": CLIP_PIPELINE_MODEL, "retries": 0}
    for attempt in range(retry_count):
        resp = post_generate_content(api_key, CLIP_PIPELINE_MODEL, body, timeout=120)
        diagnostics["retries"] = attempt + 1
        if not isinstance(resp, dict) or resp.get("status") not in {None, 200}:
            last_error = f"gemini_http_error:{resp.get('status') if isinstance(resp, dict) else 'unknown'}"
            continue
        raw = _extract_gemini_text(resp)
        parsed = _extract_json(raw)
        if isinstance(parsed, dict):
            return parsed, diagnostics
        last_error = "gemini_invalid_or_truncated_json"
    raise ClipPipelineError("retryable_fail", "Gemini returned invalid JSON.", status_code=502, details={"reason": last_error, **diagnostics})


def _normalize_media_file_part(url: str, *, fallback_mime: str) -> dict[str, Any]:
    file_url = str(url or "").strip()
    if not file_url:
        return {}
    return {"fileData": {"mimeType": fallback_mime, "fileUri": file_url}}


def _extract_refs_by_role(payload: dict[str, Any]) -> dict[str, list[str]]:
    refs_out: dict[str, list[str]] = {role: [] for role in CLIP_REF_ROLES}
    connected = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    for role in CLIP_REF_ROLES:
        src = connected.get(role) if isinstance(connected.get(role), dict) else {}
        refs = [str(item).strip() for item in (src.get("refs") if isinstance(src.get("refs"), list) else []) if str(item).strip()]
        if refs:
            refs_out[role] = refs
    return refs_out


def _normalize_clip_context(payload: dict[str, Any], provided_context: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = provided_context if isinstance(provided_context, dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    audio_url = str(
        ctx.get("audio_url")
        or ((ctx.get("audio") or {}).get("audio_url") if isinstance(ctx.get("audio"), dict) else "")
        or source.get("source_value")
        or payload.get("audioUrl")
        or ""
    ).strip()
    refs_by_role = ctx.get("refs_by_role") if isinstance(ctx.get("refs_by_role"), dict) else _extract_refs_by_role(payload)
    audio_part = (ctx.get("audio") or {}).get("media_part") if isinstance(ctx.get("audio"), dict) else {}
    if not isinstance(audio_part, dict) or not audio_part:
        audio_part = _normalize_media_file_part(audio_url, fallback_mime="audio/mpeg")
    refs_media: dict[str, list[dict[str, Any]]] = {}
    for role in CLIP_REF_ROLES:
        role_refs = refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []
        role_parts: list[dict[str, Any]] = []
        for ref_url in role_refs[:6]:
            role_parts.append(_normalize_media_file_part(str(ref_url), fallback_mime="image/jpeg"))
        refs_media[role] = [part for part in role_parts if part]
    has_audio = bool(audio_part)
    has_refs = any(refs_media.get(role) for role in CLIP_REF_ROLES)
    state = "ready_with_media" if has_audio else "missing_audio_media"
    if has_audio and not has_refs:
        state = "ready_audio_only"
    elif has_audio and has_refs:
        state = "ready_with_media_refs"
    return {
        "state": state,
        "context_version": "clip_pipeline_v1",
        "is_reusable": has_audio,
        "audio_source": "master_track",
        "audio": {
            "audio_url": audio_url,
            "media_part": audio_part,
        },
        "audio_url": audio_url,
        "refs_by_role": refs_by_role,
        "refs_media_parts_by_role": refs_media,
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


def _flatten_ref_parts(ref_parts_by_role: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for role in ("character_1", "location", "style", "props", "character_2", "character_3", "animal", "group"):
        items = ref_parts_by_role.get(role) if isinstance(ref_parts_by_role.get(role), list) else []
        if items:
            out.append({"text": f"Reference role: {role}"})
        out.extend(items)
    return out


def _build_whole_track_map_request(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    runtime = {
        "content_type": ((payload.get("director_controls") or {}).get("contentType") if isinstance(payload.get("director_controls"), dict) else ""),
        "audio_duration_sec": payload.get("audioDurationSec"),
        "refs_roles_present": [role for role, refs in (context.get("refs_by_role") or {}).items() if isinstance(refs, list) and refs],
        "context_state": context.get("state"),
    }
    parts: list[dict[str, Any]] = [
        {"text": "Build WholeTrackMapResponse JSON for clip mode only."},
        {"text": "No giant transcript. Keep lean map with sections/no_split_ranges/suggested_chunk_boundaries."},
        {"text": f"Runtime={json.dumps(runtime, ensure_ascii=False)}"},
    ]
    audio_part = ((context.get("audio") or {}).get("media_part") if isinstance(context.get("audio"), dict) else {})
    if isinstance(audio_part, dict) and audio_part:
        parts.append({"text": "Master audio input:"})
        parts.append(audio_part)
    parts.extend(_flatten_ref_parts(context.get("refs_media_parts_by_role") if isinstance(context.get("refs_media_parts_by_role"), dict) else {}))
    return {
        "systemInstruction": {"parts": [{"text": str(context.get("system_instruction") or "You are a production clip storyboard planner. Return strict JSON only.")}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 8192},
    }


def _build_chunk_request(*, req: ChunkStoryboardRequest, whole_map: WholeTrackMapResponse, context: dict[str, Any]) -> dict[str, Any]:
    runtime = {
        "context": {"audio_url": context.get("audio_url"), "context_state": context.get("state")},
        "whole_track_map": whole_map.model_dump(mode="json"),
        "chunk_request": req.model_dump(mode="json"),
    }
    parts: list[dict[str, Any]] = [
        {"text": "Return ChunkStoryboardResponse JSON for CLIP mode only."},
        {"text": "Allowed routes only: i2v, ia2v, first_last. No transcript/audioStructure/semanticTimeline."},
        {"text": f"Runtime={json.dumps(runtime, ensure_ascii=False)}"},
    ]
    audio_part = ((context.get("audio") or {}).get("media_part") if isinstance(context.get("audio"), dict) else {})
    if isinstance(audio_part, dict) and audio_part:
        parts.append({"text": "Master audio context input:"})
        parts.append(audio_part)
    parts.extend(_flatten_ref_parts(context.get("refs_media_parts_by_role") if isinstance(context.get("refs_media_parts_by_role"), dict) else {}))
    return {
        "systemInstruction": {"parts": [{"text": str(context.get("system_instruction") or "You are a production clip storyboard planner. Return strict JSON only.")}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 8192},
    }


def _plan_chunks(whole_map: WholeTrackMapResponse) -> list[ChunkBoundary]:
    if whole_map.suggested_chunk_boundaries:
        return whole_map.suggested_chunk_boundaries
    step = 30.0
    overlap = 3.0
    chunks: list[ChunkBoundary] = []
    t = 0.0
    idx = 1
    no_split = [(float(r.t0), float(r.t1)) for r in whole_map.no_split_ranges if r.t1 > r.t0]
    guard = 0
    while t < whole_map.duration_sec:
        guard += 1
        if guard > 10000:
            break
        end = min(whole_map.duration_sec, t + step)
        for r0, r1 in no_split:
            if r0 < end < r1:
                end = min(whole_map.duration_sec, r1)
                break
        if end <= t + 0.01:
            end = min(whole_map.duration_sec, t + max(1.0, step / 2))
        chunks.append(ChunkBoundary(chunk_id=f"ch_{idx:03d}", t0=round(t, 3), t1=round(end, 3)))
        next_t = max(0.0, end - overlap)
        if next_t <= t:
            next_t = end
        t = next_t
        idx += 1
    return chunks


def _scene_prompt_signature(scene_payload: dict[str, Any]) -> str:
    source = " ".join(
        str(scene_payload.get(key) or "").strip().lower()
        for key in ("goal", "frame_prompt", "camera_prompt", "motion_prompt", "first_frame_prompt", "last_frame_prompt", "transition_prompt")
    )
    return " ".join(source.split())


def _are_similar_boundary_scenes(left: dict[str, Any], right: dict[str, Any]) -> bool:
    same_route = str(left.get("route") or "") == str(right.get("route") or "")
    same_group = str(left.get("recurring_group_id") or "") == str(right.get("recurring_group_id") or "")
    span_close = abs(float(left.get("t0") or 0.0) - float(right.get("t0") or 0.0)) < 0.75 and abs(float(left.get("t1") or 0.0) - float(right.get("t1") or 0.0)) < 0.75
    ratio = SequenceMatcher(None, _scene_prompt_signature(left), _scene_prompt_signature(right)).ratio()
    return same_route and same_group and span_close and ratio >= 0.92


def _local_merge(track_id: str, responses: list[ChunkStoryboardResponse]) -> tuple[dict[str, Any], list[MergeIssue], dict[str, Any]]:
    issues: list[MergeIssue] = []
    ordered = sorted(responses, key=lambda x: (x.t0, x.t1, x.chunk_id))
    merged_scenes: list[dict[str, Any]] = []
    chunk_scene_map: dict[str, list[str]] = {}
    diagnostics: dict[str, Any] = {"dedup_exact": 0, "dedup_similarity": 0, "boundary_normalized": 0, "overlap_adjustments": 0}
    fingerprints: set[tuple[str, int, int, str, str]] = set()
    prev_end = 0.0
    for idx, chunk in enumerate(ordered):
        chunk_scene_map[chunk.chunk_id] = []
        if chunk.t0 > prev_end + 0.25 and idx > 0:
            issues.append(MergeIssue(code="gap", message="gap between chunks", chunk_left=ordered[idx - 1].chunk_id, chunk_right=chunk.chunk_id))
        if chunk.t0 < prev_end - 0.25 and idx > 0:
            issues.append(MergeIssue(code="overlap", message="overlap between chunks", chunk_left=ordered[idx - 1].chunk_id, chunk_right=chunk.chunk_id))
        for scene in sorted(chunk.scenes, key=lambda s: (s.t0, s.t1, s.scene_id)):
            scene_payload = scene.model_dump(mode="json", exclude_none=True)
            fingerprint = (
                str(scene_payload.get("route") or ""),
                int(round(float(scene_payload.get("t0") or 0.0) * 100)),
                int(round(float(scene_payload.get("t1") or 0.0) * 100)),
                str(scene_payload.get("recurring_group_id") or ""),
                _scene_prompt_signature(scene_payload),
            )
            if fingerprint in fingerprints:
                diagnostics["dedup_exact"] += 1
                continue
            if merged_scenes:
                prev = merged_scenes[-1]
                if abs(float(prev.get("t0", 0.0)) - scene.t0) < 0.01 and abs(float(prev.get("t1", 0.0)) - scene.t1) < 0.01:
                    diagnostics["dedup_exact"] += 1
                    continue
                if _are_similar_boundary_scenes(prev, scene_payload):
                    diagnostics["dedup_similarity"] += 1
                    continue
                if float(scene_payload.get("t0") or 0.0) < float(prev.get("t1") or 0.0):
                    scene_payload["t0"] = round(float(prev.get("t1") or 0.0), 3)
                    diagnostics["overlap_adjustments"] += 1
            fingerprints.add(fingerprint)
            merged_scenes.append(scene_payload)
            chunk_scene_map[chunk.chunk_id].append(scene.scene_id)
        prev_end = max(prev_end, chunk.t1)
    for i in range(1, len(merged_scenes)):
        left = merged_scenes[i - 1]
        right = merged_scenes[i]
        if left.get("is_boundary_scene") and right.get("is_boundary_scene"):
            out_tokens = left.get("continuity_tokens") if isinstance(left.get("continuity_tokens"), list) else []
            in_tokens = right.get("continuity_tokens") if isinstance(right.get("continuity_tokens"), list) else []
            normalized = list(dict.fromkeys([str(x).strip() for x in out_tokens + in_tokens if str(x).strip()]))
            left["continuity_tokens"] = normalized
            right["continuity_tokens"] = normalized
            diagnostics["boundary_normalized"] += 1
    merged = {
        "track_id": track_id,
        "mode": "clip",
        "scenes": merged_scenes,
        "chunk_scene_map": chunk_scene_map,
    }
    return merged, issues, diagnostics


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
    body = {
        "systemInstruction": {"parts": [{"text": "You repair chunk-boundary scenes only. Return strict JSON only."}]},
        "contents": [{"role": "user", "parts": [{"text": _build_repair_prompt(merged=merged, issues=issues)}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json", "maxOutputTokens": 4096},
    }
    parsed, _ = _call_gemini_json(api_key=api_key, body=body, retry_count=REPAIR_RETRY_COUNT)
    return {"applied": True, "issues": [issue.__dict__ for issue in issues], "result": parsed}


def _apply_repair_result(merged: dict[str, Any], repair_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    scenes = [dict(scene) for scene in (merged.get("scenes") if isinstance(merged.get("scenes"), list) else [])]
    scene_index = {str(scene.get("scene_id") or ""): idx for idx, scene in enumerate(scenes) if str(scene.get("scene_id") or "").strip()}
    result = repair_data.get("result") if isinstance(repair_data.get("result"), dict) else {}
    edges = result.get("repaired_chunk_edges") if isinstance(result.get("repaired_chunk_edges"), list) else []
    applied_edges = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        left_scene = edge.get("left_scene") if isinstance(edge.get("left_scene"), dict) else {}
        right_scene = edge.get("right_scene") if isinstance(edge.get("right_scene"), dict) else {}
        left_id = str(left_scene.get("scene_id") or "").strip()
        right_id = str(right_scene.get("scene_id") or "").strip()
        if left_id and left_id in scene_index:
            scenes[scene_index[left_id]] = left_scene
            applied_edges += 1
        if right_id and right_id in scene_index:
            scenes[scene_index[right_id]] = right_scene
            applied_edges += 1
    transition_applied = False
    transition_scene = result.get("transition_scene") if isinstance(result.get("transition_scene"), dict) else {}
    if transition_scene and str(transition_scene.get("scene_id") or "").strip():
        trans_id = str(transition_scene.get("scene_id") or "").strip()
        if trans_id not in scene_index:
            insert_at = len(scenes)
            t0 = float(transition_scene.get("t0") or 0.0)
            for idx, scene in enumerate(scenes):
                if float(scene.get("t0") or 0.0) > t0:
                    insert_at = idx
                    break
            scenes.insert(insert_at, transition_scene)
            transition_applied = True
    return {**merged, "scenes": scenes}, {"edge_rewrites_applied": applied_edges, "transition_scene_applied": transition_applied}


def _generate_whole_map_with_retry(*, api_key: str, payload: dict[str, Any], context: dict[str, Any]) -> tuple[WholeTrackMapResponse, dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    last_error: ClipPipelineError | None = None
    for attempt in range(1, WHOLE_MAP_RETRY_COUNT + 1):
        req_body = _build_whole_track_map_request(payload, context)
        try:
            raw, call_diag = _call_gemini_json(api_key=api_key, body=req_body, retry_count=2)
            model = WholeTrackMapResponse.model_validate(raw)
            return model, call_diag, diagnostics
        except ValidationError as exc:
            reason = "invalid whole track map contract"
            diagnostics.append({"stage": "whole_map", "attempt": attempt, "reason": reason, "errors": exc.errors()})
            last_error = ClipPipelineError("retryable_fail", reason, details={"attempt": attempt, "errors": exc.errors()})
        except ClipPipelineError as exc:
            diagnostics.append({"stage": "whole_map", "attempt": attempt, "reason": exc.message})
            last_error = exc
    raise last_error or ClipPipelineError("retryable_fail", "invalid whole track map contract")


def _generate_chunk_with_retry(*, api_key: str, req: ChunkStoryboardRequest, whole_map: WholeTrackMapResponse, context: dict[str, Any]) -> tuple[ChunkStoryboardResponse, list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    last_error: ClipPipelineError | None = None
    for attempt in range(1, CHUNK_RETRY_COUNT + 1):
        try:
            body = _build_chunk_request(req=req, whole_map=whole_map, context=context)
            parsed_chunk, _ = _call_gemini_json(api_key=api_key, body=body, retry_count=2)
            chunk = ChunkStoryboardResponse.model_validate(parsed_chunk)
            _validate_chunk_response(chunk)
            return chunk, diagnostics
        except ValidationError as exc:
            diagnostics.append({"stage": "chunk", "chunk_id": req.chunk_id, "attempt": attempt, "reason": "invalid chunk contract", "errors": exc.errors()})
            last_error = ClipPipelineError("retryable_fail", "invalid chunk contract", details={"chunk_id": req.chunk_id, "attempt": attempt, "errors": exc.errors()})
        except ClipPipelineError as exc:
            diagnostics.append({"stage": "chunk", "chunk_id": req.chunk_id, "attempt": attempt, "reason": exc.message})
            last_error = exc
    raise last_error or ClipPipelineError("retryable_fail", "invalid chunk", details={"chunk_id": req.chunk_id})


def run_clip_storyboard_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ClipPipelineError("fatal_fail", "GEMINI_API_KEY is missing for clip pipeline.", status_code=503)

    context = _normalize_clip_context(payload)
    state_history = [context.get("state") or "context_prepared"]
    retry_diagnostics: list[dict[str, Any]] = []
    try:
        whole_map, map_diag, map_retry = _generate_whole_map_with_retry(api_key=api_key, payload=payload, context=context)
        retry_diagnostics.extend(map_retry)

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
            chunk, chunk_retry = _generate_chunk_with_retry(api_key=api_key, req=req, whole_map=whole_map, context=context)
            retry_diagnostics.extend(chunk_retry)
            continuity_tail = ContinuityTailState.model_validate(chunk.continuity_out if isinstance(chunk.continuity_out, dict) else {})
            chunk_results.append(chunk)
            state_history.append("chunk_done")

        state_history.append("merging")
        merged, issues, merge_diag = _local_merge(whole_map.track_id, chunk_results)
        repair_data = {"applied": False, "issues": []}
        repair_apply_diag = {"edge_rewrites_applied": 0, "transition_scene_applied": False}
        if issues:
            state_history.append("repairing")
            repair_data = _run_optional_repair(api_key=api_key, merged=merged, issues=issues)
            merged, repair_apply_diag = _apply_repair_result(merged, repair_data)

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
            "meta": {"model": CLIP_PIPELINE_MODEL, "mapDiagnostics": map_diag, "retryDiagnostics": retry_diagnostics, "mergeDiagnostics": merge_diag, "repairApplyDiagnostics": repair_apply_diag},
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
    context = _normalize_clip_context(payload, payload.get("context") if isinstance(payload.get("context"), dict) else None)
    chunk, retry_diag = _generate_chunk_with_retry(api_key=api_key, req=req, whole_map=whole_map, context=context)
    return {
        "ok": True,
        "mode": "clip",
        "state": "chunk_done",
        "chunk": chunk.model_dump(mode="json", exclude_none=True),
        "context": context,
        "meta": {"model": CLIP_PIPELINE_MODEL, "retryDiagnostics": retry_diag},
    }
