import json
import logging
from typing import Any

from app.core.config import settings
from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)


FALLBACK_GEMINI_MODEL = "gemini-2.5-flash"


def _clean_refs_by_role(refs_by_role: dict[str, Any] | None) -> dict[str, list[dict[str, str]]]:
    roles = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
    src = refs_by_role if isinstance(refs_by_role, dict) else {}
    out: dict[str, list[dict[str, str]]] = {}
    for role in roles:
        items = src.get(role)
        clean = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                clean.append({"url": url, "name": str(item.get("name") or "").strip()})
        out[role] = clean
    return out


def normalize_comfy_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    mode = str(data.get("mode") or "clip").strip().lower()
    if mode not in {"clip", "kino", "reklama", "scenario"}:
        mode = "clip"
    output = str(data.get("output") or "comfy image").strip().lower()
    if output not in {"comfy image", "comfy text"}:
        output = "comfy image"

    return {
        "mode": mode,
        "output": output,
        "stylePreset": str(data.get("stylePreset") or "realism").strip().lower(),
        "freezeStyle": bool(data.get("freezeStyle")),
        "text": str(data.get("text") or "").strip(),
        "audioUrl": str(data.get("audioUrl") or "").strip(),
        "refsByRole": _clean_refs_by_role(data.get("refsByRole")),
        "storyControlMode": str(data.get("storyControlMode") or "").strip(),
        "storyMissionSummary": str(data.get("storyMissionSummary") or "").strip(),
        "timelineSource": str(data.get("timelineSource") or "").strip(),
        "narrativeSource": str(data.get("narrativeSource") or "").strip(),
    }


def build_comfy_planner_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are COMFY storyboard planner. Return strict JSON only.\n"
        "Fields: ok, planMeta, globalContinuity, scenes, warnings, errors, debug.\n"
        "AUDIO is primary source for rhythm, emotional contour, dramatic shifts and timing.\n"
        "TEXT is optional support that clarifies intent.\n"
        "REFS are optional anchors for character/location/style/props continuity.\n"
        "Each scene must include: sceneId,title,startSec,endSec,durationSec,sceneNarrativeStep,sceneGoal,storyMission,"
        "sceneOutputRule,primaryRole,secondaryRoles,continuity,imagePrompt,videoPrompt,refsUsed.\n"
        "Do NOT include runtime render-state fields in planner output (for example imageUrl, videoUrl, audioSliceUrl).\n"
        "Scenes should feel cinematic and watchable; avoid dry static actions unless story requires it.\n"
        f"INPUT={json.dumps(payload, ensure_ascii=False)}"
    )


def _extract_text(resp: dict[str, Any]) -> str:
    try:
        parts = (((resp or {}).get("candidates") or [])[0].get("content") or {}).get("parts") or []
        text_parts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
        return "\n".join([x for x in text_parts if x])
    except Exception:
        return ""


def _extract_json(raw: str) -> dict[str, Any] | None:
    s = str(raw or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(s[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _call_gemini_plan(api_key: str, model: str, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    logger.info("[COMFY PLAN] gemini request start model=%s", model)
    resp = post_generate_content(api_key, model, body, timeout=120)
    raw = _extract_text(resp if isinstance(resp, dict) else {})
    raw_preview = raw[:3000] if raw else str((resp or {}).get("text") or "")[:3000]
    http_status = int(resp.get("status") or 0) if isinstance(resp, dict) and resp.get("__http_error__") else None
    diagnostics = {
        "requestedModel": model,
        "effectiveModel": model,
        "httpStatus": http_status,
        "rawPreview": raw_preview,
    }
    if isinstance(resp, dict) and resp.get("__http_error__"):
        logger.warning("[COMFY PLAN] gemini http error model=%s status=%s", model, resp.get("status"))
        return {"errors": ["gemini_http_error"], "debug": {"httpStatus": http_status}}, diagnostics

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("[COMFY PLAN] gemini invalid json model=%s", model)
        return {"errors": ["gemini_invalid_json"]}, diagnostics

    return parsed, diagnostics


def _normalize_scene(scene: dict[str, Any], idx: int) -> dict[str, Any]:
    src = scene if isinstance(scene, dict) else {}
    start_sec = src.get("startSec")
    end_sec = src.get("endSec")
    duration_sec = src.get("durationSec")

    if start_sec is None and isinstance(src.get("timeRange"), dict):
        start_sec = src["timeRange"].get("startSec")
        end_sec = src["timeRange"].get("endSec")
    if start_sec is None:
        start_sec = src.get("start")
    if end_sec is None:
        end_sec = src.get("end")

    try:
        start_n = float(start_sec) if start_sec is not None else None
    except Exception:
        start_n = None
    try:
        end_n = float(end_sec) if end_sec is not None else None
    except Exception:
        end_n = None
    try:
        duration_n = float(duration_sec) if duration_sec is not None else None
    except Exception:
        duration_n = None

    if duration_n is None and start_n is not None and end_n is not None:
        duration_n = max(0.0, end_n - start_n)

    refs_used = src.get("refsUsed")
    if not isinstance(refs_used, dict):
        refs_used = {}

    return {
        "sceneId": str(src.get("sceneId") or f"scene-{idx + 1}"),
        "title": str(src.get("title") or f"Scene {idx + 1}"),
        "startSec": start_n,
        "endSec": end_n,
        "durationSec": duration_n,
        "sceneNarrativeStep": str(src.get("sceneNarrativeStep") or ""),
        "sceneGoal": str(src.get("sceneGoal") or ""),
        "storyMission": str(src.get("storyMission") or ""),
        "sceneOutputRule": str(src.get("sceneOutputRule") or "scene image first"),
        "primaryRole": str(src.get("primaryRole") or "character_1"),
        "secondaryRoles": src.get("secondaryRoles") if isinstance(src.get("secondaryRoles"), list) else [],
        "continuity": str(src.get("continuity") or ""),
        "imagePrompt": str(src.get("imagePrompt") or ""),
        "videoPrompt": str(src.get("videoPrompt") or ""),
        "refsUsed": refs_used,
        # Runtime render-state fields are intentionally initialized outside planner contract.
        "imageUrl": "",
        "videoUrl": "",
    }


def run_comfy_plan(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_comfy_payload(payload)
    refs_presence = {k: len(v) for k, v in normalized["refsByRole"].items()}
    debug_signature = "COMFY_DEBUG_STEP_V1"
    module_file = __file__
    logger.info("[COMFY PLAN] request summary mode=%s output=%s style=%s", normalized["mode"], normalized["output"], normalized["stylePreset"])
    logger.info("[COMFY PLAN] text/audio/refs presence text=%s audio=%s refs=%s", bool(normalized["text"]), bool(normalized["audioUrl"]), refs_presence)
    logger.warning("[%s] run_comfy_plan entered module_file=%s", debug_signature, module_file)

    api_key = (settings.GEMINI_API_KEY or "").strip()
    # TEMP DEBUG STEP: hard pin model to verify exact model request in logs.
    requested_model = "gemini-2.5-flash"
    logger.warning("[%s] hard_requested_model=%s", debug_signature, requested_model)
    logger.warning("[%s] effective_model_before_request=%s", debug_signature, requested_model)
    if not api_key:
        return {"ok": False, "planMeta": {}, "globalContinuity": {}, "scenes": [], "warnings": [], "errors": ["GEMINI_API_KEY missing"], "debug": {"debugSignature": debug_signature, "moduleFile": module_file, "requestedModel": requested_model, "effectiveModel": None, "httpStatus": None, "rawPreview": "", "normalizedPayload": normalized}}

    body = {
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
        "contents": [{"role": "user", "parts": [{"text": build_comfy_planner_prompt(normalized)}]}],
    }

    parsed, diagnostics = _call_gemini_plan(api_key, requested_model, body)
    warnings: list[str] = []
    errors: list[str] = []

    if diagnostics["httpStatus"] == 404 and requested_model != FALLBACK_GEMINI_MODEL:
        logger.info("[COMFY PLAN] fallback_from=%s fallback_to=%s", requested_model, FALLBACK_GEMINI_MODEL)
        warnings.append(f"gemini_model_fallback:{requested_model}->{FALLBACK_GEMINI_MODEL}")
        parsed_fb, diagnostics_fb = _call_gemini_plan(api_key, FALLBACK_GEMINI_MODEL, body)
        diagnostics = {
            **diagnostics_fb,
            "requestedModel": requested_model,
            "effectiveModel": diagnostics_fb.get("effectiveModel") or FALLBACK_GEMINI_MODEL,
            "fallbackFrom": requested_model,
        }
        parsed = parsed_fb

    if diagnostics.get("httpStatus"):
        errors.append(f"gemini_http_error:{diagnostics['httpStatus']}")
    elif isinstance(parsed, dict) and "errors" in parsed and parsed.get("errors") == ["gemini_invalid_json"]:
        errors.append("gemini_invalid_json")
        parsed = {}

    raw_scenes = parsed.get("scenes") if isinstance(parsed.get("scenes"), list) else []
    scenes = [_normalize_scene(scene, idx) for idx, scene in enumerate(raw_scenes)]
    logger.info("[COMFY PLAN] normalized scenes count=%s", len(scenes))

    parsed_errors = parsed.get("errors") if isinstance(parsed.get("errors"), list) else []
    all_errors = parsed_errors + errors

    result = {
        "ok": len(all_errors) == 0,
        "planMeta": parsed.get("planMeta") if isinstance(parsed.get("planMeta"), dict) else {"mode": normalized["mode"], "output": normalized["output"], "stylePreset": normalized["stylePreset"]},
        "globalContinuity": parsed.get("globalContinuity") if isinstance(parsed.get("globalContinuity"), (dict, str)) else {},
        "scenes": scenes,
        "warnings": (parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []) + warnings,
        "errors": all_errors,
        "debug": {
            **(parsed.get("debug") if isinstance(parsed.get("debug"), dict) else {}),
            "debugSignature": debug_signature,
            "moduleFile": module_file,
            "requestedModel": diagnostics.get("requestedModel") or requested_model,
            "effectiveModel": diagnostics.get("effectiveModel") or requested_model,
            "httpStatus": diagnostics.get("httpStatus"),
            "rawPreview": diagnostics.get("rawPreview") or "",
            "normalizedPayload": normalized,
        },
    }
    first_scene = scenes[0] if scenes else {}
    logger.info(
        "[COMFY PLAN] result ok=%s scenes=%s warnings=%s errors=%s normalizedScenesCount=%s firstSceneId=%s firstSceneTitle=%s",
        result["ok"],
        len(scenes),
        len(result["warnings"]),
        len(result["errors"]),
        len(scenes),
        first_scene.get("sceneId") if isinstance(first_scene, dict) else None,
        first_scene.get("title") if isinstance(first_scene, dict) else None,
    )
    return result
