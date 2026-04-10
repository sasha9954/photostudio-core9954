from pathlib import Path
import logging
import os
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
logger = logging.getLogger(__name__)
LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}


def _is_valid_http_url(value: str | None) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return bool((parsed.netloc or "").strip())


def is_localhost_url(value: str | None) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        host = str(urlparse(raw).hostname or "").strip().lower()
    except Exception:
        return False
    return host in LOCALHOST_HOSTS


class Settings(BaseSettings):
    PS_ENV: str = "dev"
    SECRET_KEY: str = "dev-secret-change-me"
    DB_PATH: str = "app/app.db"
    PUBLIC_BASE_URL: str
    CORS_ALLOW_ORIGINS: str
    TOKEN_TTL_SECONDS: int = 60 * 60 * 24 * 14  # 14 days

    # Gemini / Engine
    GEMINI_API_KEY: str = ""
    GEMINI_IMAGE_MODEL: str = "gemini-3.1-flash-image-preview"
    GEMINI_VISION_MODEL: str = "gemini-2.5-flash"
    GEMINI_TEXT_MODEL: str = "gemini-3.1-pro-preview"
    GEMINI_TEXT_MODEL_FALLBACK: str = "gemini-3-flash-preview"
    GEMINI_TEXT_MODEL_FALLBACK_CHAIN: str = "gemini-3-flash-preview,gemini-2.5-pro"
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

    # Remote ComfyUI image-to-video
    COMFY_BASE_URL: str
    COMFY_LAB_URL: str = ""
    COMFY_OUTPUT_HANDOFF_STRATEGY: str = "backend_proxy"
    COMFY_UPLOAD_CONNECT_TIMEOUT_SEC: int = 10
    COMFY_UPLOAD_READ_TIMEOUT_SEC: int = 120
    COMFY_UPLOAD_MAX_ATTEMPTS: int = 2
    COMFY_PROMPT_CONNECT_TIMEOUT_SEC: int = 10
    COMFY_PROMPT_READ_TIMEOUT_SEC: int = 60
    COMFY_POLL_INTERVAL_SEC: int = 2
    COMFY_POLL_TIMEOUT_SEC: int = 600
    COMFY_DISABLE_PBAR_FOR_REMOTE: bool = True
    COMFY_DISABLE_PBAR_COMPAT_TOP_LEVEL: bool = True
    COMFY_IMAGE_VIDEO_WORKFLOW: str = "app/workflows/image-video-silent-directprompt.json"
    VIDEO_PROVIDER_DEFAULT: str = "kie"

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

    @field_validator("PUBLIC_BASE_URL", "COMFY_BASE_URL", "CORS_ALLOW_ORIGINS", mode="before")
    @classmethod
    def _required_non_empty(cls, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("must be set via environment and cannot be empty")
        return raw

    @property
    def cors_allow_origins_list(self) -> list[str]:
        values = []
        for item in str(self.CORS_ALLOW_ORIGINS or "").split(","):
            clean = item.strip().rstrip("/")
            if clean and _is_valid_http_url(clean):
                values.append(clean)
        # preserve order and remove duplicates
        return list(dict.fromkeys(values))

    @property
    def cors_allow_origins_invalid_list(self) -> list[str]:
        invalid = []
        for item in str(self.CORS_ALLOW_ORIGINS or "").split(","):
            clean = item.strip().rstrip("/")
            if clean and not _is_valid_http_url(clean):
                invalid.append(clean)
        return list(dict.fromkeys(invalid))


settings = Settings()

if not settings.cors_allow_origins_list:
    raise ValueError(
        "CORS_ALLOW_ORIGINS must contain at least one valid http(s) origin after filtering invalid entries"
    )


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

logger.info("[CONFIG] COMFY_BASE_URL=%s", str(settings.COMFY_BASE_URL).rstrip("/"))
logger.info("[CONFIG] PUBLIC_BASE_URL=%s", str(settings.PUBLIC_BASE_URL).rstrip("/"))
logger.info("[CONFIG] CORS_ALLOW_ORIGINS=%s", settings.cors_allow_origins_list)
if settings.cors_allow_origins_invalid_list:
    logger.warning(
        "[CONFIG WARNING] CORS_ALLOW_ORIGINS contains invalid entries (ignored)=%s",
        settings.cors_allow_origins_invalid_list,
    )
logger.info(
    "[CONFIG] is_public_base_localhost=%s",
    is_localhost_url(settings.PUBLIC_BASE_URL),
)
logger.info(
    "[CONFIG] is_comfy_base_localhost=%s",
    is_localhost_url(settings.COMFY_BASE_URL),
)
if is_localhost_url(settings.PUBLIC_BASE_URL):
    logger.warning(
        "[CONFIG PROBLEM] PUBLIC_BASE_URL points to localhost/loopback (%s). "
        "Remote Comfy lip-sync cannot fetch backend audio via localhost; set PUBLIC_BASE_URL "
        "to a reachable URL (e.g. Tailscale/MagicDNS/LAN/external).",
        str(settings.PUBLIC_BASE_URL).rstrip("/"),
    )
logger.info(
    "[CONFIG] COMFY_OUTPUT_HANDOFF_STRATEGY=%s",
    str(settings.COMFY_OUTPUT_HANDOFF_STRATEGY or "backend_proxy").strip().lower() or "backend_proxy",
)
if not _is_valid_http_url(settings.COMFY_BASE_URL):
    logger.warning(
        "[CONFIG WARNING] COMFY_BASE_URL looks invalid: %s",
        str(settings.COMFY_BASE_URL).rstrip("/"),
    )
