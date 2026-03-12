from pathlib import Path
import logging
import os

from pydantic_settings import BaseSettings


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    PS_ENV: str = "dev"
    SECRET_KEY: str = "dev-secret-change-me"
    DB_PATH: str = "app/app.db"
    PUBLIC_BASE_URL: str = "http://127.0.0.1:8000"
    TOKEN_TTL_SECONDS: int = 60 * 60 * 24 * 14  # 14 days

    # Gemini / Engine
    GEMINI_API_KEY: str = ""
    GEMINI_IMAGE_MODEL: str = "gemini-3.1-flash-image-preview"
    GEMINI_VISION_MODEL: str = "gemini-2.5-flash"
    GEMINI_TEXT_MODEL: str = "gemini-2.5-flash"
    ENGINE_DEBUG: bool = False

    # KIE / Kling video generation
    KIE_API_KEY: str = ""
    KIE_BASE_URL: str = "https://api.kie.ai/api/v1"
    KIE_VIDEO_MODEL_SINGLE: str = "kling-2.6/image-to-video"
    KIE_VIDEO_MODEL_CONTINUOUS: str = "kling/v2-5-turbo-image-to-video-pro"
    KIE_VIDEO_MODEL_LIPSYNC: str = "kling/ai-avatar-pro"
    KIE_CALLBACK_URL: str = ""
    KIE_POLL_INTERVAL_SEC: int = 5
    KIE_POLL_TIMEOUT_SEC: int = 300

    # PiAPI / OmniHuman lip-sync generation
    PIAPI_API_KEY: str = ""
    PIAPI_BASE_URL: str = "https://api.piapi.ai/api/v1"
    PIAPI_OMNIHUMAN_MODEL: str = "omni-human"
    PIAPI_OMNIHUMAN_TASK: str = "omni-human-1.5"
    PIAPI_POLL_INTERVAL_SEC: int = 5
    PIAPI_POLL_TIMEOUT_SEC: int = 300

    model_config = {
        "env_file": str(ENV_FILE),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()


def _gemini_key_source() -> str:
    if os.getenv("GEMINI_API_KEY"):
        return "environment"
    if ENV_FILE.exists():
        return ".env"
    return "missing"


logger.info(
    "[CONFIG] GEMINI key status=%s source=%s model_text=%s model_vision=%s model_image=%s",
    "found" if bool((settings.GEMINI_API_KEY or "").strip()) else "missing",
    _gemini_key_source(),
    settings.GEMINI_TEXT_MODEL,
    settings.GEMINI_VISION_MODEL,
    settings.GEMINI_IMAGE_MODEL,
)
