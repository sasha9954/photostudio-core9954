import json
import logging
from typing import Any

from app.core.config import settings
from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)


FALLBACK_GEMINI_MODEL = "gemini-2.5-flash"


def _to_float(value: Any) -> float | None:
    try:
        n = float(value)
    except Exception:
        return None
    return n if n == n and n != float("inf") and n != float("-inf") else None


def _round_sec(value: float | None) -> float | None:
    return round(float(value), 3) if value is not None else None


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

    audio_story_mode = str(data.get("audioStoryMode") or "lyrics_music").strip().lower()
    if audio_story_mode not in {"lyrics_music", "music_only", "music_plus_text"}:
        audio_story_mode = "lyrics_music"

    return {
        "mode": mode,
        "output": output,
        "audioStoryMode": audio_story_mode,
        "stylePreset": str(data.get("stylePreset") or "realism").strip().lower(),
        "freezeStyle": bool(data.get("freezeStyle")),
        "text": str(data.get("text") or "").strip(),
        "audioUrl": str(data.get("audioUrl") or "").strip(),
        "audioDurationSec": _to_float(data.get("audioDurationSec")),
        "refsByRole": _clean_refs_by_role(data.get("refsByRole")),
        "storyControlMode": str(data.get("storyControlMode") or "").strip(),
        "storyMissionSummary": str(data.get("storyMissionSummary") or "").strip(),
        "timelineSource": str(data.get("timelineSource") or "").strip(),
        "narrativeSource": str(data.get("narrativeSource") or "").strip(),
    }


def _normalize_scene_timeline(scenes: list[dict[str, Any]], audio_duration_sec: float | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    safe_audio_duration = _to_float(audio_duration_sec)
    if safe_audio_duration is None or safe_audio_duration <= 0:
        total_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes)
        timeline_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in scenes) if scenes else 0.0
        return scenes, {
            "audioDurationSec": None,
            "timelineDurationSec": _round_sec(timeline_end),
            "sceneDurationTotalSec": _round_sec(total_sum),
            "sceneCount": len(scenes),
            "normalizationApplied": False,
            "normalizationReason": None,
            "timelineScale": 1.0,
        }

    original_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in scenes) if scenes else 0.0
    original_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes)
    needs_fix = original_end > (safe_audio_duration + 0.25)
    scale = 1.0
    reason = None
    if needs_fix and original_end > 0:
        scale = safe_audio_duration / original_end
        reason = f"timeline_scaled_to_audio:{_round_sec(original_end)}->{_round_sec(safe_audio_duration)}"

    normalized: list[dict[str, Any]] = []
    cursor = 0.0
    for idx, scene in enumerate(scenes):
        start = _to_float(scene.get("startSec"))
        end = _to_float(scene.get("endSec"))
        duration = _to_float(scene.get("durationSec"))

        if start is not None and end is not None and end >= start:
            next_start = start * scale if needs_fix else start
            next_end = end * scale if needs_fix else end
        else:
            guessed_duration = duration if duration is not None and duration > 0 else 0.0
            if guessed_duration <= 0 and safe_audio_duration > 0:
                guessed_duration = safe_audio_duration / max(1, len(scenes))
            next_start = cursor
            next_end = cursor + guessed_duration

        next_start = max(0.0, min(safe_audio_duration, next_start))
        next_end = max(next_start, min(safe_audio_duration, next_end))

        if idx == len(scenes) - 1:
            next_end = safe_audio_duration
            next_start = min(next_start, next_end)

        cursor = next_end
        normalized.append(
            {
                **scene,
                "startSec": _round_sec(next_start),
                "endSec": _round_sec(next_end),
                "durationSec": _round_sec(max(0.0, next_end - next_start)),
            }
        )

    normalized_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in normalized) if normalized else 0.0
    normalized_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in normalized)
    return normalized, {
        "audioDurationSec": _round_sec(safe_audio_duration),
        "timelineDurationSec": _round_sec(normalized_end),
        "sceneDurationTotalSec": _round_sec(normalized_sum),
        "sceneCount": len(normalized),
        "normalizationApplied": bool(reason),
        "normalizationReason": reason,
        "timelineScale": _round_sec(scale),
        "originalTimelineDurationSec": _round_sec(original_end),
        "originalSceneDurationTotalSec": _round_sec(original_sum),
    }


def build_comfy_planner_prompt(payload: dict[str, Any]) -> str:
    audio_story_mode = str(payload.get("audioStoryMode") or "lyrics_music").strip().lower()
    if audio_story_mode not in {"lyrics_music", "music_only", "music_plus_text"}:
        audio_story_mode = "lyrics_music"

    # DEBUG VALIDATION CHECKLIST (manual):
    # 1) lyrics_music -> same song with lyrics should produce story beats that follow lyrical meaning.
    # 2) music_only -> same song should avoid lyric-derived plot; beats follow rhythm/energy only.
    # 3) music_plus_text -> same song + separate TEXT storyline should follow TEXT storyline; audio drives pace/energy.
    audio_story_rules = (
        "AUDIO STORY MODE RULES (STRICT):\n"
        "- lyrics_music: lyrics semantics are explicitly allowed and should be used as a narrative driver when vocals exist. You may use lyrical meaning, verse/chorus structure, emotional lyrical phrases, and explicit lyrical motifs to shape scene goals and transitions. Build scenes from lyrics+music together, not from music alone.\n"
        "- music_only: ignore lyrical semantics completely. Do not derive plot, events, world, objects, characters, or story beats from sung words. Do not build storyline from vocal text and do not substitute musical analysis with lyric interpretation. Use only rhythm, tempo, energy, dynamics, pacing, and emotional contour. If vocals exist, treat vocals as musical texture/emotional signal, never as narrative source.\n"
        "- music_plus_text: lyrics semantics must be ignored completely. TEXT node is the narrative driver for plot/events/world/objects/characters/story beats. AUDIO controls pacing, scene timing, montage rhythm, energy and emotional modulation. If lyrics conflict with TEXT, ignore lyrics semantics and follow TEXT. If TEXT is empty, fall back to a neutral music-driven storyboard without lyrics meaning.\n"
        "- Non-compliance is an error: for music_only and music_plus_text never claim lyric semantics drove the story."
    )
    segmentation_rules = (
        "SCENE SEGMENTATION RULES (HIGHEST PRIORITY):\n"
        "- Scene boundaries must follow meaningful phrase endings and real transition points.\n"
        "- Prefer cuts at: (1) vocal/semantic phrase ending, (2) musical phrase ending, (3) clear energy/rhythm/arrangement transition, (4) end of a visual micro-action, (5) emotional intention change.\n"
        "- Never split into equal-sized time blocks (forbidden: mechanical 5s, 10s, or evenly spaced chunks).\n"
        "- Duration is only a guardrail, not the main segmentation driver.\n"
        "- Guardrails: avoid <2.0s unless there is a strong accent cut; avoid >8.0s unless one continuous meaningful phrase/action justifies it.\n"
        "- If a scene exceeds 8.0s, include an explicit justification in sceneNarrativeStep or continuity.\n"
        "- Boundaries should feel cinematic and natural, not grid-based.\n"
    )
    audio_mode_segmentation = (
        "AUDIO MODE SEGMENTATION FOCUS:\n"
        "- lyrics_music: boundaries can follow lyric/sentence/sung-line endings plus music transitions.\n"
        "- music_only: ignore lyrics meaning; boundaries follow musical phrasing, energy shifts, rhythmic transitions, and structure only.\n"
        "- music_plus_text: ignore lyrics meaning; boundaries follow musical phrasing + meaningful TEXT chunks, synced to transition points.\n"
    )

    return (
        "You are COMFY storyboard planner. Return strict JSON only.\n"
        "Fields: ok, planMeta, globalContinuity, scenes, warnings, errors, debug.\n"
        f"Selected audioStoryMode={audio_story_mode}.\n"
        f"{audio_story_rules}\n"
        f"{segmentation_rules}\n"
        f"{audio_mode_segmentation}\n"
        "AUDIO is primary source for rhythm, emotional contour, dramatic shifts and timing.\n"
        "If INPUT.audioDurationSec is provided and > 0, scene timeline MUST stay inside [0, audioDurationSec].\n"
        "TEXT is optional support that clarifies intent.\n"
        "REFS are optional anchors for character/location/style/props continuity.\n"
        "Each scene must include: sceneId,title,startSec,endSec,durationSec,sceneNarrativeStep,sceneGoal,storyMission,"
        "sceneOutputRule,primaryRole,secondaryRoles,continuity,imagePrompt,videoPrompt,refsUsed.\n"
        "Do NOT include runtime render-state fields in planner output (for example imageUrl, videoUrl, audioSliceUrl).\n"
        "Scenes should feel cinematic and watchable; avoid dry static actions unless story requires it.\n"
        "In debug include segmentationMode and segmentationReason briefly explaining why boundaries were selected.\n"
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


def _build_segmentation_debug(scenes: list[dict[str, Any]], audio_story_mode: str, timing_debug: dict[str, Any]) -> dict[str, Any]:
    durations = [max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes]
    avg_duration = (sum(durations) / len(durations)) if durations else 0.0
    max_duration = max(durations) if durations else 0.0
    min_duration = min(durations) if durations else 0.0
    short_count = sum(1 for d in durations if 0.0 < d < 2.0)
    long_count = sum(1 for d in durations if d > 8.0)

    suspicious_even_chunks = False
    if len(durations) >= 3 and avg_duration > 0:
        max_delta = max(abs(d - avg_duration) for d in durations)
        suspicious_even_chunks = max_delta <= 0.35

    mode_reason_map = {
        "lyrics_music": "semantic_and_vocal_phrases_with_music_transitions",
        "music_only": "music_phrase_energy_and_structure_transitions",
        "music_plus_text": "text_meaning_chunks_synced_to_music_transitions",
    }
    mode_reason = mode_reason_map.get(audio_story_mode, "music_driven_transitions")

    return {
        "averageSceneDurationSec": _round_sec(avg_duration),
        "maxSceneDurationSec": _round_sec(max_duration),
        "minSceneDurationSec": _round_sec(min_duration),
        "shortSceneCountUnder2Sec": short_count,
        "longSceneCountOver8Sec": long_count,
        "normalizationApplied": bool(timing_debug.get("normalizationApplied")),
        "normalizationReason": timing_debug.get("normalizationReason"),
        "segmentationMode": "phrase_transition_oriented",
        "segmentationReason": mode_reason,
        "suspiciousEqualChunking": suspicious_even_chunks,
    }


def run_comfy_plan(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_comfy_payload(payload)
    refs_presence = {k: len(v) for k, v in normalized["refsByRole"].items()}
    debug_signature = "COMFY_DEBUG_STEP_V1"
    module_file = __file__
    # TEMP HARD DEBUG STEP (REMOVE AFTER CONFIRMATION):
    # VERIFY EXACT FILE + EXACT MODEL for COMFY planner requests.
    hard_debug_disable_fallback = True
    logger.info(
        "[COMFY PLAN] request summary mode=%s output=%s style=%s audioStoryMode=%s",
        normalized["mode"],
        normalized["output"],
        normalized["stylePreset"],
        normalized["audioStoryMode"],
    )
    logger.info("[COMFY PLAN] text/audio/refs presence text=%s audio=%s refs=%s", bool(normalized["text"]), bool(normalized["audioUrl"]), refs_presence)
    logger.warning("[%s] run_comfy_plan entered module_file=%s", debug_signature, module_file)
    print(f"[{debug_signature}] ENTER run_comfy_plan")
    print(f"[{debug_signature}] FILE = {module_file}")

    api_key = (settings.GEMINI_API_KEY or "").strip()
    # TEMP DEBUG STEP: hard pin model to remove ambiguity for diagnostic run.
    requested_model = "gemini-2.5-flash"
    logger.warning("[%s] hard_requested_model=%s", debug_signature, requested_model)
    logger.warning("[%s] effective_model_before_request=%s", debug_signature, requested_model)
    print(f"[{debug_signature}] HARD MODEL = {requested_model}")
    if not api_key:
        return {"ok": False, "planMeta": {}, "globalContinuity": {}, "scenes": [], "warnings": [], "errors": ["GEMINI_API_KEY missing"], "debug": {"debugSignature": debug_signature, "moduleFile": module_file, "requestedModel": requested_model, "effectiveModel": None, "httpStatus": None, "rawPreview": "", "normalizedPayload": normalized, "fallbackFrom": None, "normalizedScenesCount": 0}}

    body = {
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
        "contents": [{"role": "user", "parts": [{"text": build_comfy_planner_prompt(normalized)}]}],
    }

    parsed, diagnostics = _call_gemini_plan(api_key, requested_model, body)
    warnings: list[str] = []
    errors: list[str] = []

    if not hard_debug_disable_fallback and diagnostics["httpStatus"] == 404 and requested_model != FALLBACK_GEMINI_MODEL:
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
    scenes, timing_debug = _normalize_scene_timeline(scenes, normalized.get("audioDurationSec"))
    segmentation_debug = _build_segmentation_debug(scenes, normalized.get("audioStoryMode") or "lyrics_music", timing_debug)
    if segmentation_debug.get("suspiciousEqualChunking"):
        warnings.append("segmentation_suspicious_equal_chunks")
    logger.info("[COMFY PLAN] normalized scenes count=%s", len(scenes))

    parsed_errors = parsed.get("errors") if isinstance(parsed.get("errors"), list) else []
    all_errors = parsed_errors + errors

    plan_meta = (
        {
            **({"mode": normalized["mode"], "output": normalized["output"], "stylePreset": normalized["stylePreset"], "audioStoryMode": normalized["audioStoryMode"]}),
            **(parsed.get("planMeta") if isinstance(parsed.get("planMeta"), dict) else {}),
        }
    )
    plan_meta.update({
        "audioDurationSec": timing_debug.get("audioDurationSec"),
        "timelineDurationSec": timing_debug.get("timelineDurationSec"),
        "sceneDurationTotalSec": timing_debug.get("sceneDurationTotalSec"),
    })

    result = {
        "ok": len(all_errors) == 0,
        "planMeta": plan_meta,
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
            "fallbackFrom": diagnostics.get("fallbackFrom"),
            "normalizedScenesCount": len(scenes),
            "timing": timing_debug,
            "segmentation": segmentation_debug,
        },
    }
    if timing_debug.get("normalizationApplied"):
        result["warnings"].append(str(timing_debug.get("normalizationReason") or "timeline_normalized_to_audio"))
    first_scene = scenes[0] if scenes else {}
    logger.info(
        "[%s] result ok=%s mode=%s output=%s style=%s audioStoryMode=%s scenes=%s warnings=%s errors=%s requestedModel=%s effectiveModel=%s httpStatus=%s firstSceneId=%s firstSceneTitle=%s",
        debug_signature,
        result["ok"],
        result.get("planMeta", {}).get("mode"),
        result.get("planMeta", {}).get("output"),
        result.get("planMeta", {}).get("stylePreset"),
        result.get("planMeta", {}).get("audioStoryMode"),
        len(scenes),
        len(result["warnings"]),
        len(result["errors"]),
        result["debug"].get("requestedModel"),
        result["debug"].get("effectiveModel"),
        result["debug"].get("httpStatus"),
        first_scene.get("sceneId") if isinstance(first_scene, dict) else None,
        first_scene.get("title") if isinstance(first_scene, dict) else None,
    )
    return result
