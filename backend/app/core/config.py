from pydantic_settings import BaseSettings

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
    KIE_VIDEO_MODEL_CONTINUOUS: str = "kling-2.5/pro/image-to-video"
    KIE_VIDEO_MODEL_LIPSYNC: str = ""
    KIE_CALLBACK_URL: str = ""
    KIE_POLL_INTERVAL_SEC: int = 5
    KIE_POLL_TIMEOUT_SEC: int = 300

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

settings = Settings()
