from __future__ import annotations

import os
import re
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
    weighted_marks: list[tuple[float, str, int]] = []

    def add_mark(raw: Any, reason: str, weight: int) -> None:
        t = _to_float(raw)
        if t is None:
            return
        clipped = max(0.0, min(duration, t))
        marks.add(clipped)
        weighted_marks.append((clipped, reason, weight))

    for sec in analysis.get("sections") or []:
        add_mark((sec or {}).get("start"), "section_start", 4)
        add_mark((sec or {}).get("end"), "section_end", 5)

    for phrase in analysis.get("vocalPhrases") or []:
        add_mark((phrase or {}).get("start"), "vocal_start", 3)
        add_mark((phrase or {}).get("end"), "vocal_end", 5)

    for boundary in analysis.get("phraseBoundaries") or []:
        add_mark(boundary, "phrase_boundary", 4)

    for pause in analysis.get("pausePoints") or []:
        add_mark(pause, "pause", 6)

    for downbeat in analysis.get("downbeats") or []:
        add_mark(downbeat, "downbeat", 2)

    for peak in analysis.get("energyPeaks") or []:
        add_mark(peak, "energy_peak", 1)

    score_by_bucket: dict[float, int] = {}
    for t, _, weight in weighted_marks:
        bucket = round(t, 1)
        score_by_bucket[bucket] = score_by_bucket.get(bucket, 0) + weight

    points = sorted(marks)
    compact: list[float] = [points[0]] if points else [0.0]
    for value in points[1:]:
        if value - compact[-1] >= 1.8:
            compact.append(value)
        else:
            prev_bucket = round(compact[-1], 1)
            cur_bucket = round(value, 1)
            if score_by_bucket.get(cur_bucket, 0) > score_by_bucket.get(prev_bucket, 0):
                compact[-1] = value
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
    return "SING_CLOSEUP" if has_vocal and not text_present else "STORY_ACTION"


def _split_text_beats(text: str, desired_count: int) -> list[str]:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean:
        return []
    parts = [p.strip(" -—–") for p in re.split(r"[\n\r]+|(?<=[.!?])\s+|\s*[;:•]\s*", clean) if p.strip()]
    if not parts:
        parts = [clean]
    if len(parts) >= desired_count:
        return parts[:desired_count]

    words = [w for w in clean.split(" ") if w]
    if len(words) < 5:
        return [clean]
    chunk = max(5, int(round(len(words) / max(1, desired_count))))
    built: list[str] = []
    for i in range(0, len(words), chunk):
        built.append(" ".join(words[i:i + chunk]))
    return built[:max(1, desired_count)]


def _phase_label(scene_idx: int, total: int) -> str:
    if total <= 1:
        return "opening"
    ratio = scene_idx / max(1, total - 1)
    if ratio < 0.22:
        return "opening"
    if ratio < 0.5:
        return "buildup"
    if ratio < 0.8:
        return "peak"
    return "release"


def _scene_type_for_story_window(
    *,
    start: float,
    end: float,
    scene_idx: int,
    total: int,
    analysis: dict[str, Any],
    text_beat: str,
    story_source: str,
) -> tuple[str, str]:
    phrase_overlap = any(((_to_float(p.get("start")) or 0.0) < end) and ((_to_float(p.get("end")) or 0.0) > start) for p in (analysis.get("vocalPhrases") or []))
    near_phrase_end = any(abs(((_to_float(p.get("end")) or -99.0) - end)) <= 0.45 for p in (analysis.get("vocalPhrases") or []))
    pause_near = any(start <= ((_to_float(p) or -99.0)) <= end for p in (analysis.get("pausePoints") or []))
    section_change = any(abs(((_to_float(s.get("start")) or -99.0) - start)) <= 0.45 for s in (analysis.get("sections") or []))
    phase = _phase_label(scene_idx, total)
    beat_lower = text_beat.lower()
    dialogue_feel = any(token in beat_lower for token in ["говор", "шеп", "сказ", "вопрос", "ответ", "dialog", "voice", "narrat"])
    performance_feel = any(token in beat_lower for token in ["пой", "припев", "крич", "sing", "chorus", "voice", "вокал"])
    atmosphere_feel = any(token in beat_lower for token in ["ноч", "ветер", "улиц", "дожд", "тиш", "atmos", "landscape", "city"])

    if scene_idx == 0:
        return ("ATMOSPHERIC_WIDE" if atmosphere_feel or not phrase_overlap else "STORY_ACTION"), phase
    if scene_idx == total - 1:
        return "TRANSITION_SHOT", phase
    if phrase_overlap and performance_feel and story_source != "audio_driven":
        return "SING_CLOSEUP", phase
    if phrase_overlap and (dialogue_feel or story_source == "text_driven"):
        return "TALK_CLOSEUP", phase
    if phrase_overlap and phase in {"buildup", "peak"}:
        return "EMOTIONAL_REACTION", phase
    if pause_near or near_phrase_end:
        return "TRANSITION_SHOT", phase
    if section_change and phase in {"opening", "release"}:
        return "ATMOSPHERIC_WIDE", phase
    if not phrase_overlap and phase == "peak":
        return "DETAIL_INSERT", phase
    if not phrase_overlap and phase in {"opening", "release"}:
        return "ATMOSPHERIC_WIDE", phase
    return "STORY_ACTION", phase


def _pick_location(refs_by_role: dict[str, list[dict[str, str]]], scene_idx: int) -> str:
    loc_refs = refs_by_role.get("location") or []
    if loc_refs:
        return str((loc_refs[scene_idx % len(loc_refs)] or {}).get("name") or "anchored_location").strip() or "anchored_location"
    return "cinematic_world_main_location"


def _scene_emotion(scene_type: str, phase: str, has_vocal: bool) -> str:
    if scene_type in {"SING_CLOSEUP", "TALK_CLOSEUP"}:
        return "expressive"
    if scene_type == "EMOTIONAL_REACTION":
        return "vulnerable"
    if scene_type == "TRANSITION_SHOT":
        return "reflective"
    if phase == "peak":
        return "intense"
    return "cinematic" if has_vocal else "atmospheric"


def _build_scene_prompts(
    *,
    scene_type: str,
    primary: str,
    location: str,
    style: str,
    emotion: str,
    beat_text: str,
    continuity_hint: str,
    phase: str,
    world_bible: dict[str, Any],
) -> tuple[str, str]:
    subject = primary.replace("_", " ") if primary else "герой"
    lens = str(world_bible.get("lensFamily") or "35mm и 50mm кинолинзы")
    light = str(world_bible.get("lightingLogic") or "мотивационный кинематографичный свет")
    color = str(world_bible.get("colorWorld") or "контрастная кинопалитра")
    beat = beat_text or "развитие истории"

    image_templates = {
        "SING_CLOSEUP": f"Кинематографичный ключевой кадр: крупный план {subject}, фокус на губах и глазах во время музыкальной фразы; локация {location}; {light}; {lens}; {color}; эмоция {emotion}; в кадре чувствуется момент: {beat}. {continuity_hint}",
        "TALK_CLOSEUP": f"Ключевой стоп-кадр сцены с {subject}: разговорный крупный/средне-крупный план, читаемая мимика и взгляд в осмысленной паузе; локация {location}; {light}; {lens}; {color}; драматургический подтекст: {beat}. {continuity_hint}",
        "ATMOSPHERIC_WIDE": f"Широкий атмосферный кино-кадр: {location}, {subject} как часть пространства, выразительная глубина и воздух; {light}; {lens}; {color}; визуальная стадия {phase}; смысл кадра: {beat}. {continuity_hint}",
        "DETAIL_INSERT": f"Детальный кинематографичный инсерт: важная фактура/жест {subject} в локации {location}; микрокомпозиция и предметный акцент; {light}; {lens}; {color}; эмоциональный подтекст {emotion}; смысл: {beat}. {continuity_hint}",
        "EMOTIONAL_REACTION": f"Эмоциональный ключевой кадр: {subject} в {location}, акцент на реакции лица и пластике тела; {light}; {lens}; {color}; момент внутреннего перелома: {beat}. {continuity_hint}",
        "TRANSITION_SHOT": f"Переходный кинока кадр: {subject} в {location}, ощущение смены состояния; композиция ведет к следующей сцене; {light}; {lens}; {color}; переходный смысл: {beat}. {continuity_hint}",
        "STORY_ACTION": f"Кинематографичный стоп-кадр действия: {subject} в {location} в момент сюжетного действия; выразительная композиция, читаемый силуэт; {light}; {lens}; {color}; драматургическая цель: {beat}. {continuity_hint}",
    }
    video_templates = {
        "SING_CLOSEUP": f"В этой же сцене {subject} исполняет фразу: артикуляция и дыхание синхронны эмоциональному пику, взгляд живо меняется; плечи и руки двигаются естественно; в окружении {location} работают частицы/дым/ткань; камера мягко подается с medium-close в close-up и фиксирует кульминацию, реалистично и кинематографично.",
        "TALK_CLOSEUP": f"В той же сцене {subject} проговаривает мысль/реплику: мимика, взгляд и микропауза раскрывают смысл; корпус слегка смещается, руки дают естественный акцент; фон {location} живет мягким движением света и воздуха; камера идет мотивированным долли-ин с короткой стабилизацией в конце.",
        "ATMOSPHERIC_WIDE": f"Сцена развивается в пространстве {location}: {subject} движется внутри кадра без спешки, среда реагирует ветром, дальними источниками света и фоновым движением; камера делает плавный establishing move с легким параллаксом, удерживая реалистичный cinematic tone.",
        "DETAIL_INSERT": f"В той же сцене акцент на деталь: {subject} выполняет точный микрожест (касание, сжатие, поворот), лицо выдает эмоцию {emotion}; предметы и фактуры вокруг оживают в микродвижении; камера работает как controlled macro push-in и фиксирует смысловой акцент.",
        "EMOTIONAL_REACTION": f"В том же моменте {subject} проживает внутреннюю реакцию: взгляд уходит, затем возвращается, дыхание меняет ритм, плечи и осанка перестраиваются; среда {location} отвечает изменением света/движения фона; камера держит мотивированный handheld/steady hybrid для живого драматического эффекта.",
        "TRANSITION_SHOT": f"Переход внутри той же сцены: {subject} завершает действие и входит в новое состояние, в {location} смещается свет и глубина фона; камера делает связующее движение (arc/slide) к точке выхода сцены, сохраняя непрерывность мира.",
        "STORY_ACTION": f"В той же сцене {subject} выполняет четкое сюжетное действие, пластика тела и лицо отражают {emotion}; в локации {location} есть естественное движение среды и второго плана; камера следует за действием мотивированным трекингом с мягкой сменой крупности, реалистично и кинематографично.",
    }
    return image_templates.get(scene_type, image_templates["STORY_ACTION"]), video_templates.get(scene_type, video_templates["STORY_ACTION"])


def plan_comfy_clip(payload: dict[str, Any]) -> dict[str, Any]:
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    analysis, audio_debug = _load_audio_analysis(str(payload.get("audioUrl") or ""), _to_float(payload.get("audioDurationSec")))
    duration = _to_float(analysis.get("duration")) or _to_float(payload.get("audioDurationSec")) or 30.0

    boundaries = _extract_boundaries(duration, analysis)
    scenes: list[dict[str, Any]] = []
    text = str(payload.get("text") or "").strip()
    story_mode = str(payload.get("audioStoryMode") or "lyrics_music").strip() or "lyrics_music"

    text_beats = _split_text_beats(text, max(1, len(boundaries) - 1))
    vocal_count = len(analysis.get("vocalPhrases") or [])
    story_source = "audio_driven"
    if text and vocal_count:
        story_source = "hybrid"
    elif text:
        story_source = "text_driven"

    world_bible = {
        "storyMode": story_mode,
        "visualStyle": str(payload.get("stylePreset") or "realism"),
        "cameraLanguage": "cinematic, motivated movement, emotional closeups with contextual wides",
        "lensFamily": "anamorphic-like 35/50/85 with selective wides",
        "lightingLogic": "motivated practicals + controlled key/fill, continuity across scenes",
        "productionFeel": "high-end music video realism with grounded texture",
        "colorWorld": "cohesive cinematic palette with controlled contrast and skin fidelity",
        "continuityRules": "same production world, stable identity, coherent lighting and lens family",
        "characterBible": [role for role in CAST_ROLES if refs_by_role.get(role)] or ["character_1"],
        "locationBible": [str((item or {}).get("name") or "") for item in (refs_by_role.get("location") or []) if str((item or {}).get("name") or "")] or ["cinematic_world_main_location"],
        "propsBible": [str((item or {}).get("name") or "") for item in (refs_by_role.get("props") or []) if str((item or {}).get("name") or "")],
        "emotionalArc": "build → peak → resolve",
        "refUsageSummary": {role: len(refs_by_role.get(role) or []) for role in COMFY_REF_ROLES},
    }

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
        beat_text = text_beats[idx % len(text_beats)] if text_beats else ""
        scene_type, phase = _scene_type_for_story_window(
            start=start,
            end=end,
            scene_idx=idx,
            total=max(1, len(boundaries) - 1),
            analysis=analysis,
            text_beat=beat_text,
            story_source=story_source,
        )

        if beat_text:
            purpose = f"раскрыть сюжетный бит: {beat_text[:140]}"
            scene_goal = f"визуально и эмоционально донести: {beat_text[:180]}"
            narrative_step = f"{phase}_beat_{idx + 1}"
            lyric_text = beat_text if scene_type == "SING_CLOSEUP" else ""
            spoken_text = beat_text if scene_type in {"TALK_CLOSEUP", "STORY_ACTION", "EMOTIONAL_REACTION"} else ""
        else:
            section_type = next(
                (
                    str((s or {}).get("type") or "")
                    for s in (analysis.get("sections") or [])
                    if ((_to_float((s or {}).get("start")) or -1.0) <= start < ((_to_float((s or {}).get("end")) or 10**9)))
                ),
                "section",
            )
            purpose = f"развить {section_type} фазу трека через {scene_type.lower()}"
            scene_goal = f"поддержать {phase} дугу истории по аудио-структуре"
            narrative_step = f"audio_{phase}_{idx + 1}"
            lyric_text = ""
            spoken_text = f"Аудио-ориентированный переход {phase} без текстовой опоры" if scene_type == "TALK_CLOSEUP" else ""

        title = f"Сцена {idx + 1}: {scene_type.lower()}"
        continuity = f"Сохранять единый мир, цветовую логику и идентичность героев. Локация: {location}."
        continuity += f" Фаза: {phase}. Источник истории: {story_source}."
        emotion = _scene_emotion(scene_type, phase, has_vocal)
        image_prompt_ru, video_prompt_ru = _build_scene_prompts(
            scene_type=scene_type,
            primary=primary,
            location=location,
            style=str(payload.get("stylePreset") or "realism"),
            emotion=emotion,
            beat_text=beat_text,
            continuity_hint="Соблюдать неизменность персонажей, костюма и мира.",
            phase=phase,
            world_bible=world_bible,
        )

        future_model = "sing_lipsync" if scene_type == "SING_CLOSEUP" else ("talk_or_lipsync" if scene_type == "TALK_CLOSEUP" else "story_video")
        phrase_end_near = any(abs(((_to_float(p.get("end")) or -99.0) - end)) <= 0.45 for p in (analysis.get("vocalPhrases") or []))
        pause_inside = any(start <= ((_to_float(pp) or -99.0)) <= end for pp in (analysis.get("pausePoints") or []))
        section_start_near = any(abs(((_to_float(s.get("start")) or -99.0) - start)) <= 0.45 for s in (analysis.get("sections") or []))
        anchor_type = "phrase_end" if phrase_end_near else ("pause_point" if pause_inside else ("section_change" if section_start_near else "audio_flow"))
        scene = {
            "sceneId": f"scene_{idx + 1:03d}",
            "title": title,
            "startSec": _round(start),
            "endSec": _round(end),
            "durationSec": _round(end - start),
            "anchorType": anchor_type,
            "sceneType": scene_type,
            "purpose": purpose,
            "lyricText": lyric_text,
            "spokenText": spoken_text,
            "emotion": emotion,
            "characters": [primary] + secondary,
            "location": location,
            "styleKey": str(payload.get("stylePreset") or "realism"),
            "futureRenderModel": future_model,
            "imagePromptRu": image_prompt_ru,
            "videoPromptRu": video_prompt_ru,
            "imagePromptEn": "",
            "videoPromptEn": "",
            "continuity": continuity,
            "sceneGoal": scene_goal,
            "sceneNarrativeStep": narrative_step,
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
            "lyricText": "",
            "spokenText": "",
            "refsUsed": [],
            "primaryRole": "character_1",
            "secondaryRoles": [],
            "roleSelectionReason": "fallback_single_scene",
            "refDirectives": {},
            "heroEntityId": "character_1",
            "supportEntityIds": [],
            "mustAppear": ["character_1"],
            "mustNotAppear": [],
            "environmentLock": True,
            "styleLock": bool(payload.get("freezeStyle", False)),
            "identityLock": True,
        }]

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
            "narrativeSource": payload.get("narrativeSource") or story_source,
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
                "pausePointCount": len(analysis.get("pausePoints") or []),
                "phraseBoundaryCount": len(analysis.get("phraseBoundaries") or []),
                "storySource": story_source,
            },
            "boundaries": boundaries,
        },
    }
