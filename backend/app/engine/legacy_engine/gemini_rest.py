import json
import re
from typing import Any, Dict, Optional
import urllib.parse

class GeminiRestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: object | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

def _get_json(url: str, headers: dict) -> dict:
    try:
        r = requests.get(url, headers=headers, timeout=60)
    except requests.RequestException as e:
        raise GeminiRestError(f"Gemini request failed: {e}") from e
    try:
        data = r.json()
    except Exception:
        data = {"raw": (r.text or "")[:2000]}
    if not r.ok:
        raise GeminiRestError("Gemini API returned HTTP error", status_code=r.status_code, body=data)
    return data

def list_models(api_key: str) -> dict:
    base = "https://generativelanguage.googleapis.com/v1beta"
    headers = {"x-goog-api-key": api_key}
    return _get_json(f"{base}/models", headers=headers)

import requests


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

def _safe_model_id(model: str) -> str:
    """Normalize Gemini model id to a safe ASCII path segment.
    Accepts: "gemini-...", "models/gemini-...". Rejects displayName like "Nano Banana 2".
    """
    s = (model or "").strip()
    # Allow callers to pass full "models/..."
    if s.startswith("models/"):
        s = s[len("models/"):]
    # Must be ASCII (requests may choke on non-ASCII in URL construction on Windows)
    try:
        s.encode("ascii")
    except Exception:
        return "gemini-3.1-flash-image-preview"
    # Accept only expected ids
    if s.startswith("gemini-"):
        return s
    # Sometimes people paste a longer string that contains the id
    m = re.search(r"(gemini-[A-Za-z0-9.\-]+)", s)
    if m:
        return m.group(1)
    return "gemini-3.1-flash-image-preview"



def post_generate_content(api_key: str, model: str, body: Dict[str, Any], timeout: int = 90) -> Dict[str, Any]:
    """
    Calls Gemini generateContent using UTF-8 JSON (IMPORTANT for Cyrillic prompts on Windows).

    Returns JSON dict. On HTTP/transport error returns:
      {"__http_error__": True, "status": <int>, "text": <str>}
    """
    if not api_key:
        return {"__http_error__": True, "status": 0, "text": "GEMINI_API_KEY is empty"}

    model_id = _safe_model_id(model)
    # Quote to a safe ASCII URL segment (defensive)
    model_q = urllib.parse.quote(model_id, safe="A-Za-z0-9-._")
    url = f"{GEMINI_BASE}/models/{model_q}:generateContent"

    # IMPORTANT:
    # - Use json= (not data=) so requests encodes UTF-8 properly.
    # - Set explicit charset.
    # - Provide x-goog-api-key header (works same way as Veo).
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "x-goog-api-key": api_key,
    }

    try:
        r = requests.post(
            url,
            params={"key": api_key},  # keep as fallback; header is primary
            json=body,
            headers=headers,
            timeout=timeout,
        )
    except Exception as e:
        return {"__http_error__": True, "status": 0, "text": f"REQUEST_FAILED: {e}"}

    if not r.ok:
        # Try to extract a human readable error
        text = r.text or ""
        try:
            j = r.json()
            if isinstance(j, dict) and "error" in j and isinstance(j["error"], dict):
                msg = j["error"].get("message")
                if msg:
                    text = f"{msg} | raw={text}"
        except Exception:
            pass
        return {"__http_error__": True, "status": int(r.status_code), "text": text}

    try:
        return r.json()
    except Exception:
        return {"__http_error__": True, "status": int(r.status_code), "text": f"BAD_JSON_RESPONSE: {r.text}"}