from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from app.engine.gemini_rest import post_generate_content, resolve_gemini_api_key

router = APIRouter()

DIRECTOR_QUESTIONS_MODEL = "gemini-2.5-flash"


def build_director_prompt(context: dict[str, Any]) -> str:
    return f"""
You are an AI Director.

Based on the context, generate max 3 questions.

Each question must:
* be multiple choice
* be relevant to context (train, club, city, etc.)
* help define:
  * performance density
  * location usage
  * intro style

Rules:
* max 3 questions
* no free text answers
* must always return structured JSON

Return JSON:
{{
  "questions": [
    {{
      "id": "performance_density",
      "text": "...",
      "options": [
        {{ "label": "...", "value": "..." }}
      ]
    }}
  ]
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


@router.post("/director/questions")
async def director_questions(payload: dict[str, Any]) -> dict[str, Any]:
    context = payload if isinstance(payload, dict) else {}
    prompt = build_director_prompt(context)

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
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="gemini_invalid_json")

    safe_questions: list[dict[str, Any]] = []
    for item in (parsed.get("questions") if isinstance(parsed.get("questions"), list) else []):
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip() or f"q_{len(safe_questions) + 1}"
        text = str(item.get("text") or "").strip()
        options_raw = item.get("options")
        options: list[dict[str, str]] = []
        if isinstance(options_raw, list):
            for opt in options_raw:
                if not isinstance(opt, dict):
                    continue
                label = str(opt.get("label") or "").strip()
                value = str(opt.get("value") or "").strip()
                if label and value:
                    options.append({"label": label, "value": value})
        if text and options:
            safe_questions.append({"id": qid, "text": text, "options": options[:4]})
        if len(safe_questions) >= 3:
            break

    return {"questions": safe_questions}
