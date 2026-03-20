from __future__ import annotations

import os
import re
import tempfile
from typing import Any
from urllib.parse import urlparse

import requests

from app.core.static_paths import ASSETS_DIR
from app.engine.audio_analyzer import analyze_audio
from app.engine.audio_text_preprocessor import preprocess_audio_text

COMFY_REF_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
CAST_ROLES = ["character_1", "character_2", "character_3", "animal", "group"]
EXPLICIT_PEOPLE_TOKENS = [
    "man", "woman", "men", "women", "person", "people", "soldier", "soldiers", "worker", "workers", "guard", "guards",
    "scientist", "scientists", "engineer", "engineers", "commander", "operator", "operators", "crowd", "villager",
    "мужчина", "женщина", "люди", "человек", "солдат", "солдаты", "рабочий", "рабочие", "охранник", "учёный",
    "ученый", "инженер", "командир", "оператор", "группа людей", "персонаж", "герой",
]
INFRASTRUCTURE_TOKENS = [
    "bunker", "bunkers", "underground", "base", "bases", "tunnel", "tunnels", "corridor", "corridors", "facility",
    "facilities", "military", "missile", "launch", "launch site", "launch facility", "surveillance", "archive", "schematic",
    "map", "control room", "blast door", "steel door", "reinforced", "desert facility", "infrastructure", "iranian",
    "бункер", "бункеры", "подзем", "база", "базы", "туннел", "коридор", "шахта", "укреплен", "укреплён", "военн",
    "ракет", "пусков", "наблюдени", "архив", "схем", "карта", "командный пункт", "двер", "гермодвер", "инфраструктур",
]
SEMANTIC_OBJECT_HINTS = [
    {
        "tokens": ["desert", "sand", "dune", "песок", "песк", "песчан", "пустын"],
        "visual": "пустынный ландшафт, песчаные дюны, следы техники или разведки",
        "image": "пустыня, песчаная поверхность, следы техники, обзор местности сверху или с дальнего плана",
        "motion": "ветер гонит песок, камера ведёт разведывательный обзор территории",
        "sfx": "ветер в пустыне, песок по металлу, далёкий гул техники",
    },
    {
        "tokens": ["bunker", "бункер"],
        "visual": "укреплённый бункер, бетонные и стальные поверхности, защищённый вход",
        "image": "укреплённый бункер, армированный бетон, защитные конструкции, военная инфраструктура",
        "motion": "камера приближается к укреплённому входу или проходит вдоль массивных стен",
        "sfx": "глухой индустриальный гул, вентиляция, металлический резонанс",
    },
    {
        "tokens": ["tunnel", "туннел", "коридор", "shaft", "шахта"],
        "visual": "подземный тоннель или технологический коридор, коммуникации, кабели, глубина прохода",
        "image": "тоннель, подземный коридор, технические коммуникации, кабели, глубинная перспектива",
        "motion": "камера уходит в глубину тоннеля, свет и пыль движутся по коридору",
        "sfx": "эхо шагов, вентиляция, гул подземных систем",
    },
    {
        "tokens": ["blast door", "steel door", "door", "blastgate", "гермодвер", "двер", "ворота"],
        "visual": "тяжёлая гермодверь или взрывозащитные ворота, массивные механизмы запирания",
        "image": "тяжёлая взрывозащитная дверь, стальные запоры, герметичный вход",
        "motion": "дверь медленно открывается или камера останавливается перед её массой и механизмами",
        "sfx": "скрежет тяжёлого металла, гидравлика, низкий гул привода",
    },
    {
        "tokens": ["missile", "rocket", "ракет", "пусков"],
        "visual": "ракета, пусковая шахта или транспортно-пусковой контейнер как ключевой объект сцены",
        "image": "ракета или пусковая система, военный объект, инженерные детали",
        "motion": "камера раскрывает ракету или пусковую систему по вертикали или вдоль корпуса",
        "sfx": "металлический гул, предупреждающие сигналы, механика пускового узла",
    },
    {
        "tokens": ["satellite", "orbital", "спутник", "орбит"],
        "visual": "спутниковая разведка, орбитальный аппарат, экран с космическими данными или спутник в кадре",
        "image": "спутник, орбитальная техника, разведывательные данные, экран наблюдения",
        "motion": "на экране движутся спутниковые метки или камера следует за орбитальной схемой",
        "sfx": "электронные сигналы, тихий шум аппаратуры, пульс систем связи",
    },
    {
        "tokens": ["map", "schematic", "карта", "карт", "схем"],
        "visual": "карта местности, схема объекта, тактическая разметка, отметки входов и маршрутов",
        "image": "карта, схема, координаты, тактические пометки, аналитический стол или экран",
        "motion": "камера скользит по карте или слои схемы последовательно раскрывают объект",
        "sfx": "шорох бумаги или лёгкое жужжание экрана, щелчки интерфейса",
    },
    {
        "tokens": ["entrance", "entry", "вход", "въезд"],
        "visual": "замаскированный или защищённый вход в объект, дорога, рампа или контрольно-пропускная зона",
        "image": "вход в подземный объект, защищённая рампа, контрольно-пропускная точка",
        "motion": "камера раскрывает вход с воздуха или медленно подходит к нему по оси",
        "sfx": "ветер, далёкая техника, шум ворот и охранных систем",
    },
    {
        "tokens": ["facility", "base", "infrastructure", "underground", "facility", "объект", "база", "подзем", "инфраструктур"],
        "visual": "масштабный подземный или военный объект, инженерная инфраструктура, уровни и коммуникации",
        "image": "подземный объект, инженерная инфраструктура, защищённые уровни, документальная разведка",
        "motion": "камера связывает разные части объекта и показывает его масштаб и устройство",
        "sfx": "индустриальный гул, вентиляция, механические вибрации комплекса",
    },
]


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


def _extract_weighted_marks(duration: float, analysis: dict[str, Any]) -> tuple[list[float], list[tuple[float, str, int]], dict[float, int]]:
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
    return sorted(marks), weighted_marks, score_by_bucket


def _extract_boundaries(duration: float, analysis: dict[str, Any]) -> list[float]:
    points, _, score_by_bucket = _extract_weighted_marks(duration, analysis)

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


def _select_speech_boundaries(duration: float, analysis: dict[str, Any], desired_scene_count: int) -> list[float]:
    desired_scene_count = max(1, desired_scene_count)
    default = _extract_boundaries(duration, analysis)
    if desired_scene_count <= 1:
        return [0.0, round(duration, 3)]

    points, _, score_by_bucket = _extract_weighted_marks(duration, analysis)
    candidate_points = sorted({
        round(max(0.0, min(duration, p)), 3)
        for p in points
        if 0.65 < p < (duration - 0.65)
    })
    if not candidate_points:
        return default

    candidate_points.sort(key=lambda p: (-score_by_bucket.get(round(p, 1), 0), p))
    selected: list[float] = []
    min_gap = max(1.6, duration / max(2.0, desired_scene_count * 1.35))
    target_positions = [(duration * idx / desired_scene_count) for idx in range(1, desired_scene_count)]

    ordered_candidates = sorted(candidate_points, key=lambda point: (
        min(abs(point - target) for target in target_positions),
        -score_by_bucket.get(round(point, 1), 0),
        point,
    ))
    for point in ordered_candidates:
        if any(abs(point - existing) < min_gap for existing in selected):
            continue
        selected.append(point)
        if len(selected) >= desired_scene_count - 1:
            break

    if len(selected) < desired_scene_count - 1:
        fallback_candidates = sorted(candidate_points)
        for point in fallback_candidates:
            if any(abs(point - existing) < 1.2 for existing in selected):
                continue
            selected.append(point)
            if len(selected) >= desired_scene_count - 1:
                break

    boundaries = [0.0] + sorted(selected[:desired_scene_count - 1]) + [round(duration, 3)]
    dedup: list[float] = []
    for point in boundaries:
        if not dedup or abs(point - dedup[-1]) > 0.45:
            dedup.append(round(point, 3))
    if dedup[0] != 0.0:
        dedup.insert(0, 0.0)
    if dedup[-1] < duration:
        dedup.append(round(duration, 3))
    return dedup if len(dedup) >= 2 else default


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


def _contains_any_token(text: str, tokens: list[str]) -> bool:
    clean = _compact_text(text).lower()
    if not clean:
        return False
    for token in tokens:
        needle = _compact_text(token).lower()
        if not needle:
            continue
        if needle.isascii() and needle.replace(" ", "").isalpha():
            pattern = r"\b" + r"\s+".join(re.escape(part) for part in needle.split()) + r"\b"
            if re.search(pattern, clean):
                return True
            continue
        if needle in clean:
            return True
    return False


def _derive_characters_allowed(refs_by_role: dict[str, list[dict[str, str]]], combined_text: str) -> bool:
    if any(refs_by_role.get(role) for role in CAST_ROLES):
        return True
    return _contains_any_token(combined_text, EXPLICIT_PEOPLE_TOKENS)


def _pick_environment_subject(refs_by_role: dict[str, list[dict[str, str]]], semantic_text: str) -> str:
    if refs_by_role.get("location"):
        first = refs_by_role["location"][0] or {}
        label = _compact_text(first.get("name"))
        if label:
            return label
    if refs_by_role.get("props"):
        first = refs_by_role["props"][0] or {}
        label = _compact_text(first.get("name"))
        if label:
            return label
    if _contains_any_token(semantic_text, INFRASTRUCTURE_TOKENS):
        return "подземная военная инфраструктура"
    return "окружение и инфраструктура"


def _collect_semantic_scene_details(semantic_text: str, location: str) -> dict[str, str]:
    beat = _compact_text(semantic_text) or "документальный смысловой фрагмент"
    lowered = beat.lower()
    matched_visuals: list[str] = []
    matched_images: list[str] = []
    matched_motion: list[str] = []
    matched_sfx: list[str] = []

    for hint in SEMANTIC_OBJECT_HINTS:
        if any(_contains_any_token(lowered, [token]) for token in hint["tokens"]):
            matched_visuals.append(hint["visual"])
            matched_images.append(hint["image"])
            matched_motion.append(hint["motion"])
            matched_sfx.append(hint["sfx"])

    if not matched_visuals:
        if _contains_any_token(beat, INFRASTRUCTURE_TOKENS):
            matched_visuals.append("подземная или военная инфраструктура, инженерные детали, следы реальной эксплуатации")
            matched_images.append("документальная инфраструктурная среда, реальные инженерные детали")
            matched_motion.append("камера последовательно раскрывает устройство объекта")
            matched_sfx.append("низкий индустриальный фон, вентиляция, металлическое эхо")
        else:
            matched_visuals.append("конкретная среда и предметы, буквально соответствующие смыслу речи")
            matched_images.append("реальная сцена без абстрактной замены смысла")
            matched_motion.append("мотивированное движение камеры, связанное с действием или раскрытием места")
            matched_sfx.append("натуральный звук среды, подчёркивающий место и действие")

    subject = location if location and location != "cinematic_world_main_location" else _pick_environment_subject({"location": [], "props": []}, beat)
    desert_bias = any(_contains_any_token(lowered, [token]) for token in ["desert", "sand", "dune", "песк", "пустын"])
    visual_description = (
        f"Показать {subject} как буквальное воплощение фразы: {beat}. "
        f"В кадре должны быть {', '.join(dict.fromkeys(matched_visuals))}."
    )
    if desert_bias:
        camera_plan = (
            "Камера работает как воздушная или возвышенная разведка местности: сначала общий обзор пустынного рельефа, "
            f"затем снижение/приближение к ключевому объекту. Основной приём: {matched_motion[0]}."
        )
    else:
        camera_plan = (
            f"Камера работает как осмысленная разведка/наблюдение за сценой; начать с читаемого установочного ракурса и затем "
            f"уточнить ключевой объект. Основной приём: {matched_motion[0]}."
        )
    motion_plan = (
        f"Движение в кадре должно быть смысловым, а не декоративным: {', '.join(dict.fromkeys(matched_motion))}."
    )
    sfx_plan = (
        f"Звуковая среда сцены: {', '.join(dict.fromkeys(matched_sfx))}."
    )
    image_subject = "; ".join(dict.fromkeys(matched_images))
    motion_subject = "; ".join(dict.fromkeys(matched_motion))
    return {
        "sceneMeaning": beat,
        "visualDescription": visual_description,
        "cameraPlan": camera_plan,
        "motionPlan": motion_plan,
        "sfxPlan": sfx_plan,
        "imageSubject": image_subject,
        "motionSubject": motion_subject,
    }


def _resolve_scene_roles(
    *,
    scene_idx: int,
    total: int,
    refs_by_role: dict[str, list[dict[str, str]]],
    characters_allowed: bool,
    semantic_text: str,
) -> tuple[str, list[str], list[str], str]:
    if characters_allowed:
        return _active_roles_for_scene(scene_idx, total, refs_by_role)

    refs_used: list[str] = []
    primary = _pick_environment_subject(refs_by_role, semantic_text)
    if refs_by_role.get("location"):
        refs_used.append("location")
    if refs_by_role.get("props"):
        refs_used.append("props")
    if refs_by_role.get("style"):
        refs_used.append("style")
    return primary, [], list(dict.fromkeys(refs_used)), "environment_only_no_character_invention"


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


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _collect_text_inputs(payload: dict[str, Any]) -> tuple[str, str]:
    text_fields = [
        ("text", payload.get("text")),
        ("lyricsText", payload.get("lyricsText")),
        ("transcriptText", payload.get("transcriptText")),
        ("spokenTextHint", payload.get("spokenTextHint")),
        ("lyrics", payload.get("lyrics")),
        ("transcript", payload.get("transcript")),
    ]
    for key, value in text_fields:
        clean = _compact_text(value)
        if clean:
            return clean, key
    return "", ""


def _collect_semantic_hints(payload: dict[str, Any]) -> str:
    hint_sources = [
        payload.get("audioSemanticHints"),
        payload.get("semanticHints"),
        payload.get("audioSemanticSummary"),
        payload.get("spokenTextHint"),
    ]
    values: list[str] = []
    for src in hint_sources:
        if isinstance(src, str):
            clean = _compact_text(src)
            if clean:
                values.append(clean)
        elif isinstance(src, list):
            for item in src:
                clean = _compact_text(item)
                if clean:
                    values.append(clean)
        elif isinstance(src, dict):
            for key in ["summary", "keywords", "intent", "theme", "notes"]:
                value = src.get(key)
                if isinstance(value, list):
                    values.extend([_compact_text(v) for v in value if _compact_text(v)])
                else:
                    clean = _compact_text(value)
                    if clean:
                        values.append(clean)
    return " | ".join(dict.fromkeys([v for v in values if v]))[:500]


def _active_section_type(start: float, analysis: dict[str, Any]) -> str:
    for section in analysis.get("sections") or []:
        sec_start = _to_float((section or {}).get("start"))
        sec_end = _to_float((section or {}).get("end"))
        if sec_start is None or sec_end is None:
            continue
        if sec_start <= start < sec_end:
            return str((section or {}).get("type") or "").strip().lower() or "section"
    return "section"


def _scene_type_for_story_window(
    *,
    start: float,
    end: float,
    scene_idx: int,
    total: int,
    analysis: dict[str, Any],
    text_beat: str,
    story_source: str,
    section_type: str,
    prev_scene_type: str | None,
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

    strong_text_beat = len(beat_lower) >= 24
    section = section_type.lower()
    closeup_prev = prev_scene_type in {"SING_CLOSEUP", "TALK_CLOSEUP"}
    likely_chorus = any(token in section for token in ["chorus", "hook", "drop", "припев"])
    likely_verse = any(token in section for token in ["verse", "куплет"])
    likely_intro = any(token in section for token in ["intro", "интро", "start"])
    likely_bridge = any(token in section for token in ["bridge", "бридж", "break"])
    likely_outro = any(token in section for token in ["outro", "аутро", "ending"])
    is_climax = phase == "peak" and phrase_overlap and (performance_feel or likely_chorus)

    if scene_idx == 0:
        if likely_intro or atmosphere_feel or not phrase_overlap:
            return "ATMOSPHERIC_WIDE", phase
        return "STORY_ACTION", phase
    if scene_idx == total - 1:
        return "TRANSITION_SHOT", phase
    if pause_near or near_phrase_end:
        if phase in {"release", "opening"} or likely_bridge or likely_outro:
            return "TRANSITION_SHOT", phase
    if phrase_overlap and performance_feel and story_source != "audio_driven" and (likely_chorus or is_climax):
        return "SING_CLOSEUP", phase
    if phrase_overlap and (dialogue_feel or (story_source in {"text_driven", "hybrid", "lyrics_hint"} and strong_text_beat and likely_verse)):
        if closeup_prev and not is_climax:
            return "STORY_ACTION", phase
        return "TALK_CLOSEUP", phase
    if closeup_prev and not is_climax:
        if phrase_overlap:
            return "EMOTIONAL_REACTION", phase
        if phase in {"release", "opening"}:
            return "ATMOSPHERIC_WIDE", phase
        return "DETAIL_INSERT", phase
    if likely_chorus and phrase_overlap and phase in {"buildup", "peak"}:
        return "EMOTIONAL_REACTION", phase
    if likely_intro and phase == "opening":
        return "ATMOSPHERIC_WIDE", phase
    if likely_bridge and phase in {"buildup", "peak"}:
        return "DETAIL_INSERT", phase
    if likely_outro and phase == "release":
        return "TRANSITION_SHOT", phase
    if phrase_overlap and phase in {"buildup", "peak"}:
        return "EMOTIONAL_REACTION", phase
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


def _build_speech_scene_prompts(
    *,
    semantic_text: str,
    location: str,
    scene_idx: int,
    total: int,
    phase: str,
    characters_allowed: bool,
    world_bible: dict[str, Any],
    refs_used: list[str],
) -> tuple[str, str, dict[str, str]]:
    beat = semantic_text or "документальный смысловой фрагмент"
    lens = str(world_bible.get("lensFamily") or "35mm и 50mm кинолинзы")
    light = str(world_bible.get("lightingLogic") or "контролируемый индустриальный свет")
    color = str(world_bible.get("colorWorld") or "сдержанная документальная палитра")
    style_tag = "документальная реалистичность, инфраструктурная точность, без гламура"
    environment_focus = "среда, объекты и инфраструктура в приоритете" if not characters_allowed else "среда и действия должны оставаться предметно мотивированными"
    subject = location if location and location != "cinematic_world_main_location" else _pick_environment_subject({"location": [], "props": []}, beat)
    infrastructure_bias = _contains_any_token(beat, INFRASTRUCTURE_TOKENS)
    if infrastructure_bias:
        subject = location if location and location != "cinematic_world_main_location" else "подземный военный объект"
    scene_brief = _collect_semantic_scene_details(beat, location)
    world_continuity = f"Сохранять палитру, реализм и единый документальный мир. Фаза {phase}. Рефы: {', '.join(refs_used) if refs_used else 'environment-only'}."

    image_prompt = (
        f"Документальный кинематографичный кадр: {subject}; локация {location}; смысл сцены: {beat}; "
        f"визуально показать: {scene_brief['imageSubject']}; {environment_focus}; {light}; {lens}; {color}; {style_tag}. {world_continuity}"
    )

    video_actions = [
        f"камера медленно и осмысленно проходит через пространство {location}, раскрывая смысл фрагмента: {beat}; движение сцены: {scene_brief['motionSubject']}",
        f"пыль, воздух и дежурные источники света движутся внутри {location}, камера делает плавный push-in, подчёркивая тему: {beat}; движение сцены: {scene_brief['motionSubject']}",
        f"индустриальные огни и тени работают по усиленной документальной логике, камера панорамирует по {location}, удерживая фокус на теме: {beat}; движение сцены: {scene_brief['motionSubject']}",
        f"пространство {location} живёт сдержанным движением среды; камера делает мотивированный dolly/slide, чтобы раскрыть: {beat}; движение сцены: {scene_brief['motionSubject']}",
    ]
    if infrastructure_bias:
        video_actions = [
            f"камера медленно продвигается по {location}, показывая укреплённые поверхности и инженерные детали; движение напрямую поддерживает тему: {beat}; движение сцены: {scene_brief['motionSubject']}",
            f"тусклый промышленный свет мерцает по усиленным стенам {location}, пыль движется в воздухе, камера осторожно входит глубже в пространство, раскрывая: {beat}; движение сцены: {scene_brief['motionSubject']}",
            f"тяжёлые механические элементы {location} работают или застыли в ожидании; камера проводит документальную панораму, удерживая смысл: {beat}; движение сцены: {scene_brief['motionSubject']}",
            f"ветер, пыль или вентиляция создают реалистичное движение среды вокруг {location}; камера считывает инфраструктуру как главный персонаж сцены, раскрывая: {beat}; движение сцены: {scene_brief['motionSubject']}",
        ]
    video_prompt = video_actions[scene_idx % len(video_actions)] + f". Сохранять единый реалистичный тон и непрерывность мира от сцены к сцене."
    return image_prompt, video_prompt, scene_brief


def plan_comfy_clip(payload: dict[str, Any]) -> dict[str, Any]:
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    analysis, audio_debug = _load_audio_analysis(str(payload.get("audioUrl") or ""), _to_float(payload.get("audioDurationSec")))
    duration = _to_float(analysis.get("duration")) or _to_float(payload.get("audioDurationSec")) or 30.0

    scenes: list[dict[str, Any]] = []
    text, text_source = _collect_text_inputs(payload)
    story_mode = str(payload.get("audioStoryMode") or "lyrics_music").strip() or "lyrics_music"
    preprocess = preprocess_audio_text(payload=payload, analysis=analysis)

    normalized_lyrics = _compact_text(preprocess.get("lyricsText"))
    normalized_transcript = _compact_text(preprocess.get("transcriptText"))
    spoken_hint = _compact_text(preprocess.get("spokenTextHint"))
    semantic_summary = _compact_text(preprocess.get("audioSemanticSummary"))
    semantic_hint = _collect_semantic_hints({
        "audioSemanticHints": preprocess.get("audioSemanticHints"),
        "audioSemanticSummary": semantic_summary,
        "spokenTextHint": spoken_hint,
    })
    spoken_meaning_primary = story_mode == "speech_narrative"
    speech_text = _compact_text(" ".join([normalized_transcript, spoken_hint, semantic_summary]))
    supplemental_text = _compact_text(text if text_source == "text" else payload.get("text"))
    combined_meaning_text = _compact_text(" ".join([speech_text, supplemental_text, semantic_hint]))
    characters_allowed = _derive_characters_allowed(refs_by_role, combined_meaning_text)

    if spoken_meaning_primary:
        text = speech_text or text or semantic_hint
        text_source = "spoken_audio" if speech_text else (text_source or "semantic_fallback")
    elif not text:
        text = normalized_lyrics or normalized_transcript or spoken_hint
        if text:
            text_source = str(preprocess.get("textSource") or "existing_text")

    provisional_boundaries = _extract_boundaries(duration, analysis)
    provisional_scene_count = max(1, len(provisional_boundaries) - 1)
    text_beats = _split_text_beats(text, provisional_scene_count)
    if spoken_meaning_primary:
        desired_scene_count = max(1, len(text_beats) or len(_split_text_beats(speech_text or semantic_hint, provisional_scene_count)) or provisional_scene_count)
        boundaries = _select_speech_boundaries(duration, analysis, desired_scene_count)
        text_beats = _split_text_beats(text, max(1, len(boundaries) - 1))
    else:
        boundaries = provisional_boundaries
    exact_lyrics_available = bool(preprocess.get("exactLyricsAvailable"))
    transcript_available = bool(normalized_transcript)
    semantic_hint_count = len(preprocess.get("audioSemanticHints") or []) if isinstance(preprocess.get("audioSemanticHints"), list) else 0
    used_semantic_fallback = False

    vocal_count = len(analysis.get("vocalPhrases") or [])
    story_source = "audio_driven"
    if text and vocal_count:
        story_source = "hybrid"
    elif text:
        story_source = "lyrics_hint" if text_source in {"lyricsText", "lyrics"} else "text_driven"
    elif semantic_hint:
        story_source = "semantic_fallback"
        used_semantic_fallback = True
        text_source = str(preprocess.get("textSource") or "semantic_fallback")
        text_beats = _split_text_beats(semantic_hint, max(1, len(boundaries) - 1))

    if spoken_meaning_primary:
        story_source = "speech_narrative"

    prev_scene_type: str | None = None
    genre = str(payload.get("genre") or "").strip()

    world_bible = {
        "storyMode": story_mode,
        "genre": genre,
        "visualStyle": str(payload.get("stylePreset") or "realism"),
        "cameraLanguage": "documentary semantic continuity and motivated movement" if spoken_meaning_primary else "cinematic, motivated movement, emotional closeups with contextual wides",
        "lensFamily": "35mm/50mm documentary-priority lenses with selective wides" if spoken_meaning_primary else "anamorphic-like 35/50/85 with selective wides",
        "lightingLogic": "motivated industrial/documentary practicals with continuity across scenes" if spoken_meaning_primary else "motivated practicals + controlled key/fill, continuity across scenes",
        "productionFeel": "coherent documentary realism with grounded infrastructure detail" if spoken_meaning_primary else "high-end music video realism with grounded texture",
        "colorWorld": "muted documentary palette with controlled contrast and realistic materials" if spoken_meaning_primary else "cohesive cinematic palette with controlled contrast and skin fidelity",
        "continuityRules": "same documentary world, same palette, same realism level, same infrastructure logic across scenes" if spoken_meaning_primary else "same production world, stable identity, coherent lighting and lens family",
        "characterBible": [role for role in CAST_ROLES if refs_by_role.get(role)] if characters_allowed else [],
        "locationBible": [str((item or {}).get("name") or "") for item in (refs_by_role.get("location") or []) if str((item or {}).get("name") or "")] or ["cinematic_world_main_location"],
        "propsBible": [str((item or {}).get("name") or "") for item in (refs_by_role.get("props") or []) if str((item or {}).get("name") or "")],
        "emotionalArc": "semantic exposition → reveal → consequence" if spoken_meaning_primary else "build → peak → resolve",
        "refUsageSummary": {role: len(refs_by_role.get(role) or []) for role in COMFY_REF_ROLES},
        "charactersAllowed": characters_allowed,
        "spokenMeaningPrimary": spoken_meaning_primary,
    }

    scene_semantic_sources: list[dict[str, Any]] = []
    people_auto_added_count = 0

    for idx in range(len(boundaries) - 1):
        start = boundaries[idx]
        end = boundaries[idx + 1]
        if end - start < 1.2:
            continue
        has_vocal = any(
            ((_to_float(p.get("start")) or 0.0) < end) and ((_to_float(p.get("end")) or 0.0) > start)
            for p in (analysis.get("vocalPhrases") or [])
        )
        beat_text = text_beats[idx % len(text_beats)] if text_beats else ""
        semantic_slice = beat_text or (semantic_hint if spoken_meaning_primary else "")
        primary, secondary, refs_used, selection_reason = _resolve_scene_roles(
            scene_idx=idx,
            total=len(boundaries) - 1,
            refs_by_role=refs_by_role,
            characters_allowed=characters_allowed,
            semantic_text=semantic_slice,
        )
        location = _pick_location(refs_by_role, idx)
        section_type = _active_section_type(start, analysis)
        if spoken_meaning_primary:
            scene_type = "ATMOSPHERIC_WIDE" if idx == 0 else ("TRANSITION_SHOT" if idx == len(boundaries) - 2 else "STORY_ACTION")
            phase = _phase_label(idx, max(1, len(boundaries) - 1))
        else:
            scene_type, phase = _scene_type_for_story_window(
                start=start,
                end=end,
                scene_idx=idx,
                total=max(1, len(boundaries) - 1),
                analysis=analysis,
                text_beat=beat_text,
                story_source=story_source,
                section_type=section_type,
                prev_scene_type=prev_scene_type,
            )

        if beat_text:
            purpose = f"раскрыть сюжетный бит: {beat_text[:140]}"
            scene_goal = f"визуально и эмоционально донести: {beat_text[:180]}"
            narrative_step = f"{phase}_beat_{idx + 1}"
            lyric_text = beat_text if (not spoken_meaning_primary and scene_type == "SING_CLOSEUP" and exact_lyrics_available) else ""
            spoken_text = beat_text if (spoken_meaning_primary or (scene_type in {"TALK_CLOSEUP", "STORY_ACTION", "EMOTIONAL_REACTION"} and not exact_lyrics_available)) else ""
        else:
            purpose = f"развить {section_type} фазу трека через {scene_type.lower()}"
            scene_goal = f"поддержать {phase} дугу истории по аудио-структуре"
            narrative_step = f"audio_{phase}_{idx + 1}"
            lyric_text = ""
            spoken_text = f"Смысловой переход: {semantic_hint[:120]}" if (scene_type == "TALK_CLOSEUP" and semantic_hint) else ""

        if spoken_meaning_primary:
            scene_semantic_source = "transcript_segment" if normalized_transcript else ("spoken_hint" if spoken_hint else ("audio_semantic_summary" if semantic_summary else "supplemental_text"))
            if semantic_summary and beat_text and beat_text in semantic_summary:
                scene_semantic_source = "audioSemanticSummary"
        else:
            if lyric_text:
                scene_semantic_source = "lyricsText"
            elif spoken_text:
                scene_semantic_source = "spokenTextHint" if spoken_hint else "text"
            elif beat_text:
                scene_semantic_source = text_source or "text"
            elif semantic_hint:
                scene_semantic_source = "audioSemanticSummary"
            else:
                scene_semantic_source = "audio_structure"

        title = f"Сцена {idx + 1}: {scene_type.lower()}"
        continuity = f"Сохранять единый мир, цветовую логику и идентичность героев. Локация: {location}."
        continuity += f" Фаза: {phase}. Источник истории: {story_source}."
        if genre:
            continuity += f" Жанр: {genre}."
        if spoken_meaning_primary:
            continuity = f"Сохранять документальную/инфраструктурную логику мира, одну палитру, один уровень реализма и связанность локаций. Локация: {location}. Фаза: {phase}. Источник смысла: {scene_semantic_source}."
        emotion = _scene_emotion(scene_type, phase, has_vocal)
        scene_brief = {}
        if spoken_meaning_primary:
            image_prompt_ru, video_prompt_ru, scene_brief = _build_speech_scene_prompts(
                semantic_text=beat_text or semantic_hint,
                location=location,
                scene_idx=idx,
                total=max(1, len(boundaries) - 1),
                phase=phase,
                characters_allowed=characters_allowed,
                world_bible=world_bible,
                refs_used=refs_used,
            )
        else:
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

        future_model = "story_video" if spoken_meaning_primary else ("sing_lipsync" if scene_type in {"SING_CLOSEUP", "TALK_CLOSEUP"} else "story_video")
        phrase_end_near = any(abs(((_to_float(p.get("end")) or -99.0) - end)) <= 0.45 for p in (analysis.get("vocalPhrases") or []))
        pause_inside = any(start <= ((_to_float(pp) or -99.0)) <= end for pp in (analysis.get("pausePoints") or []))
        section_start_near = any(abs(((_to_float(s.get("start")) or -99.0) - start)) <= 0.45 for s in (analysis.get("sections") or []))
        anchor_type = "speech_pause_or_sentence" if (spoken_meaning_primary and pause_inside) else ("phrase_end" if phrase_end_near else ("pause_point" if pause_inside else ("section_change" if section_start_near else "audio_flow")))
        if not characters_allowed and any(role in CAST_ROLES for role in [primary] + secondary):
            people_auto_added_count += 1
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
            "sceneText": beat_text or semantic_hint,
            "sceneMeaning": scene_brief.get("sceneMeaning") or (beat_text or semantic_hint),
            "visualDescription": scene_brief.get("visualDescription") or beat_text or semantic_hint,
            "cameraPlan": scene_brief.get("cameraPlan") or "",
            "motionPlan": scene_brief.get("motionPlan") or "",
            "sfxPlan": scene_brief.get("sfxPlan") or "",
            "imagePromptRu": image_prompt_ru,
            "videoPromptRu": video_prompt_ru,
            "imagePromptEn": "",
            "videoPromptEn": "",
            "continuity": continuity,
            "sceneGoal": scene_goal,
            "sceneNarrativeStep": narrative_step,
            "sceneSemanticSource": scene_semantic_source,
            "refsUsed": refs_used,
            "primaryRole": primary,
            "secondaryRoles": secondary,
            "roleSelectionReason": selection_reason,
            "refDirectives": {role: ("required" if role in {"location", "props"} else ("optional" if role == "style" else "hero")) for role in refs_used},
            "heroEntityId": primary if primary in COMFY_REF_ROLES else "",
            "supportEntityIds": secondary,
            "mustAppear": [primary] if primary and primary in COMFY_REF_ROLES else refs_used,
            "mustNotAppear": [role for role in COMFY_REF_ROLES if refs_by_role.get(role) and role not in refs_used and role not in secondary],
            "environmentLock": True,
            "styleLock": bool(payload.get("freezeStyle", False)),
            "identityLock": bool(characters_allowed),
        }
        scenes.append(scene)
        scene_semantic_sources.append({
            "sceneId": scene["sceneId"],
            "sceneSemanticSource": scene_semantic_source,
            "semanticText": beat_text or semantic_hint,
        })
        prev_scene_type = scene_type

    if not scenes:
        fallback_characters = ["character_1"] if characters_allowed else []
        fallback_primary_role = "character_1" if characters_allowed else ""
        fallback_hero_entity_id = "character_1" if characters_allowed else ""
        fallback_must_appear = ["character_1"] if characters_allowed else []
        fallback_image_prompt_ru = (
            "Кинематографичный ключевой кадр с героем в цельном визуальном мире."
            if characters_allowed
            else "Документальный кинематографичный кадр среды и инфраструктуры в цельном визуальном мире."
        )
        fallback_video_prompt_ru = (
            "Герой действует в кадре, камера и окружение двигаются естественно в едином стиле."
            if characters_allowed
            else "Среда и инфраструктура живут в кадре с реалистичным мотивированным движением камеры и окружения."
        )
        fallback_image_prompt_en = (
            "A cinematic keyframe featuring the hero in a cohesive visual world."
            if characters_allowed
            else "A documentary cinematic shot of environment and infrastructure within a cohesive visual world."
        )
        fallback_video_prompt_en = (
            "The hero acts within the frame while camera and environment move naturally in a unified style."
            if characters_allowed
            else "Environment and infrastructure breathe within the frame with realistic motivated camera and ambient movement."
        )
        fallback_continuity = (
            "Сохранять стиль, мир и идентичность персонажа."
            if characters_allowed
            else "Сохранять стиль, мир и средовую целостность без персонажей в кадре."
        )
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
            "characters": fallback_characters,
            "location": "cinematic_world_main_location",
            "styleKey": str(payload.get("stylePreset") or "realism"),
            "futureRenderModel": "story_video",
            "sceneText": semantic_hint or text,
            "sceneMeaning": semantic_hint or text or "базовый сторителлинг",
            "visualDescription": semantic_hint or text or "Документальная сцена, прямо отражающая смысл аудио без абстрактной подмены.",
            "cameraPlan": "Осмысленный установочный ракурс с уточнением ключевого объекта сцены.",
            "motionPlan": "Мотивированное движение камеры и среды должно раскрывать смысл речи, а не только стиль.",
            "sfxPlan": "Натуральный атмосферный фон среды, связанный с местом и действием.",
            "imagePromptRu": fallback_image_prompt_ru,
            "videoPromptRu": fallback_video_prompt_ru,
            "imagePromptEn": fallback_image_prompt_en,
            "videoPromptEn": fallback_video_prompt_en,
            "continuity": fallback_continuity,
            "sceneGoal": "базовый сторителлинг",
            "sceneNarrativeStep": "beat_1",
            "sceneSemanticSource": "fallback",
            "lyricText": "",
            "spokenText": "",
            "refsUsed": [],
            "primaryRole": fallback_primary_role,
            "secondaryRoles": [],
            "roleSelectionReason": "fallback_single_scene",
            "refDirectives": {},
            "heroEntityId": fallback_hero_entity_id,
            "supportEntityIds": [],
            "mustAppear": fallback_must_appear,
            "mustNotAppear": [],
            "environmentLock": True,
            "styleLock": bool(payload.get("freezeStyle", False)),
            "identityLock": bool(characters_allowed),
        }]
        scene_semantic_sources.append({
            "sceneId": "scene_001",
            "sceneSemanticSource": "fallback",
            "semanticText": semantic_hint or text,
        })

    scene_type_histogram: dict[str, int] = {}
    closeup_scene_count = 0
    for item in scenes:
        st = str(item.get("sceneType") or "")
        if st:
            scene_type_histogram[st] = scene_type_histogram.get(st, 0) + 1
        if st in {"SING_CLOSEUP", "TALK_CLOSEUP"}:
            closeup_scene_count += 1

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
            "genre": genre,
            "storyControlMode": payload.get("storyControlMode") or "",
            "storyMissionSummary": payload.get("storyMissionSummary") or "",
            "timelineSource": payload.get("timelineSource") or "audio_structure",
            "narrativeSource": payload.get("narrativeSource") or story_source,
            "storySource": story_source,
            "textSource": str(preprocess.get("textSource") or text_source or "none"),
            "exactLyricsAvailable": exact_lyrics_available,
            "transcriptAvailable": transcript_available,
            "spokenMeaningPrimary": spoken_meaning_primary,
            "charactersAllowed": characters_allowed,
            "peopleAutoAddedCount": people_auto_added_count,
            "sceneSemanticSource": scene_semantic_sources,
            "usedSemanticFallback": used_semantic_fallback,
            "semanticHintCount": semantic_hint_count,
            "textualBeatCount": len(text_beats),
            "closeupSceneCount": closeup_scene_count,
            "sceneTypeHistogram": scene_type_histogram,
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
                "textSource": str(preprocess.get("textSource") or text_source or "none"),
                "exactLyricsAvailable": exact_lyrics_available,
                "transcriptAvailable": transcript_available,
                "spokenMeaningPrimary": spoken_meaning_primary,
                "charactersAllowed": characters_allowed,
                "sceneSemanticSource": scene_semantic_sources,
                "peopleAutoAddedCount": people_auto_added_count,
                "usedSemanticFallback": used_semantic_fallback,
                "semanticHintCount": semantic_hint_count,
                "audioSemanticSummary": semantic_summary,
                "textualBeatCount": len(text_beats),
                "closeupSceneCount": closeup_scene_count,
                "sceneTypeHistogram": scene_type_histogram,
            },
            "boundaries": boundaries,
        },
    }
