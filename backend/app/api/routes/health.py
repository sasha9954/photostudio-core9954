from fastapi import APIRouter
import os

from app.core.config import ENV_FILE, settings

router = APIRouter()


@router.get("/health")
def health():
    gemini_key_settings = (settings.GEMINI_API_KEY or "").strip()
    gemini_key_env = (os.getenv("GEMINI_API_KEY") or "").strip()
    gemini_env_file_exists = ENV_FILE.exists()

    source_resolved = "missing"
    if gemini_key_env:
        source_resolved = "environment"
    elif gemini_key_settings and gemini_env_file_exists:
        source_resolved = "env_file"

    return {
        "ok": True,
        "version": "0.2.0",
        "geminiConfigured": bool(gemini_key_settings),
        "geminiModel": settings.GEMINI_TEXT_MODEL,
        "geminiEnvVarPresent": bool(gemini_key_env),
        "geminiEnvFileExists": gemini_env_file_exists,
        "geminiConfigSourceResolved": source_resolved,
    }
