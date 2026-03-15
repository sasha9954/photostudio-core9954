from __future__ import annotations

import os
import tempfile
from typing import Any
from urllib.parse import urlparse

import requests

from app.core.static_paths import ASSETS_DIR
from app.engine.audio_analyzer import analyze_audio

COMFY_REF_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
CAST_ROLES = ["character_1", "character_2", "character_3", "animal", "group"]


def _to_float(value: Any) -> float | None:
    try:
        n = float(value)
        if n != n or n in (float("inf"), float("-inf")):
            return None
        return n
    except Exception:
        return None


def _round(value: float) -> float:
    return round(float(value), 3)


def _resolve_audio_asset_path(audio_url: str) -> str | None:
    parsed = urlparse(audio_url or "")
    path = parsed.path or ""
    if path.startswith("/static/assets/"):
        filename = os.path.basename(path[len("/static/assets/"):])
    elif path.startswith("/assets/"):
        filename = os.path.basename(path[len("/assets/"):])
    else:
        return None
    if not filename:
        return None
    base = os.path.splitext(filename)[0]
    candidates = [filename, base, f"{base}.mp3", f"{base}.wav", f"{base}.ogg", f"{base}.m4a"]
    for name in candidates:
        p = os.path.join(str(ASSETS_DIR), name)
        if os.path.isfile(p):
            return p
    return None


def _load_audio_analysis(audio_url: str, audio_duration_sec: float | None) -> tuple[dict[str, Any], dict[str, Any]]:
    debug = {"audioUrl": audio_url or None, "source": "none", "error": None}
    fallback_duration = _to_float(audio_duration_sec) or 30.0

    if not audio_url:
        return {
            "duration": fallback_duration,
            "bpm": 0.0,
            "beats": [],
            "downbeats": [],
            "bars": [],
            "vocalPhrases": [],
            "energyPeaks": [],
            "sections": [],
            "pausePoints": [],
            "phraseBoundaries": [],
        }, debug

    local_path = _resolve_audio_asset_path(audio_url)
    if local_path:
        try:
            analysis = analyze_audio(local_path)
            debug["source"] = "local_asset"
            return analysis, debug
        except Exception as exc:
            debug["error"] = f"local_analyze_failed:{str(exc)[:180]}"

    suffix = os.path.splitext(urlparse(audio_url).path)[1] or ".audio"
    temp_path = None
    try:
        response = requests.get(audio_url, timeout=30)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(response.content)
            temp_path = tmp.name
        analysis = analyze_audio(temp_path)
        debug["source"] = "http_download"
        return analysis, debug
    except Exception as exc:
        debug["error"] = f"http_analyze_failed:{str(exc)[:180]}"
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

    return {
        "duration": fallback_duration,
        "bpm": 0.0,
        "beats": [],
        "downbeats": [],
        "bars": [],
        "vocalPhrases": [],
        "energyPeaks": [],
        "sections": [],
        "pausePoints": [],
        "phraseBoundaries": [],
    }, debug


def _extract_boundaries(duration: float, analysis: dict[str, Any]) -> list[float]:
    marks = {0.0, max(0.0, duration)}
    for sec in analysis.get("sections") or []:
        s = _to_float((sec or {}).get("start"))
        e = _to_float((sec or {}).get("end"))
        if s is not None:
            marks.add(max(0.0, min(duration, s)))
        if e is not None:
            marks.add(max(0.0, min(duration, e)))

    for phrase in analysis.get("vocalPhrases") or []:
        s = _to_float((phrase or {}).get("start"))
        e = _to_float((phrase or {}).get("end"))
        if s is not None:
            marks.add(max(0.0, min(duration, s)))
        if e is not None:
            marks.add(max(0.0, min(duration, e)))

    for pause in analysis.get("pausePoints") or []:
        t = _to_float(pause)
        if t is not None:
            marks.add(max(0.0, min(duration, t)))

    points = sorted(marks)
    compact: list[float] = [points[0]] if points else [0.0]
    for value in points[1:]:
        if value - compact[-1] >= 1.8:
            compact.append(value)
    if compact[-1] < duration:
        compact.append(duration)

    expanded: list[float] = [compact[0]]
    for idx in range(1, len(compact)):
        prev = expanded[-1]
        cur = compact[idx]
        span = cur - prev
        if span > 8.2:
            pieces = max(2, int(round(span / 5.0)))
            for k in range(1, pieces):
                expanded.append(prev + (span * k / pieces))
        expanded.append(cur)

    final = [round(min(duration, max(0.0, t)), 3) for t in expanded]
    dedup: list[float] = []
    for t in final:
        if not dedup or abs(t - dedup[-1]) > 0.3:
            dedup.append(t)
    if dedup[0] != 0.0:
        dedup.insert(0, 0.0)
    if dedup[-1] < duration:
        dedup.append(duration)
    return dedup


def _active_roles_for_scene(scene_idx: int, total: int, refs_by_role: dict[str, list[dict[str, str]]]) -> tuple[str, list[str], list[str], str]:
    available = [r for r in COMFY_REF_ROLES if refs_by_role.get(r)]
    cast = [r for r in CAST_ROLES if refs_by_role.get(r)]
    primary = cast[scene_idx % len(cast)] if cast else (available[0] if available else "character_1")

    refs_used: list[str] = []
    if primary:
        refs_used.append(primary)
    if refs_by_role.get("location") and (scene_idx == 0 or scene_idx % 2 == 0):
        refs_used.append("location")
    if refs_by_role.get("style") and scene_idx in {0, total - 1}:
        refs_used.append("style")
    if refs_by_role.get("props") and (scene_idx % 3 == 1):
        refs_used.append("props")

    secondary = [r for r in cast if r != primary][:2]
    if scene_idx % 4 != 0:
        secondary = secondary[:1]
    return primary, secondary, list(dict.fromkeys(refs_used)), "scene_role_rotation_with_ref_presence"


def _scene_type_for_window(start: float, end: float, scene_idx: int, total: int, has_vocal: bool, text_present: bool) -> str:
    if has_vocal:
        return "SING_CLOSEUP" if not text_present or scene_idx % 2 == 0 else "TALK_CLOSEUP"
    if scene_idx == 0:
        return "ATMOSPHERIC_WIDE"
    if scene_idx == total - 1:
        return "TRANSITION_SHOT"
    cycle = ["STORY_ACTION", "EMOTIONAL_REACTION", "DETAIL_INSERT", "ATMOSPHERIC_WIDE"]
    return cycle[scene_idx % len(cycle)]


def _pick_location(refs_by_role: dict[str, list[dict[str, str]]], scene_idx: int) -> str:
    loc_refs = refs_by_role.get("location") or []
    if loc_refs:
        return str((loc_refs[scene_idx % len(loc_refs)] or {}).get("name") or "anchored_location").strip() or "anchored_location"
    return "cinematic_world_main_location"


def plan_comfy_clip(payload: dict[str, Any]) -> dict[str, Any]:
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    analysis, audio_debug = _load_audio_analysis(str(payload.get("audioUrl") or ""), _to_float(payload.get("audioDurationSec")))
    duration = _to_float(analysis.get("duration")) or _to_float(payload.get("audioDurationSec")) or 30.0

    boundaries = _extract_boundaries(duration, analysis)
    scenes: list[dict[str, Any]] = []
    text = str(payload.get("text") or "").strip()
    story_mode = str(payload.get("audioStoryMode") or "lyrics_music").strip() or "lyrics_music"

    for idx in range(len(boundaries) - 1):
        start = boundaries[idx]
        end = boundaries[idx + 1]
        if end - start < 1.2:
            continue
        has_vocal = any(
            ((_to_float(p.get("start")) or 0.0) < end) and ((_to_float(p.get("end")) or 0.0) > start)
            for p in (analysis.get("vocalPhrases") or [])
        )
        primary, secondary, refs_used, selection_reason = _active_roles_for_scene(idx, len(boundaries) - 1, refs_by_role)
        location = _pick_location(refs_by_role, idx)
        scene_type = _scene_type_for_window(start, end, idx, len(boundaries) - 1, has_vocal, bool(text))

        purpose = "вокальная подача ключевой фразы" if scene_type == "SING_CLOSEUP" else (
            "герой проговаривает важную мысль" if scene_type == "TALK_CLOSEUP" else "развитие истории и атмосферы"
        )
        title = f"Сцена {idx + 1}: {scene_type.lower()}"
        continuity = f"Сохранять единый мир, цветовую логику и идентичность героев. Локация: {location}."
        base_subject = primary.replace("_", " ") if primary else "герой"

        image_prompt_ru = (
            f"Кинематографичный ключевой кадр: {base_subject} в сцене типа {scene_type}, локация {location}, "
            f"выразительная композиция, целостный свет и фактура, стиль {payload.get('stylePreset') or 'realism'}, высокая реалистичность."
        )
        video_prompt_ru = (
            f"В той же сцене {base_subject} выполняет осмысленное действие по сюжету; выражение лица и пластика тела меняются по эмоциональной дуге. "
            f"Окружение живёт: ветер/частицы/фоновые объекты двигаются естественно. Камера начинает со среднего плана, мягко "
            f"переводит акцент на героя и завершает кадр стабилизированным движением в киноязыке, сохраняя реализм и единый визуальный мир."
        )

        future_model = "sing_lipsync" if scene_type in {"SING_CLOSEUP", "TALK_CLOSEUP"} and has_vocal else "story_video"
        scene = {
            "sceneId": f"scene_{idx + 1:03d}",
            "title": title,
            "startSec": _round(start),
            "endSec": _round(end),
            "durationSec": _round(end - start),
            "anchorType": "vocal_phrase" if has_vocal else "section_change",
            "sceneType": scene_type,
            "purpose": purpose,
            "lyricText": "",
            "spokenText": "",
            "emotion": "intense" if has_vocal else "cinematic",
            "characters": [primary] + secondary,
            "location": location,
            "styleKey": str(payload.get("stylePreset") or "realism"),
            "futureRenderModel": future_model,
            "imagePromptRu": image_prompt_ru,
            "videoPromptRu": video_prompt_ru,
            "imagePromptEn": "",
            "videoPromptEn": "",
            "continuity": continuity,
            "sceneGoal": purpose,
            "sceneNarrativeStep": f"beat_{idx + 1}",
            "refsUsed": refs_used,
            "primaryRole": primary,
            "secondaryRoles": secondary,
            "roleSelectionReason": selection_reason,
            "refDirectives": {role: ("hero" if role == primary else "required") for role in refs_used},
            "heroEntityId": primary,
            "supportEntityIds": secondary,
            "mustAppear": [primary] if primary else [],
            "mustNotAppear": [role for role in COMFY_REF_ROLES if refs_by_role.get(role) and role not in refs_used and role not in secondary],
            "environmentLock": True,
            "styleLock": bool(payload.get("freezeStyle", False)),
            "identityLock": True,
        }
        scenes.append(scene)

    if not scenes:
        scenes = [{
            "sceneId": "scene_001",
            "title": "Сцена 1",
            "startSec": 0.0,
            "endSec": _round(duration),
            "durationSec": _round(duration),
            "anchorType": "text_story",
            "sceneType": "STORY_ACTION",
            "purpose": "базовый сторителлинг",
            "emotion": "cinematic",
            "characters": ["character_1"],
            "location": "cinematic_world_main_location",
            "styleKey": str(payload.get("stylePreset") or "realism"),
            "futureRenderModel": "story_video",
            "imagePromptRu": "Кинематографичный ключевой кадр с героем в цельном визуальном мире.",
            "videoPromptRu": "Герой действует в кадре, камера и окружение двигаются естественно в едином стиле.",
            "continuity": "Сохранять стиль, мир и идентичность персонажа.",
            "sceneGoal": "базовый сторителлинг",
            "sceneNarrativeStep": "beat_1",
            "refsUsed": [],
            "primaryRole": "character_1",
            "secondaryRoles": [],
            "refDirectives": {},
            "heroEntityId": "character_1",
            "supportEntityIds": [],
            "mustAppear": ["character_1"],
            "mustNotAppear": [],
            "environmentLock": True,
            "styleLock": bool(payload.get("freezeStyle", False)),
            "identityLock": True,
        }]

    world_bible = {
        "storyMode": story_mode,
        "visualStyle": str(payload.get("stylePreset") or "realism"),
        "cameraLanguage": "cinematic, motivated movement, emotional closeups with contextual wides",
        "continuityRules": "same production world, stable identity, coherent lighting and lens family",
        "characterBible": [role for role in CAST_ROLES if refs_by_role.get(role)] or ["character_1"],
        "locationBible": [str((item or {}).get("name") or "") for item in (refs_by_role.get("location") or []) if str((item or {}).get("name") or "")] or ["cinematic_world_main_location"],
        "propsBible": [str((item or {}).get("name") or "") for item in (refs_by_role.get("props") or []) if str((item or {}).get("name") or "")],
        "emotionalArc": "build → peak → resolve",
        "refUsageSummary": {role: len(refs_by_role.get(role) or []) for role in COMFY_REF_ROLES},
    }

    return {
        "ok": True,
        "worldBible": world_bible,
        "globalContinuity": world_bible.get("continuityRules"),
        "scenes": scenes,
        "warnings": [],
        "errors": [],
        "planMeta": {
            "mode": payload.get("mode", "clip"),
            "output": payload.get("output", "comfy image"),
            "stylePreset": payload.get("stylePreset", "realism"),
            "audioStoryMode": story_mode,
            "storyControlMode": payload.get("storyControlMode") or "",
            "storyMissionSummary": payload.get("storyMissionSummary") or "",
            "timelineSource": payload.get("timelineSource") or "audio_structure",
            "narrativeSource": payload.get("narrativeSource") or ("text" if text else "audio"),
            "audioDurationSec": _round(duration),
            "sceneDurationTotalSec": _round(sum(float(s.get("durationSec") or 0.0) for s in scenes)),
            "worldBible": world_bible,
            "summary": {"sceneCount": len(scenes)},
        },
        "debug": {
            "audio": audio_debug,
            "analysis": {
                "duration": analysis.get("duration"),
                "bpm": analysis.get("bpm"),
                "vocalPhraseCount": len(analysis.get("vocalPhrases") or []),
                "sectionCount": len(analysis.get("sections") or []),
                "energyPeakCount": len(analysis.get("energyPeaks") or []),
            },
            "boundaries": boundaries,
        },
    }
