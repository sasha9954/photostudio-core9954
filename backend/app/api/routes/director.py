from __future__ import annotations

import json
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


@router.post("/director/chat")
async def director_chat(payload: dict[str, Any]) -> dict[str, Any]:
    raw_payload = payload if isinstance(payload, dict) else {}
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
        _ = bool(parsed.get("done") is True)

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
