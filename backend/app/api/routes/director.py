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


def build_director_prompt(context: dict[str, Any], answers_so_far: dict[str, Any] | None = None) -> str:
    world_hint = str((context or {}).get("world_hint") or "generic").strip().lower()
    safe_answers = answers_so_far if isinstance(answers_so_far, dict) else {}
    if "performance_density" not in safe_answers:
        next_question_id = "performance_density"
    elif "world_mode" not in safe_answers:
        next_question_id = "world_mode"
    elif "intro_mode" not in safe_answers:
        next_question_id = "intro_mode"
    else:
        next_question_id = ""
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

    safe_answers = {
        str(k).strip(): str(v).strip()
        for k, v in answers_so_far.items()
        if str(k).strip() in ALLOWED_IDS and str(v).strip()
    }

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
