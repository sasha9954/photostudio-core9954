import os
from dataclasses import dataclass

@dataclass(frozen=True)
class EngineConfig:
    api_key: str
    image_model: str = "gemini-3.1-flash-image-preview"
    vision_model: str = "gemini-3.1-flash"  # для классификации/понимания

def load_engine_config() -> EngineConfig:
    def _norm_env(v: str) -> str:
        # Windows/.env иногда даёт BOM (\ufeff) или кавычки вокруг значения.
        v = (v or "").strip()
        v = v.lstrip("\ufeff")
        if (len(v) >= 2) and ((v[0] == v[-1]) and v[0] in ('"', "'")):
            v = v[1:-1].strip()
        return v

    api_key = _norm_env(os.getenv("GEMINI_API_KEY") or "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    image_model = _norm_env(os.getenv("GEMINI_IMAGE_MODEL") or "gemini-3.1-flash-image-preview")
    # Normalize Gemini model id: allow 'models/<id>' or '<id>'. Reject non-ASCII/spaces to avoid requests latin-1 header/url issues on Windows.
    image_model = image_model.replace('models/', '').strip()
    if (not image_model) or any((ord(ch) > 127) for ch in image_model) or any(ch.isspace() for ch in image_model):
        # Fallback to a safe default. If you want a specific model, set GEMINI_IMAGE_MODEL to e.g. gemini-3.1-flash-image-preview
        image_model = 'gemini-3.1-flash-image-preview'
    vision_model = _norm_env(os.getenv("GEMINI_VISION_MODEL") or "gemini-3.1-flash")
    vision_model = vision_model.replace('models/', '').strip()
    if (not vision_model) or any((ord(ch) > 127) for ch in vision_model) or any(ch.isspace() for ch in vision_model):
        vision_model = 'gemini-3.1-flash'
    return EngineConfig(api_key=api_key, image_model=image_model, vision_model=vision_model)
