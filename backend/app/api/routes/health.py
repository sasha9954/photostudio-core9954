from fastapi import APIRouter

from app.core.config import ENV_FILE, settings

router = APIRouter()


@router.get("/health")
def health():
    api_key_present = bool((settings.GEMINI_API_KEY or "").strip())
    source = "environment" if __import__("os").getenv("GEMINI_API_KEY") else ".env" if ENV_FILE.exists() else "missing"
    return {
        "ok": True,
        "version": "0.2.0",
        "geminiConfigured": api_key_present,
        "geminiModel": settings.GEMINI_TEXT_MODEL,
        "geminiConfigSource": source,
    }
