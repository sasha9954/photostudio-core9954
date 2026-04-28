from __future__ import annotations

import json
import hashlib
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from app.engine.gemini_rest import post_generate_content, resolve_gemini_api_key

router = APIRouter()

DIRECTOR_QUESTIONS_MODEL = "gemini-2.5-flash"
ALLOWED_IDS = ("performance_density", "world_mode", "intro_mode")
ALLOWED_VALUES_BY_ID = {
    "performance_density": {"atmospheric", "balanced", "performance_heavy"},
    "world_mode": {
        "train_only",
        "train_plus_city",
        "city_memory_dominant",
        "club_dancefloor",
        "club_bar_backstage",
        "club_mixed",
        "single_location",
        "mixed_locations",
        "memory_intercut",
    },
    "intro_mode": {"intro_environment", "intro_character", "intro_action"},
}
REQUIRED_FIELDS = [
    "lip_sync_density",
    "performance_place",
    "world_zones",
    "intro_plan",
    "outro_plan",
    "camera_style",
]
ALLOWED_DIRECTOR_VALUES: dict[str, set[str]] = {
    "lip_sync_density": {
        "vocal_light_30",
        "balanced_50",
        "vocal_heavy_70",
        "full_vocal",
    },
    "performance_place": {
        "one_main_place",
        "multiple_places",
        "performance_plus_memories",
    },
    "world_zones": {
        "train_only",
        "train_and_odesa",
        "odesa_dominant",
        "club_dancefloor",
        "club_full",
        "city_mixed",
        "generic_mixed",
    },
    "intro_plan": {
        "intro_location_first",
        "intro_character_first",
        "intro_action_first",
    },
    "outro_plan": {
        "outro_stay_inside",
        "outro_arrival",
        "outro_exit_to_world",
    },
    "camera_style": {
        "static_cinematic",
        "smooth_glide",
        "emotional_close",
        "dynamic_music",
    },
}


def _stable_hash_payload(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute_payload_scenario_input_signature(payload: dict[str, Any]) -> str:
    safe = payload if isinstance(payload, dict) else {}
    source = _safe_dict(safe.get("source"))
    metadata = _safe_dict(safe.get("metadata"))
    metadata_audio = _safe_dict(metadata.get("audio"))
    refs_by_role = _safe_dict(safe.get("refs_by_role") or safe.get("context_refs"))
    connected_context = _safe_dict(safe.get("connected_context_summary"))
    creative_config = _safe_dict(safe.get("creative_config") or _safe_dict(safe.get("director_controls")).get("creative_config"))
    route_strategy = {
        "mode": str(_safe_dict(safe.get("route_strategy")).get("mode") or creative_config.get("route_strategy_mode") or "").strip().lower(),
        "preset": str(_safe_dict(safe.get("route_strategy")).get("preset") or creative_config.get("route_strategy_preset") or "").strip().lower(),
        "targets": _safe_dict(safe.get("routeTargetsPerBlock") or creative_config.get("route_targets_per_block")),
        "lipsync_ratio": creative_config.get("lipsync_ratio"),
        "first_last_ratio": creative_config.get("first_last_ratio"),
        "i2v_ratio": creative_config.get("i2v_ratio"),
    }
    signature_payload = {
        "note": str(safe.get("narrative_note") or safe.get("director_note") or safe.get("story_text") or safe.get("text") or "").strip(),
        "active_source_mode": str(source.get("source_mode") or metadata.get("activeSourceMode") or "").strip().lower(),
        "audio_url": str(metadata_audio.get("url") or source.get("source_value") or safe.get("audioUrl") or "").strip(),
        "audio_duration_sec": float(
            safe.get("audioDurationSec")
            or safe.get("audio_duration_sec")
            or source.get("audioDurationSec")
            or metadata_audio.get("durationSec")
            or 0
        ),
        "refs_by_role": refs_by_role,
        "refs_present_by_role": _safe_dict(connected_context.get("refsPresentByRole") or connected_context.get("refs_present_by_role")),
        "connected_refs_by_role": _safe_dict(connected_context.get("connectedRefsPresentByRole") or connected_context.get("connected_refs_present_by_role")),
        "director_mode": str(safe.get("director_mode") or metadata.get("director_mode") or "").strip().lower(),
        "content_type": str(safe.get("content_type") or safe.get("contentType") or _safe_dict(safe.get("director_controls")).get("contentType") or "").strip().lower(),
        "format": str(safe.get("format") or _safe_dict(safe.get("director_controls")).get("format") or "").strip(),
        "route_strategy": route_strategy,
    }
    return _stable_hash_payload(signature_payload)


def _director_artifact_signature(value: Any) -> str:
    return str(_safe_dict(value).get("created_for_signature") or "").strip()


def _world_mode_options(world_hint: str) -> list[dict[str, str]]:
    hint = str(world_hint or "generic").strip().lower()
    if hint == "train":
        return [
            {"label": "Только поезд", "value": "train_only"},
            {"label": "Поезд + Одесса", "value": "train_plus_city"},
            {"label": "Больше Одессы", "value": "city_memory_dominant"},
        ]
    if hint == "club":
        return [
            {"label": "Танцпол", "value": "club_dancefloor"},
            {"label": "Бар / закулисье", "value": "club_bar_backstage"},
            {"label": "Весь клуб", "value": "club_mixed"},
        ]
    return [
        {"label": "Одно место", "value": "single_location"},
        {"label": "Несколько мест", "value": "mixed_locations"},
        {"label": "С воспоминаниями", "value": "memory_intercut"},
    ]


def build_fallback_director_question(context: dict[str, Any], answers_so_far: dict[str, Any]) -> dict[str, Any] | None:
    world_hint = str((context or {}).get("world_hint") or "generic").lower()
    safe_answers = answers_so_far if isinstance(answers_so_far, dict) else {}
    if "performance_density" not in safe_answers:
        return {
            "id": "performance_density",
            "text": "Какой баланс клипа ближе?",
            "options": [
                {"label": "Больше атмосферы", "value": "atmospheric"},
                {"label": "50/50", "value": "balanced"},
                {"label": "Больше пения", "value": "performance_heavy"},
            ],
        }
    if "world_mode" not in safe_answers:
        return {
            "id": "world_mode",
            "text": "Как устроить пространство клипа?",
            "options": _world_mode_options(world_hint),
        }
    if "intro_mode" not in safe_answers:
        return {
            "id": "intro_mode",
            "text": "С чего открыть клип?",
            "options": [
                {"label": "Через среду", "value": "intro_environment"},
                {"label": "Через героя", "value": "intro_character"},
                {"label": "С действия", "value": "intro_action"},
            ],
        }
    return None


def get_next_director_question_id(answers_so_far: dict[str, Any]) -> str:
    safe_answers = answers_so_far if isinstance(answers_so_far, dict) else {}
    if "performance_density" not in safe_answers:
        return "performance_density"
    if "world_mode" not in safe_answers:
        return "world_mode"
    if "intro_mode" not in safe_answers:
        return "intro_mode"
    return ""

def _build_director_config_preview(answers_so_far: dict[str, Any]) -> dict[str, Any]:
    safe_answers = answers_so_far if isinstance(answers_so_far, dict) else {}
    config: dict[str, Any] = {}
    pd = str(safe_answers.get("performance_density") or "").strip()
    if pd == "atmospheric":
        config["ia2v_ratio"] = 0.2
    elif pd == "balanced":
        config["ia2v_ratio"] = 0.5
    elif pd == "performance_heavy":
        config["ia2v_ratio"] = 0.8

    wm = str(safe_answers.get("world_mode") or "").strip()
    if wm == "train_only":
        config["ia2v_locations"] = ["train"]
        config["i2v_locations"] = ["train"]
    elif wm == "train_plus_city":
        config["ia2v_locations"] = ["train"]
        config["i2v_locations"] = ["city"]
    elif wm == "city_memory_dominant":
        config["ia2v_locations"] = ["train"]
        config["i2v_locations"] = ["city"]
        config["memory_intercut"] = True
    elif wm == "club_dancefloor":
        config["ia2v_locations"] = ["club_dancefloor"]
        config["i2v_locations"] = ["club"]
    elif wm == "club_bar_backstage":
        config["ia2v_locations"] = ["club_bar", "club_backstage"]
        config["i2v_locations"] = ["club"]
    elif wm == "club_mixed":
        config["ia2v_locations"] = ["club"]
        config["i2v_locations"] = ["club"]

    intro = str(safe_answers.get("intro_mode") or "").strip()
    if intro == "intro_environment":
        config["intro_scenes"] = ["environment_opening"]
    elif intro == "intro_character":
        config["intro_scenes"] = ["hero_closeup"]
    elif intro == "intro_action":
        config["intro_scenes"] = ["action_start"]
    return config


def build_director_config_from_answers(answers: dict[str, Any]) -> dict[str, Any]:
    safe_answers = answers if isinstance(answers, dict) else {}
    config: dict[str, Any] = {}

    lip_sync_density = str(safe_answers.get("lip_sync_density") or "").strip()
    if lip_sync_density == "vocal_light_30":
        config["ia2v_ratio"] = 0.3
        config["i2v_ratio"] = 0.7
    elif lip_sync_density == "balanced_50":
        config["ia2v_ratio"] = 0.5
        config["i2v_ratio"] = 0.5
    elif lip_sync_density == "vocal_heavy_70":
        config["ia2v_ratio"] = 0.7
        config["i2v_ratio"] = 0.3
    elif lip_sync_density == "full_vocal":
        config["ia2v_ratio"] = 0.9
        config["i2v_ratio"] = 0.1

    performance_place = str(safe_answers.get("performance_place") or "").strip()
    if performance_place in {"one_main_place", "multiple_places", "performance_plus_memories"}:
        config["performance_place_mode"] = performance_place

    world_zones = str(safe_answers.get("world_zones") or "").strip()
    if world_zones == "train_only":
        config["ia2v_locations"] = ["train"]
        config["i2v_locations"] = ["train"]
    elif world_zones == "train_and_odesa":
        config["ia2v_locations"] = ["train"]
        config["i2v_locations"] = ["odesa_city", "odesa_port", "odesa_streets"]
    elif world_zones == "odesa_dominant":
        config["ia2v_locations"] = ["train"]
        config["i2v_locations"] = ["odesa_city", "odesa_port", "odesa_streets", "odesa_courtyard"]
        config["memory_intercut"] = True
    elif world_zones == "club_dancefloor":
        config["ia2v_locations"] = ["club_dancefloor"]
        config["i2v_locations"] = ["club_dancefloor"]
    elif world_zones == "club_full":
        config["ia2v_locations"] = ["club_dancefloor", "club_bar", "club_backstage"]
        config["i2v_locations"] = ["club_dancefloor", "club_bar", "club_backstage", "crowd"]
    elif world_zones == "city_mixed":
        config["ia2v_locations"] = ["main_location"]
        config["i2v_locations"] = ["city", "streets", "interiors"]
    elif world_zones == "generic_mixed":
        config["i2v_locations"] = ["main_location", "secondary_location"]

    intro_plan = str(safe_answers.get("intro_plan") or "").strip()
    if intro_plan == "intro_location_first":
        config["intro_scenes"] = ["location_establishing", "character_entry"]
    elif intro_plan == "intro_character_first":
        config["intro_scenes"] = ["hero_closeup", "emotional_setup"]
    elif intro_plan == "intro_action_first":
        config["intro_scenes"] = ["action_start", "rhythm_start"]

    outro_plan = str(safe_answers.get("outro_plan") or "").strip()
    if outro_plan == "outro_stay_inside":
        config["outro_scenes"] = ["final_inside", "emotional_hold"]
    elif outro_plan == "outro_arrival":
        config["outro_scenes"] = ["arrival_or_resolution", "final_look"]
    elif outro_plan == "outro_exit_to_world":
        config["outro_scenes"] = ["exit_to_world", "wide_final"]

    camera_style = str(safe_answers.get("camera_style") or "").strip()
    if camera_style == "static_cinematic":
        config["camera_style"] = "still_witness"
    elif camera_style == "smooth_glide":
        config["camera_style"] = "cinematic_glide"
    elif camera_style == "emotional_close":
        config["camera_style"] = "emotional_proximity"
    elif camera_style == "dynamic_music":
        config["camera_style"] = "dynamic_controlled"

    return config


def _sanitize_director_answers(answers: dict[str, Any] | None) -> dict[str, str]:
    safe_answers = answers if isinstance(answers, dict) else {}
    normalized: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        value = str(safe_answers.get(field) or "").strip()
        if value and value in ALLOWED_DIRECTOR_VALUES.get(field, set()):
            normalized[field] = value
    return normalized


def _get_missing_director_fields(answers: dict[str, Any]) -> list[str]:
    safe_answers = answers if isinstance(answers, dict) else {}
    return [field for field in REQUIRED_FIELDS if field not in safe_answers]


def _fallback_question_for_field(field: str, context: dict[str, Any]) -> str:
    world_hint = str((context or {}).get("world_hint") or "generic").strip().lower()
    if field == "lip_sync_density":
        return "Сколько пения / lip-sync показываем: больше атмосферы, 50/50, больше пения или почти весь клип поёт?"
    if field == "performance_place":
        if world_hint == "train":
            return "Где герой поёт: только в купе, в разных местах поезда или поезд + воспоминания?"
        if world_hint == "club":
            return "Где держим главный перформанс: одна зона, разные зоны клуба или клуб + вставки?"
        return "Где держим главный перформанс: в одном основном месте, в нескольких местах или с вставками-воспоминаниями?"
    if field == "world_zones":
        if world_hint == "train":
            return "Что показываем между пением: только поезд, поезд + Одесса или больше Одессы?"
        if world_hint == "club":
            return "Какие зоны клуба показываем: танцпол, весь клуб или клуб + город?"
        return "Какие зоны мира показываем между пением: основной локацией ограничимся или добавим другие пространства?"
    if field == "intro_plan":
        return "Как начать клип: сначала место, сразу герой или сразу действие?"
    if field == "outro_plan":
        return "Как закончить клип: остаться внутри, финальное прибытие или выход в мир?"
    if field == "camera_style":
        return "Как снимать камерой: статично-киношно, плавно, крупно-эмоционально или динамично под бит?"
    return "Уточните режиссёрское решение."


def build_director_chat_prompt(
    context: dict[str, Any],
    messages: list[dict[str, Any]],
    answers: dict[str, Any],
    user_message: str,
) -> str:
    safe_messages = messages if isinstance(messages, list) else []
    trimmed_messages: list[dict[str, str]] = []
    for msg in safe_messages[-12:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        content = str(msg.get("content") or "").strip()
        if role not in {"assistant", "user"} or not content:
            continue
        trimmed_messages.append({"role": role, "content": content})

    return f"""
You are an AI Director assistant for a music video generator.

Your job:
1. Understand the user's free-text answer.
2. Extract any director fields from it.
3. Update structured answers.
4. Ask the next missing question.
5. Return JSON only.

Important:
* Speak Russian only.
* Do not use technical terms like ia2v, i2v, payload, API.
* Do not ask about clothing, refs, model quality, resolution.
* Ask only about director choices.
* The user may answer naturally, not with exact option names.
* If user answer contains multiple decisions, extract all of them.
* If answer is unclear, ask a short clarification.
* Maximum one assistant question per response.
* When all required fields are collected, set done=true.

Return JSON:
{{
  "extracted_answers": {{
    "lip_sync_density": "..."
  }},
  "assistant_message": "Следующий вопрос...",
  "done": false
}}

Allowed values:
{json.dumps({k: sorted(v) for k, v in ALLOWED_DIRECTOR_VALUES.items()}, ensure_ascii=False)}

Current answers:
{json.dumps(answers if isinstance(answers, dict) else {}, ensure_ascii=False)}

Messages:
{json.dumps(trimmed_messages, ensure_ascii=False)}

User message:
{user_message}

Context:
{json.dumps(context if isinstance(context, dict) else {}, ensure_ascii=False)}
""".strip()


def build_director_prompt(context: dict[str, Any], answers_so_far: dict[str, Any] | None = None) -> str:
    world_hint = str((context or {}).get("world_hint") or "generic").strip().lower()
    safe_answers = answers_so_far if isinstance(answers_so_far, dict) else {}
    next_question_id = get_next_director_question_id(safe_answers)
    world_mode_options = _world_mode_options(world_hint)
    return f"""
Ты AI-режиссёр клипа.

Правила:
* Ответь только на русском языке.
* Задай только ОДИН следующий вопрос.
* Варианты должны быть короткими: максимум 5 слов.
* Не пиши длинные объяснения в кнопках.
* Не спрашивай про одежду, рефы, качество, модель, формат.
* Спрашивай только про режиссуру: баланс перформанса, пространство, начало клипа.
* Question id MUST be one of:
  * performance_density
  * world_mode
  * intro_mode
* Do not invent ids.
* Return JSON only.

Допустимые значения:
* performance_density: atmospheric, balanced, performance_heavy
* world_mode (для world_hint={world_hint}): {json.dumps(world_mode_options, ensure_ascii=False)}
* intro_mode: intro_environment, intro_character, intro_action
* Already answered:
{json.dumps(safe_answers, ensure_ascii=False)}
* Ask ONLY this next question id:
{next_question_id}
* Do not ask any answered question again.

Формат ответа:
{{
  "question": {{
    "id": "performance_density | world_mode | intro_mode",
    "text": "Короткий вопрос",
    "options": [
      {{ "label": "Коротко", "value": "allowed_value" }},
      {{ "label": "Коротко", "value": "allowed_value" }}
    ]
  }}
}}

Context:
{json.dumps(context, ensure_ascii=False)}
""".strip()


def _extract_text_from_gemini(response: dict[str, Any]) -> str:
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        chunks: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
        if chunks:
            return "\n".join(chunks)
    return ""


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    payload = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = payload.find("{")
    end = payload.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(payload[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _validate_question(
    question: dict[str, Any] | None,
    answers_so_far: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(question, dict):
        return None
    qid = str(question.get("id") or "").strip()
    if qid not in ALLOWED_IDS or qid in answers_so_far:
        return None
    text = str(question.get("text") or "").strip()
    options = question.get("options")
    if not text or not isinstance(options, list) or not (2 <= len(options) <= 3):
        return None
    valid_options: list[dict[str, str]] = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        label = str(opt.get("label") or "").strip()
        value = str(opt.get("value") or "").strip()
        allowed_values = ALLOWED_VALUES_BY_ID.get(qid, set())
        if value not in allowed_values:
            continue
        if not label or not value:
            continue
        if len(label.split()) > 5:
            continue
        valid_options.append({"label": label, "value": value})
    if not (2 <= len(valid_options) <= 3):
        return None
    return {"id": qid, "text": text, "options": valid_options}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_non_empty_string(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _safe_nonempty_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x or "").strip()]


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ensure_director_v2_legacy_contract_fields(
    director_contract: dict[str, Any],
    director_config: dict[str, Any],
    director_package: dict[str, Any],
) -> dict[str, Any]:
    contract = dict(_safe_dict(director_contract))
    config = _safe_dict(director_config)
    package = _safe_dict(director_package)

    world_roles = _safe_dict(contract.get("world_roles"))
    route_location_rules = _safe_dict(contract.get("route_location_rules"))

    ia2v_locations = _safe_nonempty_list(config.get("ia2v_locations"))
    i2v_locations = _safe_nonempty_list(config.get("i2v_locations"))

    timeline_contract = _safe_dict(package.get("timeline_contract") or contract.get("timeline_contract"))
    route_contract = _safe_dict(package.get("route_contract") or contract.get("route_contract"))

    timelines = _safe_list(timeline_contract.get("timelines"))
    for tl in timelines:
        if not isinstance(tl, dict):
            continue
        world_role = str(tl.get("world_role") or "").strip()
        label = str(tl.get("label") or tl.get("world") or tl.get("description") or "").strip()
        allowed_routes = [str(x).strip().lower() for x in _safe_list(tl.get("allowed_routes"))]
        if not ia2v_locations and ("ia2v" in allowed_routes or world_role == "performance_world") and label:
            ia2v_locations = [label]
        if not i2v_locations and ("i2v" in allowed_routes or world_role == "memory_world") and label:
            i2v_locations = [label]

    ia2v_contract = _safe_dict(route_contract.get("ia2v"))
    i2v_contract = _safe_dict(route_contract.get("i2v"))
    if not ia2v_locations:
        ia2v_candidate = _first_non_empty_string(
            ia2v_contract.get("required_world"),
            ia2v_contract.get("world"),
            ia2v_contract.get("setting"),
            ia2v_contract.get("timeline"),
        )
        if ia2v_candidate:
            ia2v_locations = [ia2v_candidate]
    if not i2v_locations:
        i2v_candidate = _first_non_empty_string(
            i2v_contract.get("required_world"),
            i2v_contract.get("world"),
            i2v_contract.get("setting"),
            i2v_contract.get("timeline"),
        )
        if i2v_candidate:
            i2v_locations = [i2v_candidate]

    performance_world = _safe_dict(world_roles.get("performance_world"))
    memory_world = _safe_dict(world_roles.get("memory_world"))

    if not _safe_nonempty_list(performance_world.get("allowed_zones")) and ia2v_locations:
        performance_world["allowed_zones"] = ia2v_locations
    if "label" not in performance_world:
        performance_world["label"] = ""

    if not _safe_nonempty_list(memory_world.get("allowed_zones")) and i2v_locations:
        memory_world["allowed_zones"] = i2v_locations
    if "label" not in memory_world:
        memory_world["label"] = ""

    world_roles["performance_world"] = performance_world
    world_roles["memory_world"] = memory_world
    contract["world_roles"] = world_roles

    if "ia2v" not in route_location_rules:
        route_location_rules["ia2v"] = {
            "world_role": "performance_world",
            "performer_visibility": "required",
            "singer_visibility": "required",
            "lip_sync_framing": "required",
        }
    else:
        ia2v_rule = _safe_dict(route_location_rules.get("ia2v"))
        ia2v_rule.setdefault("world_role", "performance_world")
        ia2v_rule.setdefault("performer_visibility", "required")
        ia2v_rule.setdefault("singer_visibility", "required")
        ia2v_rule.setdefault("lip_sync_framing", "required")
        route_location_rules["ia2v"] = ia2v_rule

    if "i2v" not in route_location_rules:
        route_location_rules["i2v"] = {
            "world_role": "memory_world",
            "performer_visibility": "optional_or_absent",
            "singer_visibility": "offscreen_or_non_dominant",
        }
    else:
        i2v_rule = _safe_dict(route_location_rules.get("i2v"))
        i2v_rule.setdefault("world_role", "memory_world")
        i2v_rule.setdefault("performer_visibility", "optional_or_absent")
        i2v_rule.setdefault("singer_visibility", "offscreen_or_non_dominant")
        route_location_rules["i2v"] = i2v_rule

    contract["route_location_rules"] = route_location_rules
    has_world_split = bool(
        _safe_nonempty_list(performance_world.get("allowed_zones"))
        or _safe_nonempty_list(memory_world.get("allowed_zones"))
    )
    contract["hard_location_binding"] = bool(contract.get("hard_location_binding") or has_world_split)
    return contract


_DIRECTOR_ROLE_FUNCTIONS = {
    "performer",
    "speaker",
    "singer",
    "memory_subject",
    "action_subject",
    "support_subject",
    "episodic_visual_extra",
    "environment_subject",
    "unknown",
}


def _ensure_director_v2_structured_contract_fields(
    director_contract: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = dict(_safe_dict(director_contract))
    mode_contract = _safe_dict(contract.get("mode_contract"))
    clip_mode = str(mode_contract.get("mode") or "").strip().lower() == "clip"
    character_contract = _safe_dict(contract.get("character_contract"))
    role_usage_contract = _safe_dict(contract.get("role_usage_contract"))
    ref_usage_map = _safe_dict(_safe_dict(contract.get("reference_usage_contract")).get("character_usage"))
    clip_contract = _safe_dict(contract.get("clip_contract"))
    performance_contract = _safe_dict(contract.get("performance_contract"))
    memory_contract = _safe_dict(contract.get("memory_contract"))
    payload_context_refs = _safe_dict(payload.get("context_refs"))
    payload_refs_by_role = _safe_dict(payload.get("refs_by_role"))
    connected_summary = _safe_dict(payload.get("connected_context_summary"))
    summary_refs_by_role = _safe_dict(connected_summary.get("refsPresentByRole"))
    summary_connected_refs_by_role = _safe_dict(connected_summary.get("connectedRefsPresentByRole"))
    payload_selected_refs = payload.get("selected_refs")

    performance_roles = {
        str(role_id or "").strip().lower()
        for role_id in _safe_list(performance_contract.get("performance_roles"))
        if str(role_id or "").strip()
    }
    memory_roles = {
        str(role_id or "").strip().lower()
        for role_id in _safe_list(memory_contract.get("memory_roles"))
        if str(role_id or "").strip()
    }

    def _normalize_routes(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [x.strip() for x in re.split(r"[,;/|]", value) if x.strip()]
        routes: list[str] = []
        for item in _safe_list(value):
            token = str(item or "").strip().lower()
            if token in {"ia2v", "i2v"} and token not in routes:
                routes.append(token)
        return routes

    def _to_bool_or_none(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        token = str(value or "").strip().lower()
        if token in {"true", "1", "yes", "required"}:
            return True
        if token in {"false", "0", "no", "none", "not_required"}:
            return False
        return None

    def _lookup_role_map_value(role_map: dict[str, Any], role_id: str) -> Any:
        if role_id in role_map:
            return role_map.get(role_id)
        role_id_l = role_id.lower()
        for key, value in role_map.items():
            if str(key or "").strip().lower() == role_id_l:
                return value
        return None

    def _has_visual_ref_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            token = value.strip().lower()
            if not token:
                return False
            if token in {
                "auto",
                "false",
                "0",
                "none",
                "null",
                "no",
                "absent",
                "no_reference_needed",
                "no reference",
                "без рефа",
                "референс не нужен",
            }:
                return False
            if re.fullmatch(r"(character|role)_\d+", token):
                return False
            if token.startswith(("http://", "https://", "/static/", "data:image")):
                return True
            if re.search(r"\.(png|jpg|jpeg|webp|gif|bmp)(?:[?#].*)?$", token):
                return True
            return False
        if isinstance(value, list):
            return any(_has_visual_ref_value(item) for item in value)
        if isinstance(value, dict):
            for key in ("present", "has_refs", "connected", "count", "total"):
                if key in value and _has_visual_ref_value(value.get(key)):
                    return True
            if any(_has_visual_ref_value(v) for v in value.values()):
                return True
            return len(value) > 0
        return False

    def _role_has_visual_refs(role_id: str) -> bool:
        selected_refs_dict = _safe_dict(payload_selected_refs) if isinstance(payload_selected_refs, dict) else {}
        if selected_refs_dict:
            if _has_visual_ref_value(_lookup_role_map_value(selected_refs_dict, role_id)):
                return True
            if _has_visual_ref_value(_lookup_role_map_value(selected_refs_dict, f"ref_{role_id}")):
                return True
        for role_map in (
            payload_context_refs,
            payload_refs_by_role,
            summary_refs_by_role,
            summary_connected_refs_by_role,
        ):
            if _has_visual_ref_value(_lookup_role_map_value(role_map, role_id)):
                return True
        for item in _safe_list(payload_selected_refs):
            row = _safe_dict(item)
            item_role = str(row.get("role_id") or row.get("role") or row.get("character_role") or "").strip().lower()
            if item_role == role_id.lower() and _has_visual_ref_value(row):
                return True
            item_roles = [str(v or "").strip().lower() for v in _safe_list(row.get("role_ids")) if str(v or "").strip()]
            if role_id.lower() in item_roles and _has_visual_ref_value(row):
                return True
        return False

    def _infer_function(raw_function: str, routes: list[str], ref_usage_hint: str, role_row: dict[str, Any]) -> str:
        token = str(raw_function or "").strip().lower()
        if token in _DIRECTOR_ROLE_FUNCTIONS:
            return token
        token_blob = " ".join(
            [
                token,
                str(role_row.get("story_role") or "").strip().lower(),
                str(role_row.get("description") or "").strip().lower(),
                ref_usage_hint,
            ]
        )
        if any(
            x in token_blob
            for x in (
                "episodic",
                "эпизод",
                "эпизодический",
                "одна сцена",
                "один эпизод",
                "без рефа",
                "референс не нужен",
                "no_reference_needed",
                "no reference",
                "extra",
                "background",
            )
        ):
            return "episodic_visual_extra"
        if any(x in token_blob for x in ("support", "secondary")):
            return "support_subject"
        if any(x in token_blob for x in ("memory", "past", "flashback", "воспомин", "память", "прошл", "молодост")):
            return "memory_subject"
        if any(x in token_blob for x in ("action", "movement", "event", "действ", "событие", "движение")):
            return "action_subject"
        if any(x in token_blob for x in ("speaker", "talk", "dialog")):
            return "speaker"
        if any(
            x in token_blob
            for x in (
                "singer",
                "vocal",
                "sing",
                "поет",
                "поёт",
                "пение",
                "вокал",
                "поющий",
                "певец",
                "певица",
                "липсинк",
                "lip-sync",
                "lipsync",
            )
        ):
            return "singer"
        if routes == ["ia2v"] or ("ia2v" in routes and "i2v" not in routes):
            return "performer"
        if "i2v" in routes:
            return "action_subject"
        return "unknown"

    role_ids: set[str] = set()
    for source in (character_contract, role_usage_contract, ref_usage_map):
        for key in _safe_dict(source).keys():
            role_id = str(key or "").strip().lower()
            if role_id.startswith("character_"):
                role_ids.add(role_id)

    normalized_role_usage: dict[str, dict[str, Any]] = {}
    role_route_inference_sources: dict[str, list[str]] = {}
    reference_detection_by_role: dict[str, dict[str, Any]] = {}
    for role_id in sorted(role_ids):
        char_row = _safe_dict(character_contract.get(role_id))
        role_row = _safe_dict(role_usage_contract.get(role_id))
        ref_usage_hint = str(ref_usage_map.get(role_id) or "").strip().lower()
        route_inference_sources: list[str] = []
        routes = _normalize_routes(role_row.get("routes") or char_row.get("routes"))
        if routes:
            if _normalize_routes(role_row.get("routes")):
                route_inference_sources.append("role_usage_contract.routes")
            elif _normalize_routes(char_row.get("routes")):
                route_inference_sources.append("character_contract.routes")
        preferred_route = str(role_row.get("preferred_route") or char_row.get("preferred_route") or "").strip().lower()
        if preferred_route in {"ia2v", "i2v"} and preferred_route not in routes:
            routes.append(preferred_route)
            route_inference_sources.append("preferred_route")
        if not routes:
            episodic_hint_for_route = any(
                hint in " ".join(
                    [
                        ref_usage_hint,
                        str(char_row.get("story_role") or "").strip().lower(),
                        str(role_row.get("story_role") or "").strip().lower(),
                        str(char_row.get("description") or "").strip().lower(),
                        str(role_row.get("description") or "").strip().lower(),
                    ]
                )
                for hint in ("episodic", "эпизод", "одна сцена", "один эпизод", "no reference", "no_reference_needed")
            )
            if role_id in performance_roles:
                routes = ["ia2v"]
                route_inference_sources.append("performance_contract.performance_roles")
            elif role_id in memory_roles:
                routes = ["i2v"]
                route_inference_sources.append("memory_contract.memory_roles")
            elif "no_reference_needed" in ref_usage_hint and episodic_hint_for_route:
                routes = ["i2v"]
                route_inference_sources.append("reference_usage_contract.no_reference_needed+episodic")
            elif clip_mode and role_id == str(clip_contract.get("vocal_owner_role") or "").strip().lower():
                routes = ["ia2v"]
                route_inference_sources.append("clip_contract.vocal_owner_role")

        raw_function = str(role_row.get("function") or char_row.get("function") or char_row.get("story_role") or "").strip()
        function = _infer_function(raw_function, routes, ref_usage_hint, {**char_row, **role_row})
        if not routes:
            if function in {"performer", "speaker", "singer"}:
                routes = ["ia2v"]
                route_inference_sources.append("function_fallback:performer_or_singer")
            elif function in {"memory_subject", "action_subject", "support_subject", "episodic_visual_extra", "environment_subject"}:
                routes = ["i2v"]
                route_inference_sources.append("function_fallback:narrative_or_episodic")
        reference_required = _to_bool_or_none(
            role_row.get("reference_required")
            if "reference_required" in role_row
            else char_row.get("reference_required")
        )
        has_visual_refs = _role_has_visual_refs(role_id)
        if "no_reference_needed" in ref_usage_hint:
            reference_required = False
        elif has_visual_refs:
            reference_required = True

        identity_mode = str(role_row.get("identity_mode") or char_row.get("identity_mode") or "").strip().lower()
        episodic_hint = any(
            hint in " ".join(
                [
                    str(char_row.get("story_role") or "").strip().lower(),
                    str(role_row.get("story_role") or "").strip().lower(),
                    ref_usage_hint,
                    function,
                ]
            )
            for hint in ("episodic", "extra", "no_reference_needed", "background")
        )
        if identity_mode not in {"strict", "loose", "episodic"}:
            if reference_required is True:
                identity_mode = "strict"
            elif episodic_hint:
                identity_mode = "episodic"
            else:
                identity_mode = "loose"
        if not str(role_row.get("identity_mode") or char_row.get("identity_mode") or "").strip() and has_visual_refs and "no_reference_needed" not in ref_usage_hint:
            identity_mode = "strict"

        max_scene_count_raw = role_row.get("max_scene_count")
        if max_scene_count_raw in {None, ""}:
            max_scene_count_raw = char_row.get("max_scene_count")
        try:
            max_scene_count = int(max_scene_count_raw) if max_scene_count_raw not in {None, ""} else None
        except (TypeError, ValueError):
            max_scene_count = None
        if max_scene_count is None and reference_required is False and identity_mode == "episodic":
            max_scene_count = 1

        reason = str(role_row.get("reason") or "").strip()
        if not reason:
            reason_parts: list[str] = []
            if routes:
                reason_parts.append(f"route:{'/'.join(routes)}")
            if ref_usage_hint:
                reason_parts.append(f"reference_policy:{ref_usage_hint}")
            elif reference_required is not None:
                reason_parts.append(f"reference_required:{str(reference_required).lower()}")
            if raw_function:
                reason_parts.append(f"context:{raw_function}")
            reason = "; ".join(reason_parts)[:240]

        normalized_role_usage[role_id] = {
            "function": function if function in _DIRECTOR_ROLE_FUNCTIONS else "unknown",
            "routes": routes,
            "reference_required": reference_required,
            "identity_mode": identity_mode,
            "max_scene_count": max_scene_count,
            "reason": reason,
        }
        role_route_inference_sources[role_id] = route_inference_sources
        reference_detection_by_role[role_id] = {
            "has_visual_refs": has_visual_refs,
            "ref_usage_hint": ref_usage_hint,
            "reference_required": reference_required,
        }

    route_semantics = _safe_dict(contract.get("route_semantics"))
    route_semantics.setdefault(
        "ia2v",
        {
            "meaning": "audio_sensitive_performance_or_lip_sync_scene",
            "allowed_role_functions": ["performer", "speaker", "singer"],
        },
    )
    route_semantics.setdefault(
        "i2v",
        {
            "meaning": "visual_story_or_memory_action_scene",
            "allowed_role_functions": [
                "memory_subject",
                "action_subject",
                "support_subject",
                "episodic_visual_extra",
                "environment_subject",
            ],
        },
    )
    contract["route_semantics"] = route_semantics
    contract["role_usage_contract"] = normalized_role_usage

    existing_scene_requirements = _safe_list(contract.get("scene_requirements"))
    scene_requirements_are_structured = bool(existing_scene_requirements)
    normalized_requirements: list[dict[str, Any]] = []
    for idx, row in enumerate(existing_scene_requirements, start=1):
        req = _safe_dict(row)
        normalized_requirements.append(
            {
                "id": str(req.get("id") or f"req_{idx:02d}").strip(),
                "required": bool(req.get("required", True)),
                "expected_route": str(req.get("expected_route") or req.get("route") or "").strip().lower(),
                "expected_roles": [
                    str(role).strip().lower()
                    for role in _safe_list(req.get("expected_roles") or req.get("roles"))
                    if str(role).strip().lower().startswith("character_")
                ],
                "expected_role_functions": [str(v).strip().lower() for v in _safe_list(req.get("expected_role_functions")) if str(v).strip()],
                "expected_world": str(req.get("expected_world") or "").strip().lower(),
                "min_count": int(req.get("min_count") or 1),
                "max_count": int(req.get("max_count")) if req.get("max_count") not in {None, ""} else None,
                "source_text": str(req.get("source_text") or req.get("text") or "").strip(),
                "purpose": str(req.get("purpose") or "").strip(),
            }
        )

    scene_requirements_source = ""
    if scene_requirements_are_structured:
        scene_requirements_source = "director_contract.scene_requirements"
    if clip_mode and not normalized_requirements:
        mandatory_scene_rows: list[str] = []
        scene_contract = _safe_dict(contract.get("scene_contract"))
        for source in (
            _safe_list(scene_contract.get("mandatory_scenes")),
            _safe_list(clip_contract.get("mandatory_scenes")),
            _safe_list(memory_contract.get("mandatory_scenes")),
        ):
            for row in source:
                text = str(row or "").strip()
                if text:
                    mandatory_scene_rows.append(text)

        req_idx = 1
        if mandatory_scene_rows:
            scene_requirements_source = "mandatory_scenes_fallback"
            for text in mandatory_scene_rows:
                lowered = text.lower()
                expected_route = ""
                source_text = text
                if lowered.startswith("ia2v:"):
                    expected_route = "ia2v"
                    source_text = text[5:].strip() or text
                elif lowered.startswith("i2v:"):
                    expected_route = "i2v"
                    source_text = text[4:].strip() or text
                normalized_requirements.append(
                    {
                        "id": f"req_{req_idx:02d}",
                        "required": True,
                        "expected_route": expected_route,
                        "expected_roles": [],
                        "expected_role_functions": [],
                        "expected_world": "",
                        "min_count": 1,
                        "max_count": None,
                        "source_text": text,
                        "purpose": source_text[:160],
                    }
                )
                req_idx += 1

        role_fallback_rows: list[dict[str, Any]] = []
        for role_id, role_row in normalized_role_usage.items():
            function = str(role_row.get("function") or "").strip().lower()
            routes = _safe_list(role_row.get("routes"))
            if function in {"performer", "speaker", "singer"} and "ia2v" in routes:
                role_fallback_rows.append(
                    {
                        "id": f"req_{req_idx:02d}",
                        "required": True,
                        "expected_route": "ia2v",
                        "expected_roles": [role_id],
                        "expected_role_functions": [function],
                        "expected_world": "performance_world",
                        "min_count": 1,
                        "max_count": None,
                        "source_text": "Main performance role inferred from director contract.",
                        "purpose": "ensure audio-sensitive performance scenes are present",
                    }
                )
                req_idx += 1
            if function in {"memory_subject", "action_subject", "support_subject", "episodic_visual_extra", "environment_subject"} and "i2v" in routes:
                role_fallback_rows.append(
                    {
                        "id": f"req_{req_idx:02d}",
                        "required": True,
                        "expected_route": "i2v",
                        "expected_roles": [role_id],
                        "expected_role_functions": [function],
                        "expected_world": "memory_world",
                        "min_count": 1,
                        "max_count": int(role_row.get("max_scene_count")) if role_row.get("max_scene_count") not in {None, ""} else None,
                        "source_text": "Story/memory/action role inferred from director contract.",
                        "purpose": "ensure visual narrative beats are present",
                    }
                )
                req_idx += 1

        if not mandatory_scene_rows:
            scene_requirements_source = "role_usage_fallback"
            normalized_requirements.extend(role_fallback_rows)
        elif role_fallback_rows:
            normalized_requirements.extend(role_fallback_rows[:2])

    if normalized_requirements:
        contract["scene_requirements"] = normalized_requirements

    missing_fields: list[str] = []
    if not _safe_dict(contract.get("route_semantics")):
        missing_fields.append("route_semantics")
    if not _safe_dict(contract.get("role_usage_contract")):
        missing_fields.append("role_usage_contract")
    if clip_mode and not _safe_list(contract.get("scene_requirements")):
        missing_fields.append("scene_requirements")

    diagnostics = {
        "director_contract_v2_present": bool(
            _safe_dict(contract.get("route_semantics"))
            and _safe_dict(contract.get("role_usage_contract"))
        ),
        "director_contract_v2_role_usage_count": len(_safe_dict(contract.get("role_usage_contract"))),
        "director_contract_v2_scene_requirements_count": len(_safe_list(contract.get("scene_requirements"))),
        "director_contract_v2_route_semantics_present": bool(_safe_dict(contract.get("route_semantics"))),
        "director_contract_v2_missing_fields": missing_fields,
        "director_contract_v2_role_route_inference_sources": role_route_inference_sources,
        "director_contract_v2_reference_detection_by_role": reference_detection_by_role,
        "director_contract_v2_scene_requirements_source": scene_requirements_source,
        "director_contract_v2_scene_requirements_are_structured": scene_requirements_are_structured,
    }
    return contract, diagnostics




CLIP_ROUTE_BALANCE_PRESETS = {
    "balanced_50_50",
    "performance_heavy_70_30",
    "story_heavy_30_70",
    "all_lipsync",
    "ai_decides",
}


def _is_clip_music_video_payload(payload: dict[str, Any]) -> bool:
    source = _safe_dict(payload.get("source"))
    metadata = _safe_dict(payload.get("metadata"))
    controls = _safe_dict(payload.get("director_controls"))

    mode_candidate = _first_non_empty_string(
        payload.get("director_mode"),
        payload.get("mode_selected"),
        payload.get("mode_type"),
        payload.get("mode"),
        metadata.get("director_mode"),
        metadata.get("mode"),
        source.get("director_mode"),
        source.get("mode"),
    ).lower()
    content_type_candidate = _first_non_empty_string(
        payload.get("content_type"),
        payload.get("contentType"),
        controls.get("contentType"),
        metadata.get("content_type"),
        metadata.get("contentType"),
        source.get("content_type"),
        source.get("contentType"),
    ).lower()
    return mode_candidate == "clip" or content_type_candidate == "music_video"


def _ensure_clip_mode_contracts(
    director_contract: dict[str, Any],
    director_package: dict[str, Any],
    questions: list[Any],
    answers: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    contract = dict(_safe_dict(director_contract))
    package = dict(_safe_dict(director_package))

    mode_source_raw = _first_non_empty_string(
        contract.get("mode_source"),
        package.get("mode_source"),
        answers.get("mode_source"),
    ).lower()
    mode_source = "user_selected" if mode_source_raw == "user_selected" else "ai_confirmed"

    mode_contract = {
        "mode": "clip",
        "mode_locked": True,
        "mode_source": mode_source,
        "allowed_routes": ["ia2v", "i2v"],
        "forbidden_routes": ["first_last"],
        "first_last_allowed": False,
        "ai_must_not_change_mode_without_user_confirmation": True,
    }

    clip_contract = _safe_dict(package.get("clip_contract") or contract.get("clip_contract"))
    clip_contract["clip_type"] = "music_video"
    clip_contract["audio_is_primary_clock"] = True
    clip_contract["performance_route"] = "ia2v"
    clip_contract["story_route"] = "i2v"
    clip_contract["first_last_allowed"] = False

    distribution = _safe_dict(package.get("scene_distribution_contract") or contract.get("scene_distribution_contract"))
    distribution["first_last_ratio"] = 0
    distribution["first_last_allowed"] = False

    prompt_policy = _safe_dict(package.get("prompt_policy") or contract.get("prompt_policy"))
    prompt_policy["negative_rules_are_internal"] = True
    prompt_policy["do_not_copy_negative_rules_into_ltx_positive_prompt"] = True

    package_distribution = _safe_dict(package.get("scene_distribution_contract"))
    contract_distribution = _safe_dict(contract.get("scene_distribution_contract"))
    asked_required = bool(
        answers.get("route_balance")
        or answers.get("scene_distribution")
        or package_distribution.get("user_approved")
        or contract_distribution.get("user_approved")
    )

    contract["mode_contract"] = mode_contract
    contract["clip_contract"] = clip_contract
    contract["scene_distribution_contract"] = distribution
    contract["prompt_policy"] = prompt_policy

    package["mode_contract"] = mode_contract
    package["clip_contract"] = clip_contract
    package["scene_distribution_contract"] = distribution
    package["prompt_policy"] = prompt_policy

    return contract, package, asked_required


def _normalize_clip_contract_aliases(
    director_contract: dict[str, Any],
    director_package: dict[str, Any],
    answers: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = dict(_safe_dict(director_contract))
    package = dict(_safe_dict(director_package))
    normalized_aliases_used: list[str] = []
    route_balance_alias_normalized = False
    route_balance_from_ratio_object = False
    alias_normalization_applied = False

    mode_contract = _safe_dict(package.get("mode_contract") or contract.get("mode_contract"))
    if str(mode_contract.get("mode") or "").strip().lower() != "clip":
        package["_clip_alias_normalization_diagnostics"] = {
            "director_clip_alias_normalization_applied": False,
            "director_clip_aliases_used": [],
            "director_clip_route_balance_alias_normalized": False,
        }
        return contract, package

    clip_contract = _safe_dict(package.get("clip_contract") or contract.get("clip_contract"))
    route_contract = _safe_dict(package.get("route_contract") or contract.get("route_contract"))
    scene_distribution = _safe_dict(package.get("scene_distribution_contract") or contract.get("scene_distribution_contract"))
    prompt_policy = _safe_dict(package.get("prompt_policy") or contract.get("prompt_policy"))

    performance_definition = _first_non_empty_string(
        clip_contract.get("performance_definition"),
        clip_contract.get("ia2v_definition"),
        route_contract.get("ia2v_route_purpose"),
        route_contract.get("ia2v_scenes"),
        prompt_policy.get("ia2v_prompt_guidelines"),
    )
    if performance_definition and not _first_non_empty_string(clip_contract.get("performance_definition")):
        clip_contract["performance_definition"] = performance_definition
        alias_normalization_applied = True
        normalized_aliases_used.append("clip_contract.performance_definition")

    story_cutaway_definition = _first_non_empty_string(
        clip_contract.get("story_cutaway_definition"),
        clip_contract.get("i2v_definition"),
        clip_contract.get("i2v_content_between_performances"),
        route_contract.get("i2v_route_purpose"),
        route_contract.get("i2v_scenes"),
        prompt_policy.get("i2v_prompt_guidelines"),
    )
    if story_cutaway_definition and not _first_non_empty_string(clip_contract.get("story_cutaway_definition")):
        clip_contract["story_cutaway_definition"] = story_cutaway_definition
        alias_normalization_applied = True
        normalized_aliases_used.append("clip_contract.story_cutaway_definition")

    route_balance_alias_map = {
        "50_50": "balanced_50_50",
        "balanced": "balanced_50_50",
        "more_ia2v": "performance_heavy_70_30",
        "more_i2v": "story_heavy_30_70",
        "all_lip_sync": "all_lipsync",
        "all_lipsync": "all_lipsync",
        "ai_decides": "ai_decides",
    }
    answer_route_balance_map = {
        "50_50": "balanced_50_50",
        "more_ia2v": "performance_heavy_70_30",
        "more_i2v": "story_heavy_30_70",
    }
    original_route_balance = _first_non_empty_string(scene_distribution.get("route_balance")).lower()
    mapped_route_balance = route_balance_alias_map.get(original_route_balance, original_route_balance)

    route_balance_confirmation = _first_non_empty_string(_safe_dict(answers).get("route_balance_confirmation")).lower()
    route_balance_from_user = answer_route_balance_map.get(route_balance_confirmation)
    route_balance_used_user_confirmation = False
    if route_balance_from_user:
        mapped_route_balance = route_balance_from_user
        route_balance_used_user_confirmation = True

    user_approved_or_ai_decides = _first_non_empty_string(
        scene_distribution.get("user_approved_or_ai_decides")
    ).lower()
    if user_approved_or_ai_decides in {"user_approved", "approved", "confirmed"}:
        if not bool(scene_distribution.get("user_approved")):
            scene_distribution["user_approved"] = True
            alias_normalization_applied = True
            normalized_aliases_used.append("scene_distribution_contract.user_approved")
    elif user_approved_or_ai_decides == "ai_decides":
        route_balance_value = scene_distribution.get("route_balance")
        route_balance_value_is_string = isinstance(route_balance_value, str) and bool(route_balance_value.strip())
        if not route_balance_value_is_string:
            scene_distribution["route_balance"] = "ai_decides"
            alias_normalization_applied = True
            normalized_aliases_used.append("scene_distribution_contract.route_balance")

    route_balance_object = scene_distribution.get("route_balance")
    if isinstance(route_balance_object, dict):
        ia2v_ratio_obj = _safe_float(route_balance_object.get("ia2v_ratio"), None)
        i2v_ratio_obj = _safe_float(route_balance_object.get("i2v_ratio"), None)
        first_last_ratio_obj = _safe_float(route_balance_object.get("first_last_ratio"), None)

        if scene_distribution.get("ia2v_ratio") is None and ia2v_ratio_obj is not None:
            scene_distribution["ia2v_ratio"] = ia2v_ratio_obj
            alias_normalization_applied = True
            normalized_aliases_used.append("scene_distribution_contract.ia2v_ratio")
        if scene_distribution.get("i2v_ratio") is None and i2v_ratio_obj is not None:
            scene_distribution["i2v_ratio"] = i2v_ratio_obj
            alias_normalization_applied = True
            normalized_aliases_used.append("scene_distribution_contract.i2v_ratio")
        if scene_distribution.get("first_last_ratio") is None and first_last_ratio_obj is not None:
            scene_distribution["first_last_ratio"] = first_last_ratio_obj
            alias_normalization_applied = True
            normalized_aliases_used.append("scene_distribution_contract.first_last_ratio")

        if ia2v_ratio_obj is not None and i2v_ratio_obj is not None and first_last_ratio_obj is not None:
            route_balance_from_object = ""
            if first_last_ratio_obj == 0 and ia2v_ratio_obj == 0.5 and i2v_ratio_obj == 0.5:
                route_balance_from_object = "balanced_50_50"
            elif first_last_ratio_obj == 0 and ia2v_ratio_obj == 0.7 and i2v_ratio_obj == 0.3:
                route_balance_from_object = "performance_heavy_70_30"
            elif first_last_ratio_obj == 0 and ia2v_ratio_obj == 0.3 and i2v_ratio_obj == 0.7:
                route_balance_from_object = "story_heavy_30_70"
            elif first_last_ratio_obj == 0 and ia2v_ratio_obj == 1.0 and i2v_ratio_obj == 0.0:
                route_balance_from_object = "all_lipsync"

            if route_balance_from_object:
                scene_distribution["route_balance"] = route_balance_from_object
                alias_normalization_applied = True
                route_balance_alias_normalized = True
                route_balance_from_ratio_object = True
                normalized_aliases_used.append("scene_distribution_contract.route_balance_from_ratio_object")

    if mapped_route_balance and mapped_route_balance != original_route_balance:
        scene_distribution["route_balance"] = mapped_route_balance
        alias_normalization_applied = True
        route_balance_alias_normalized = True
        normalized_aliases_used.append("scene_distribution_contract.route_balance")
    elif mapped_route_balance and not _first_non_empty_string(scene_distribution.get("route_balance")):
        scene_distribution["route_balance"] = mapped_route_balance
        alias_normalization_applied = True
        normalized_aliases_used.append("scene_distribution_contract.route_balance")

    final_route_balance = _first_non_empty_string(scene_distribution.get("route_balance")).lower()
    if final_route_balance == "balanced_50_50":
        if scene_distribution.get("ia2v_ratio") is None:
            scene_distribution["ia2v_ratio"] = 0.5
            alias_normalization_applied = True
            normalized_aliases_used.append("scene_distribution_contract.ia2v_ratio")
        if scene_distribution.get("i2v_ratio") is None:
            scene_distribution["i2v_ratio"] = 0.5
            alias_normalization_applied = True
            normalized_aliases_used.append("scene_distribution_contract.i2v_ratio")
        if route_balance_used_user_confirmation:
            scene_distribution["user_approved"] = True
            alias_normalization_applied = True
            normalized_aliases_used.append("scene_distribution_contract.user_approved")
    elif final_route_balance == "performance_heavy_70_30":
        scene_distribution["ia2v_ratio"] = 0.7
        scene_distribution["i2v_ratio"] = 0.3
        alias_normalization_applied = True
        normalized_aliases_used.extend(["scene_distribution_contract.ia2v_ratio", "scene_distribution_contract.i2v_ratio"])
    elif final_route_balance == "story_heavy_30_70":
        scene_distribution["ia2v_ratio"] = 0.3
        scene_distribution["i2v_ratio"] = 0.7
        alias_normalization_applied = True
        normalized_aliases_used.extend(["scene_distribution_contract.ia2v_ratio", "scene_distribution_contract.i2v_ratio"])
    elif final_route_balance == "all_lipsync":
        scene_distribution["ia2v_ratio"] = 1.0
        scene_distribution["i2v_ratio"] = 0.0
        alias_normalization_applied = True
        normalized_aliases_used.extend(["scene_distribution_contract.ia2v_ratio", "scene_distribution_contract.i2v_ratio"])

    ia2v_meaning = _first_non_empty_string(
        scene_distribution.get("ia2v_meaning"),
        clip_contract.get("performance_definition"),
        clip_contract.get("ia2v_definition"),
        route_contract.get("ia2v_route_purpose"),
    )
    if ia2v_meaning and not _first_non_empty_string(scene_distribution.get("ia2v_meaning")):
        scene_distribution["ia2v_meaning"] = ia2v_meaning
        alias_normalization_applied = True
        normalized_aliases_used.append("scene_distribution_contract.ia2v_meaning")

    i2v_meaning = _first_non_empty_string(
        scene_distribution.get("i2v_meaning"),
        clip_contract.get("story_cutaway_definition"),
        clip_contract.get("i2v_definition"),
        clip_contract.get("i2v_content_between_performances"),
        route_contract.get("i2v_route_purpose"),
    )
    if i2v_meaning and not _first_non_empty_string(scene_distribution.get("i2v_meaning")):
        scene_distribution["i2v_meaning"] = i2v_meaning
        alias_normalization_applied = True
        normalized_aliases_used.append("scene_distribution_contract.i2v_meaning")

    if prompt_policy and not _first_non_empty_string(prompt_policy.get("ltx_positive_prompt_rule")):
        prompt_policy["ltx_positive_prompt_rule"] = "Only describe what must be visible and moving."
        alias_normalization_applied = True
        normalized_aliases_used.append("prompt_policy.ltx_positive_prompt_rule")
    prompt_policy["negative_rules_are_internal"] = True
    prompt_policy["do_not_copy_negative_rules_into_ltx_positive_prompt"] = True

    contract["clip_contract"] = clip_contract
    contract["scene_distribution_contract"] = scene_distribution
    contract["prompt_policy"] = prompt_policy
    package["clip_contract"] = clip_contract
    package["scene_distribution_contract"] = scene_distribution
    package["prompt_policy"] = prompt_policy

    package["_clip_alias_normalization_diagnostics"] = {
        "director_clip_alias_normalization_applied": bool(alias_normalization_applied),
        "director_clip_aliases_used": sorted(set(normalized_aliases_used)),
        "director_clip_route_balance_alias_normalized": bool(route_balance_alias_normalized),
        "director_clip_route_balance_from_ratio_object": bool(route_balance_from_ratio_object),
    }
    return contract, package


def _build_director_v2_prompt(payload: dict[str, Any]) -> str:
    clip_mode_block = ""
    if _is_clip_music_video_payload(payload):
        clip_mode_block = """
CLIP MODE (ОБЯЗАТЕЛЬНО):
8) Режим клипа зафиксирован: mode=clip, mode_locked=true.
9) Не задавай вопрос "что мы делаем?" в clip mode (если mode уже известен и не auto/unknown).
10) Маршрут first_last запрещён всегда. Разрешены только ia2v и i2v.
11) ia2v = lip-sync/перформанс: певец виден, рот читаемый, эмоции лица/тела считываются.
12) i2v = сюжетные перебивки, воспоминания, действия, локации, атмосфера между перформансами.
13) Все недостающие клип-решения ты обязан спросить САМ в phase=questions.
14) Backend не будет добавлять вопросы за тебя. Не возвращай done=true, пока не собран полный clip contract (или пользователь явно делегировал решение AI).
15) Обязательно задать (или подтвердить из ответов) клип-специфичные решения:
    - баланс маршрутов (route balance),
    - что значит ia2v именно в этом клипе,
    - что значит i2v именно в этом клипе,
    - где происходит перформанс,
    - что показывает i2v между перформансами,
    - обязательные сцены,
    - интро,
    - аутро/финал,
    - как использовать референсы.
16) При done=true в director_package и director_contract обязательно включить:
    - mode_contract,
    - clip_contract,
    - scene_distribution_contract,
    - reference_usage_contract,
    - prompt_policy.
""".strip()
    return f"""
Ты AI Director V2 для видео-сториборда.

Правила:
1) Отвечай СТРОГО JSON без markdown.
2) Язык вопросов и assistant_message: русский.
3) Не используй статические шаблоны вопросов; задавай только сюжетно-специфичные вопросы из входного payload.
4) 3-8 вопросов обычно, максимум 15, если данных мало.
5) Если данных достаточно: phase=done и собери полный director_package/director_config/director_contract.
6) Не переписывай narrative пользователя, не выдумывай несвязанных персонажей.
7) Не добавляй LTX negative правила.
{clip_mode_block}

Схема phase=questions:
{{
  "phase":"questions",
  "assistant_message":"...",
  "story_understanding":{{
    "summary":"...",
    "detected_timelines":[],
    "detected_roles":{{}},
    "detected_conflicts_or_gaps":[]
  }},
  "questions":[
    {{
      "id":"snake_case",
      "label":"...",
      "type":"single_choice|multi_choice|free_text",
      "options":[{{"value":"...","label":"..."}}],
      "required":true,
      "applies_to":["core","roles","scenes","prompts","audio","final_video_prompt"]
    }}
  ],
  "answers":{{}},
  "missing_fields":[],
  "director_config_preview":{{}},
  "director_contract_preview":{{}},
  "director_package_preview":{{"package_version":"director_package_v2"}},
  "done":false
}}

Схема phase=done:
{{
  "phase":"done",
  "assistant_message":"Режиссура собрана. Можно запускать пайплайн.",
  "answers":{{}},
  "director_summary":"...",
  "director_package":{{
    "package_version":"director_package_v2",
    "story_intent":{{}},
    "timeline_contract":{{}},
    "character_contract":{{}},
    "reference_usage_contract":{{}},
    "route_contract":{{}},
    "performance_contract":{{}},
    "memory_contract":{{}},
    "audio_contract":{{}},
    "scene_contract":{{}},
    "prompt_contract":{{}},
    "video_reference_contract":{{}}
  }},
  "director_config":{{}},
  "director_contract":{{
    "hard_location_binding": false,
    "world_roles": {{}},
    "route_location_rules": {{}},
    "timeline_contract": {{}},
    "character_contract": {{}},
    "reference_usage_contract": {{}},
    "route_contract": {{}},
    "route_semantics": {{
      "ia2v": {{
        "meaning": "audio_sensitive_performance_or_lip_sync_scene",
        "allowed_role_functions": ["performer","speaker","singer"]
      }},
      "i2v": {{
        "meaning": "visual_story_or_memory_action_scene",
        "allowed_role_functions": ["memory_subject","action_subject","support_subject","episodic_visual_extra","environment_subject"]
      }}
    }},
    "role_usage_contract": {{
      "character_1": {{
        "function":"performer",
        "routes":["ia2v"],
        "reference_required":true,
        "identity_mode":"strict",
        "max_scene_count":null,
        "reason":"..."
      }}
    }},
    "scene_requirements":[
      {{
        "id":"req_01",
        "required":true,
        "expected_route":"ia2v",
        "expected_roles":["character_1"],
        "expected_role_functions":["performer"],
        "expected_world":"performance_world",
        "min_count":1,
        "max_count":null,
        "source_text":"...",
        "purpose":"..."
      }}
    ],
    "performance_contract": {{}},
    "memory_contract": {{}},
    "audio_contract": {{}},
    "prompt_contract": {{}},
    "video_reference_contract": {{}}
  }},
  "missing_fields":[],
  "done":true
}}

Текущий payload:
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def _extract_payload_refs(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    refs_by_role = _safe_dict(payload.get("context_refs") or payload.get("refs_by_role"))
    normalized_roles: dict[str, Any] = {}
    character_refs: dict[str, Any] = {}
    for role, value in refs_by_role.items():
        role_key = str(role or "").strip()
        if not role_key:
            continue
        role_value = value if isinstance(value, (list, dict, str)) else str(value)
        normalized_roles[role_key] = role_value
        if "character" in role_key.lower():
            character_refs[role_key] = role_value
    return normalized_roles, character_refs


def _reference_usage_has_character_mapping(reference_usage_contract: dict[str, Any]) -> bool:
    usage = _safe_dict(reference_usage_contract)
    if not usage:
        return False
    if (
        _first_non_empty_string(usage.get("character_roles"))
        or _safe_dict(usage.get("roles"))
        or _safe_list(usage.get("usage_rules"))
        or _safe_dict(usage.get("character_usage"))
    ):
        return True

    mapping_hints = ("character", "ref", "usage", "role", "hero", "girl", "person")

    def _is_meaningful(value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, dict):
            return len(value) > 0
        if isinstance(value, list):
            return len(value) > 0
        return False

    for key, value in usage.items():
        key_l = str(key or "").strip().lower()
        if not _is_meaningful(value):
            continue
        if any(hint in key_l for hint in mapping_hints):
            return True
        if isinstance(value, dict):
            nested_keys = [str(k or "").strip().lower() for k in value.keys()]
            if any(any(hint in nested for hint in mapping_hints) for nested in nested_keys):
                return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    nested_keys = [str(k or "").strip().lower() for k in item.keys()]
                    if any(any(hint in nested for hint in mapping_hints) for nested in nested_keys):
                        return True
    return False


def _validate_clip_director_contract(
    director_contract: dict[str, Any],
    director_package: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[bool, list[str], dict[str, Any]]:
    if not _is_clip_music_video_payload(payload):
        diagnostics = {
            "clip_contract_schema_checked": False,
            "clip_contract_valid": True,
            "clip_contract_missing_fields": [],
        }
        return True, [], diagnostics

    missing_fields: list[str] = []
    contract = _safe_dict(director_contract)
    package = _safe_dict(director_package)

    mode_contract = _safe_dict(package.get("mode_contract") or contract.get("mode_contract"))
    clip_contract = _safe_dict(package.get("clip_contract") or contract.get("clip_contract"))
    scene_distribution = _safe_dict(package.get("scene_distribution_contract") or contract.get("scene_distribution_contract"))
    reference_usage_contract = _safe_dict(package.get("reference_usage_contract") or contract.get("reference_usage_contract"))
    prompt_policy = _safe_dict(package.get("prompt_policy") or contract.get("prompt_policy"))

    allowed_route_set = set(_safe_nonempty_list(mode_contract.get("allowed_routes")))
    forbidden_routes = set(_safe_nonempty_list(mode_contract.get("forbidden_routes")))
    if str(mode_contract.get("mode") or "").strip().lower() != "clip":
        missing_fields.append("mode_contract.mode")
    if bool(mode_contract.get("mode_locked")) is not True:
        missing_fields.append("mode_contract.mode_locked")
    if allowed_route_set != {"ia2v", "i2v"}:
        missing_fields.append("mode_contract.allowed_routes")
    if "first_last" not in forbidden_routes:
        missing_fields.append("mode_contract.forbidden_routes")
    if bool(mode_contract.get("first_last_allowed")) is not False:
        missing_fields.append("mode_contract.first_last_allowed")

    if bool(clip_contract.get("audio_is_primary_clock")) is not True:
        missing_fields.append("clip_contract.audio_is_primary_clock")
    if str(clip_contract.get("performance_route") or "").strip().lower() != "ia2v":
        missing_fields.append("clip_contract.performance_route")
    if str(clip_contract.get("story_route") or "").strip().lower() != "i2v":
        missing_fields.append("clip_contract.story_route")
    if bool(clip_contract.get("first_last_allowed")) is not False:
        missing_fields.append("clip_contract.first_last_allowed")
    if not _first_non_empty_string(clip_contract.get("performance_definition")):
        missing_fields.append("clip_contract.performance_definition")
    if not _first_non_empty_string(clip_contract.get("story_cutaway_definition")):
        missing_fields.append("clip_contract.story_cutaway_definition")

    route_balance = _first_non_empty_string(scene_distribution.get("route_balance"))
    if not route_balance:
        missing_fields.append("scene_distribution_contract.route_balance")
    user_approved = bool(scene_distribution.get("user_approved"))
    user_approved_alias = _first_non_empty_string(
        scene_distribution.get("user_approved_or_ai_decides")
    ).lower()
    user_approved_via_alias = user_approved_alias in {"user_approved", "approved", "confirmed"}
    if not (user_approved or user_approved_via_alias or route_balance == "ai_decides"):
        missing_fields.append("scene_distribution_contract.user_approved_or_ai_decides")
    ia2v_ratio = scene_distribution.get("ia2v_ratio")
    i2v_ratio = scene_distribution.get("i2v_ratio")
    if route_balance not in CLIP_ROUTE_BALANCE_PRESETS:
        try:
            float(ia2v_ratio)
        except (TypeError, ValueError):
            missing_fields.append("scene_distribution_contract.ia2v_ratio")
        try:
            float(i2v_ratio)
        except (TypeError, ValueError):
            missing_fields.append("scene_distribution_contract.i2v_ratio")
    if _safe_float(scene_distribution.get("first_last_ratio"), -1) != 0:
        missing_fields.append("scene_distribution_contract.first_last_ratio")
    if bool(scene_distribution.get("first_last_allowed")) is not False:
        missing_fields.append("scene_distribution_contract.first_last_allowed")
    if not _first_non_empty_string(scene_distribution.get("ia2v_meaning")):
        missing_fields.append("scene_distribution_contract.ia2v_meaning")
    if not _first_non_empty_string(scene_distribution.get("i2v_meaning")):
        missing_fields.append("scene_distribution_contract.i2v_meaning")

    refs_by_role, character_refs = _extract_payload_refs(payload)
    if refs_by_role:
        if not reference_usage_contract:
            missing_fields.append("reference_usage_contract")
        else:
            if character_refs and not _reference_usage_has_character_mapping(reference_usage_contract):
                missing_fields.append("reference_usage_contract.character_usage")

    if bool(prompt_policy.get("negative_rules_are_internal")) is not True:
        missing_fields.append("prompt_policy.negative_rules_are_internal")
    if bool(prompt_policy.get("do_not_copy_negative_rules_into_ltx_positive_prompt")) is not True:
        missing_fields.append("prompt_policy.do_not_copy_negative_rules_into_ltx_positive_prompt")
    if not _first_non_empty_string(prompt_policy.get("ltx_positive_prompt_rule")):
        missing_fields.append("prompt_policy.ltx_positive_prompt_rule")

    valid = len(missing_fields) == 0
    diagnostics = {
        "clip_contract_schema_checked": True,
        "clip_contract_valid": valid,
        "clip_contract_missing_fields": missing_fields,
    }
    return valid, missing_fields, diagnostics


def _build_director_v2_retry_prompt(
    original_payload: dict[str, Any],
    previous_response: dict[str, Any],
    missing_fields: list[str],
) -> str:
    missing = [str(x).strip() for x in missing_fields if str(x).strip()]
    return f"""
Your previous response marked done=true, but clip contract validation failed.
Missing or invalid fields: {json.dumps(missing, ensure_ascii=False)}
You must not let backend create questions.
Return either:
A) phase='questions' with your own Russian user-facing questions for the missing decisions;
OR
B) phase='done' only if you provide a complete valid director_package/director_contract.

Repeat constraints:
- clip mode forbids first_last
- allowed routes are ia2v/i2v only
- you must ask missing questions yourself
- backend will not inject questions

Return strict JSON only.

Previous response:
{json.dumps(previous_response, ensure_ascii=False)}

Payload:
{json.dumps(original_payload, ensure_ascii=False)}
""".strip()


def _normalize_director_v2_output(parsed: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    phase = str(parsed.get("phase") or "questions").strip().lower()
    done = bool(parsed.get("done")) or phase == "done"
    answers = _safe_dict(parsed.get("answers"))
    questions = _safe_list(parsed.get("questions"))
    director_config = _safe_dict(parsed.get("director_config"))
    director_contract = _safe_dict(parsed.get("director_contract"))
    director_package = _safe_dict(parsed.get("director_package"))
    if done and not director_package:
        director_package = _safe_dict(parsed.get("director_package_preview"))
    if director_package:
        director_package["package_version"] = "director_package_v2"

    is_clip_music_video = _is_clip_music_video_payload(payload)
    if is_clip_music_video:
        director_contract, director_package, _ = _ensure_clip_mode_contracts(
            director_contract,
            director_package,
            questions,
            answers,
        )
        director_contract, director_package = _normalize_clip_contract_aliases(
            director_contract,
            director_package,
            answers,
        )

    if done:
        for field in (
            "timeline_contract", "character_contract", "reference_usage_contract", "route_contract",
            "performance_contract", "memory_contract", "audio_contract", "prompt_contract", "video_reference_contract",
        ):
            if field not in director_contract and field in director_package:
                director_contract[field] = director_package.get(field)
        director_contract = _ensure_director_v2_legacy_contract_fields(
            director_contract,
            director_config,
            director_package,
        )
        if not str(director_contract.get("source") or "").strip():
            director_contract["source"] = "ai_director_v2"
        director_contract, director_v2_contract_diagnostics = _ensure_director_v2_structured_contract_fields(
            director_contract,
            payload,
        )
    else:
        director_v2_contract_diagnostics = {
            "director_contract_v2_present": False,
            "director_contract_v2_role_usage_count": 0,
            "director_contract_v2_scene_requirements_count": 0,
            "director_contract_v2_route_semantics_present": False,
            "director_contract_v2_missing_fields": [],
        }

    scene_distribution_contract = _safe_dict(director_package.get("scene_distribution_contract") or director_contract.get("scene_distribution_contract"))
    clip_distribution_present = bool(scene_distribution_contract)
    clip_alias_normalization_diagnostics = _safe_dict(director_package.pop("_clip_alias_normalization_diagnostics", {}))
    clip_valid, clip_missing_fields, clip_schema_diagnostics = _validate_clip_director_contract(
        director_contract,
        director_package,
        payload,
    )
    provided_current_signature = str(payload.get("current_scenario_input_signature") or "").strip()
    current_signature = str(provided_current_signature or _compute_payload_scenario_input_signature(payload)).strip()
    signature_source = "provided_current_scenario_input_signature" if provided_current_signature else "backend_computed_hash"
    diagnostics = {
        "director_v2": True,
        "gemini_questions_generated": bool(questions) or done,
        "static_fallback_used": False,
        "input_has_audio": bool(_safe_dict(payload.get("metadata")).get("audio") or _safe_dict(payload.get("source")).get("source_mode") == "audio"),
        "input_has_video": str(_safe_dict(payload.get("source")).get("source_mode") or "") in {"video_file", "video_link"},
        "refs_roles_present": sorted(list(_safe_dict(payload.get("context_refs") or payload.get("refs_by_role")).keys())),
        "legacy_contract_normalized": bool(done),
        "legacy_world_roles_present": bool(_safe_dict(director_contract.get("world_roles"))),
        "legacy_route_location_rules_present": bool(_safe_dict(director_contract.get("route_location_rules"))),
        "legacy_hard_location_binding": bool(director_contract.get("hard_location_binding")),
        "director_clip_mode_contract_applied": bool(is_clip_music_video),
        "director_clip_first_last_forbidden": bool(is_clip_music_video),
        "director_clip_allowed_routes": ["ia2v", "i2v"] if is_clip_music_video else [],
        "director_clip_distribution_contract_present": bool(clip_distribution_present),
        "director_backend_question_injection_used": False,
        "director_ai_retry_used": False,
        "director_ai_retry_required": bool(is_clip_music_video and done and not clip_valid),
        "director_clip_contract_schema_checked": bool(clip_schema_diagnostics.get("clip_contract_schema_checked")),
        "director_clip_contract_valid": bool(clip_schema_diagnostics.get("clip_contract_valid")),
        "director_clip_contract_incomplete": bool(is_clip_music_video and not clip_valid),
        "director_clip_missing_fields": clip_missing_fields,
        "director_clip_alias_normalization_applied": bool(clip_alias_normalization_diagnostics.get("director_clip_alias_normalization_applied")),
        "director_clip_aliases_used": _safe_list(clip_alias_normalization_diagnostics.get("director_clip_aliases_used")),
        "director_clip_route_balance_alias_normalized": bool(clip_alias_normalization_diagnostics.get("director_clip_route_balance_alias_normalized")),
        "director_contract_v2_present": bool(director_v2_contract_diagnostics.get("director_contract_v2_present")),
        "director_contract_v2_role_usage_count": int(director_v2_contract_diagnostics.get("director_contract_v2_role_usage_count") or 0),
        "director_contract_v2_scene_requirements_count": int(director_v2_contract_diagnostics.get("director_contract_v2_scene_requirements_count") or 0),
        "director_contract_v2_route_semantics_present": bool(director_v2_contract_diagnostics.get("director_contract_v2_route_semantics_present")),
        "director_contract_v2_missing_fields": _safe_list(director_v2_contract_diagnostics.get("director_contract_v2_missing_fields")),
        "current_scenario_input_signature": current_signature,
        "signature_source": signature_source,
        "stored_director_signature": str(_director_artifact_signature(director_contract) or _director_artifact_signature(director_package)),
    }
    diagnostics["director_signature_matches_current"] = bool(
        not diagnostics["stored_director_signature"]
        or diagnostics["stored_director_signature"] == current_signature
    )
    diagnostics.setdefault("director_stale_contract_ignored", False)
    if done:
        director_contract["created_for_signature"] = current_signature
        director_package["created_for_signature"] = current_signature
        diagnostics["director_created_for_signature"] = current_signature
    assistant_message = str(parsed.get("assistant_message") or "").strip()
    if done and is_clip_music_video and clip_valid:
        assistant_message = "Режиссура клипа собрана. Можно запускать пайплайн."
    elif is_clip_music_video:
        assistant_message = "Нужно уточнить режиссуру клипа перед запуском пайплайна."
    elif not done:
        assistant_message = assistant_message or "Нужно уточнить режиссуру перед запуском пайплайна."
    return {
        "ok": True,
        "phase": "done" if done else "questions",
        "assistant_message": assistant_message,
        "questions": questions,
        "story_understanding": _safe_dict(parsed.get("story_understanding")),
        "answers": answers,
        "missing_fields": _safe_list(parsed.get("missing_fields")),
        "director_summary": str(parsed.get("director_summary") or "").strip(),
        "director_config": director_config,
        "director_contract": director_contract,
        "director_package": director_package,
        "director_created_for_signature": str(director_contract.get("created_for_signature") or director_package.get("created_for_signature") or current_signature),
        "current_scenario_input_signature": current_signature,
        "director_config_preview": _safe_dict(parsed.get("director_config_preview")),
        "director_contract_preview": _safe_dict(parsed.get("director_contract_preview")),
        "director_package_preview": _safe_dict(parsed.get("director_package_preview")),
        "done": done,
        "diagnostics": diagnostics,
    }


@router.post("/director/chat")
async def director_chat(payload: dict[str, Any]) -> dict[str, Any]:
    raw_payload = payload if isinstance(payload, dict) else {}
    if str(raw_payload.get("mode") or "").strip().lower() == "director_v2":
        provided_current_signature = str(raw_payload.get("current_scenario_input_signature") or "").strip()
        current_signature = str(
            provided_current_signature
            or _compute_payload_scenario_input_signature(raw_payload)
        ).strip()
        signature_source = "provided_current_scenario_input_signature" if provided_current_signature else "backend_computed_hash"
        raw_payload["current_scenario_input_signature"] = current_signature
        incoming_contract = _safe_dict(raw_payload.get("director_contract"))
        incoming_package = _safe_dict(raw_payload.get("director_package"))
        incoming_answers = _safe_dict(raw_payload.get("directorAnswers") or raw_payload.get("director_answers"))
        stored_signature = str(
            _director_artifact_signature(incoming_contract)
            or _director_artifact_signature(incoming_package)
            or raw_payload.get("director_created_for_signature")
            or ""
        ).strip()
        stale_contract_ignored = bool(stored_signature and current_signature and stored_signature != current_signature)
        if stale_contract_ignored:
            raw_payload["director_contract"] = {}
            raw_payload["director_package"] = {}
            raw_payload["directorAnswers"] = {}
            raw_payload["director_answers"] = {}
        raw_payload.setdefault("diagnostics", {})
        raw_payload["diagnostics"] = {
            **_safe_dict(raw_payload.get("diagnostics")),
            "current_scenario_input_signature": current_signature,
            "signature_source": signature_source,
            "stored_director_signature": stored_signature,
            "director_signature_matches_current": bool(not stored_signature or stored_signature == current_signature),
            "director_stale_contract_ignored": stale_contract_ignored,
            "stale_signature": stored_signature if stale_contract_ignored else "",
            "persisted_director_result_reused": bool(not stale_contract_ignored and bool(incoming_contract or incoming_package or incoming_answers)),
        }
        key_info = resolve_gemini_api_key()
        if not key_info.get("valid"):
            return {
                "ok": False,
                "error": f"gemini_key_invalid:{key_info.get('error') or 'missing'}",
                "fallback_used": False,
                "diagnostics": {
                    "director_v2": True,
                    "gemini_questions_generated": False,
                    "static_fallback_used": False,
                    "input_has_audio": False,
                    "input_has_video": False,
                    "refs_roles_present": [],
                },
            }
        prompt = _build_director_v2_prompt(raw_payload)
        gemini_body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.9,
                "responseMimeType": "application/json",
            },
        }
        result = post_generate_content(
            str(key_info.get("api_key") or ""),
            DIRECTOR_QUESTIONS_MODEL,
            gemini_body,
            timeout=60,
        )
        if result.get("__http_error__"):
            status = int(result.get("status") or 502)
            raise HTTPException(status_code=status if status > 0 else 502, detail="gemini_request_failed")
        parsed = _extract_json_object(_extract_text_from_gemini(result))
        if not isinstance(parsed, dict):
            retry_result = post_generate_content(
                str(key_info.get("api_key") or ""),
                DIRECTOR_QUESTIONS_MODEL,
                {
                    "contents": [{"parts": [{"text": f"{prompt}\n\nВерни valid JSON only. Без markdown."}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "topP": 0.9,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=60,
            )
            parsed = _extract_json_object(_extract_text_from_gemini(retry_result))
            if not isinstance(parsed, dict):
                return {
                    "ok": False,
                    "error": "gemini_invalid_json",
                    "fallback_used": False,
                    "diagnostics": {
                        "director_v2": True,
                        "gemini_questions_generated": False,
                        "static_fallback_used": False,
                        "input_has_audio": False,
                        "input_has_video": False,
                        "refs_roles_present": [],
                    },
                }
        normalized = _normalize_director_v2_output(parsed, raw_payload)
        normalized_diag = _safe_dict(normalized.get("diagnostics"))
        normalized_diag["director_stale_contract_ignored"] = bool(stale_contract_ignored)
        normalized_diag["stale_signature"] = stored_signature if stale_contract_ignored else ""
        normalized_diag["current_signature"] = current_signature
        normalized_diag["signature_source"] = signature_source
        normalized_diag["persisted_director_result_reused"] = bool(
            not stale_contract_ignored and bool(incoming_contract or incoming_package or incoming_answers)
        )
        normalized["diagnostics"] = normalized_diag
        diagnostics = _safe_dict(normalized.get("diagnostics"))
        is_clip_mode = _is_clip_music_video_payload(raw_payload)
        clip_missing_fields = _safe_list(diagnostics.get("director_clip_missing_fields"))
        retry_required = bool(
            is_clip_mode
            and (str(normalized.get("phase") or "").strip().lower() == "done" or bool(normalized.get("done")))
            and bool(diagnostics.get("director_clip_contract_schema_checked"))
            and not bool(diagnostics.get("director_clip_contract_valid"))
        )
        if retry_required:
            retry_prompt = _build_director_v2_retry_prompt(raw_payload, parsed, [str(x) for x in clip_missing_fields])
            retry_result = post_generate_content(
                str(key_info.get("api_key") or ""),
                DIRECTOR_QUESTIONS_MODEL,
                {
                    "contents": [{"parts": [{"text": retry_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.15,
                        "topP": 0.9,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=60,
            )
            retry_parsed = _extract_json_object(_extract_text_from_gemini(retry_result))
            if isinstance(retry_parsed, dict):
                retry_normalized = _normalize_director_v2_output(retry_parsed, raw_payload)
                retry_diag = _safe_dict(retry_normalized.get("diagnostics"))
                retry_valid = bool(retry_diag.get("director_clip_contract_valid"))
                retry_is_done = bool(retry_normalized.get("done")) or str(retry_normalized.get("phase") or "") == "done"
                retry_diag["director_ai_retry_used"] = True
                retry_diag["director_ai_retry_required"] = bool(retry_is_done and not retry_valid)
                retry_normalized["diagnostics"] = retry_diag
                if retry_is_done and retry_valid:
                    return retry_normalized
                retry_missing_fields = _safe_list(retry_diag.get("director_clip_missing_fields")) or clip_missing_fields
                retry_questions = _safe_list(retry_normalized.get("questions"))
                return {
                    **retry_normalized,
                    "phase": "questions",
                    "done": False,
                    "questions": retry_questions,
                    "assistant_message": "AI Director не завершил контракт клипа. Нужно уточнить режиссуру или повторить формирование.",
                    "diagnostics": {
                        **retry_diag,
                        "director_ai_retry_used": True,
                        "director_ai_retry_required": True,
                        "director_clip_contract_incomplete": True,
                        "director_clip_missing_fields": retry_missing_fields,
                    },
                }
            diagnostics["director_ai_retry_used"] = True
            diagnostics["director_ai_retry_required"] = True
            diagnostics["director_clip_contract_incomplete"] = True
            normalized["phase"] = "questions"
            normalized["done"] = False
            normalized["questions"] = []
            normalized["assistant_message"] = "AI Director не завершил контракт клипа. Нужно уточнить режиссуру или повторить формирование."
            normalized["diagnostics"] = diagnostics
            return normalized
        return normalized

    context = raw_payload.get("context") if isinstance(raw_payload.get("context"), dict) else {}
    messages = raw_payload.get("messages") if isinstance(raw_payload.get("messages"), list) else []
    director_state = raw_payload.get("director_state") if isinstance(raw_payload.get("director_state"), dict) else {}
    incoming_answers = director_state.get("answers") if isinstance(director_state, dict) else {}
    user_message = str(raw_payload.get("user_message") or "").strip()

    answers = _sanitize_director_answers(incoming_answers if isinstance(incoming_answers, dict) else {})
    missing_fields = _get_missing_director_fields(answers)
    if not missing_fields:
        return {
            "assistant_message": "",
            "answers": answers,
            "director_config_preview": build_director_config_from_answers(answers),
            "missing_fields": [],
            "done": True,
        }

    fallback_message = _fallback_question_for_field(missing_fields[0], context)
    if not user_message and not messages:
        return {
            "assistant_message": fallback_message,
            "answers": answers,
            "director_config_preview": build_director_config_from_answers(answers),
            "missing_fields": missing_fields,
            "done": False,
        }

    prompt = build_director_chat_prompt(context, messages, answers, user_message)
    key_info = resolve_gemini_api_key()
    if not key_info.get("valid"):
        raise HTTPException(status_code=500, detail=f"gemini_key_invalid:{key_info.get('error') or 'missing'}")

    result = post_generate_content(
        str(key_info.get("api_key") or ""),
        DIRECTOR_QUESTIONS_MODEL,
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.25,
                "topP": 0.9,
                "responseMimeType": "application/json",
            },
        },
        timeout=45,
    )
    if result.get("__http_error__"):
        status = int(result.get("status") or 502)
        raise HTTPException(status_code=status if status > 0 else 502, detail="gemini_request_failed")

    extracted_answers: dict[str, str] = {}
    assistant_message = fallback_message
    parsed = _extract_json_object(_extract_text_from_gemini(result))
    if isinstance(parsed, dict):
        candidate_answers = parsed.get("extracted_answers")
        if isinstance(candidate_answers, dict):
            for field, allowed in ALLOWED_DIRECTOR_VALUES.items():
                value = str(candidate_answers.get(field) or "").strip()
                if value in allowed:
                    extracted_answers[field] = value
        candidate_message = str(parsed.get("assistant_message") or "").strip()
        if candidate_message:
            assistant_message = candidate_message

    merged_answers = _sanitize_director_answers({**answers, **extracted_answers})
    missing_fields = _get_missing_director_fields(merged_answers)
    resolved_done = len(missing_fields) == 0

    if resolved_done:
        assistant_message = "Режиссура собрана ✅ Можно запускать общий пайплайн."
    else:
        assistant_message = _fallback_question_for_field(missing_fields[0], context)

    return {
        "assistant_message": assistant_message,
        "answers": merged_answers,
        "director_config_preview": build_director_config_from_answers(merged_answers),
        "missing_fields": missing_fields,
        "done": resolved_done,
    }


@router.post("/director/questions")
async def director_questions(payload: dict[str, Any]) -> dict[str, Any]:
    raw_payload = payload if isinstance(payload, dict) else {}
    if "context" in raw_payload and isinstance(raw_payload.get("context"), dict):
        context = raw_payload.get("context") or {}
        answers_so_far = raw_payload.get("answers_so_far")
        answers_so_far = answers_so_far if isinstance(answers_so_far, dict) else {}
    else:
        context = raw_payload
        answers_so_far = {}

    safe_answers = {}
    for k, v in answers_so_far.items():
        key = str(k).strip()
        value = str(v).strip()

        if key not in ALLOWED_IDS or not value:
            continue

        allowed_values = ALLOWED_VALUES_BY_ID.get(key, set())
        if value not in allowed_values:
            continue

        safe_answers[key] = value

    if all(qid in safe_answers for qid in ALLOWED_IDS):
        return {
            "done": True,
            "question": None,
            "answers_so_far": safe_answers,
            "director_config_preview": _build_director_config_preview(safe_answers),
        }

    fallback_question = build_fallback_director_question(context, safe_answers)
    if fallback_question is None:
        return {
            "done": True,
            "question": None,
            "answers_so_far": safe_answers,
            "director_config_preview": _build_director_config_preview(safe_answers),
        }

    prompt = build_director_prompt(context, safe_answers)

    key_info = resolve_gemini_api_key()
    if not key_info.get("valid"):
        raise HTTPException(status_code=500, detail=f"gemini_key_invalid:{key_info.get('error') or 'missing'}")

    result = post_generate_content(
        str(key_info.get("api_key") or ""),
        DIRECTOR_QUESTIONS_MODEL,
        {"contents": [{"parts": [{"text": prompt}]}]},
        timeout=45,
    )
    if result.get("__http_error__"):
        status = int(result.get("status") or 502)
        raise HTTPException(status_code=status if status > 0 else 502, detail="gemini_request_failed")

    parsed = _extract_json_object(_extract_text_from_gemini(result))
    candidate_question = None
    if isinstance(parsed, dict):
        candidate_question = parsed.get("question")
        if candidate_question is None:
            legacy_questions = parsed.get("questions")
            if isinstance(legacy_questions, list) and legacy_questions:
                candidate_question = legacy_questions[0]

    validated = _validate_question(candidate_question, safe_answers)
    expected_qid = get_next_director_question_id(safe_answers)

    if validated and validated.get("id") != expected_qid:
        validated = None

    question = validated or fallback_question
    if question is None:
        return {
            "done": True,
            "question": None,
            "answers_so_far": safe_answers,
            "director_config_preview": _build_director_config_preview(safe_answers),
        }

    return {
        "done": False,
        "question": question,
        "answers_so_far": safe_answers,
        "director_config_preview": _build_director_config_preview(safe_answers),
    }
