import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.engine.comfy_brain_engine import run_comfy_plan, run_comfy_prompt_sync
from app.engine.comfy_reference_profile import build_reference_profiles
from app.engine.scenario_director_engine import ScenarioDirectorError, run_scenario_director

import json
import logging

router = APIRouter()
logger = logging.getLogger(__name__)
ALLOWED_AUDIO_STORY_MODES = {"lyrics_music", "music_only", "music_plus_text", "speech_narrative"}
ALLOWED_PLANNER_MODES = {"legacy", "gemini_only"}
ALLOWED_COMFY_GENRES = {"horror", "romance", "comedy", "drama", "action", "thriller", "noir", "dreamy", "melancholy", "fashion", "surreal", "performance", "experimental"}
ALLOWED_PROJECT_MODES = {"narration_first", "music_first", "hybrid"}
ALLOWED_INPUT_MODES = {"audio_first", "text_to_audio_first"}


class RefItemIn(BaseModel):
    url: str = ""
    name: str = ""
    roleType: str = ""




class ClipComfyPromptSyncIn(BaseModel):
    sourceText: str = ""
    sourceLang: str = "ru"
    targetLang: str = "en"
    promptType: str = "image"
    sceneContext: dict[str, Any] = Field(default_factory=dict)
    stylePreset: str = ""
    mode: str = ""

class ClipComfyPlanIn(BaseModel):
    mode: str = "clip"
    plannerMode: str = "legacy"
    output: str = "comfy image"
    stylePreset: str = "realism"
    genre: str = ""
    freezeStyle: bool = False
    text: str = ""
    storyText: str = ""
    inputMode: str | None = None
    projectMode: str = "narration_first"
    audioUrl: str = ""
    masterAudioUrl: str = ""
    audioDurationSec: float | None = None
    globalMusicTrackUrl: str = ""
    musicTrackUrl: str = ""
    refsByRole: dict[str, list[RefItemIn]] = Field(default_factory=dict)
    storyControlMode: str = ""
    storyMissionSummary: str = ""
    audioStoryMode: str = "lyrics_music"
    timelineSource: str = ""
    narrativeSource: str = ""
    lyricsText: str = ""
    transcriptText: str = ""
    spokenTextHint: str = ""
    audioSemanticHints: list[str] | dict[str, Any] | str | None = None
    audioSemanticSummary: str = ""
    plannerRules: dict[str, Any] = Field(default_factory=dict)
    plannerOverrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("audioStoryMode", mode="before")
    @classmethod
    def validate_audio_story_mode(cls, value: Any) -> str:
        normalized = str(value or "lyrics_music").strip().lower()
        return normalized if normalized in ALLOWED_AUDIO_STORY_MODES else "lyrics_music"

    @field_validator("plannerMode", mode="before")
    @classmethod
    def validate_planner_mode(cls, value: Any) -> str:
        normalized = str(value or "legacy").strip().lower()
        return normalized if normalized in ALLOWED_PLANNER_MODES else "legacy"

    @field_validator("genre", mode="before")
    @classmethod
    def validate_genre(cls, value: Any) -> str:
        raw = str(value or "").strip()
        return raw if raw.lower() in ALLOWED_COMFY_GENRES else ""

    @field_validator("projectMode", mode="before")
    @classmethod
    def validate_project_mode(cls, value: Any) -> str:
        normalized = str(value or "narration_first").strip().lower()
        return normalized if normalized in ALLOWED_PROJECT_MODES else "narration_first"

    @field_validator("inputMode", mode="before")
    @classmethod
    def validate_input_mode(cls, value: Any) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        normalized = str(value).strip().lower()
        return normalized if normalized in ALLOWED_INPUT_MODES else None


class ClipComfyConnectRefsIn(BaseModel):
    refsByRole: dict[str, list[RefItemIn]] = Field(default_factory=dict)


class ClipComfyAnalyzeRefIn(BaseModel):
    role: str = ""
    refs: list[RefItemIn] = Field(default_factory=list)


class ScenarioDirectorReferenceIn(BaseModel):
    label: str = ""
    source_label: str = ""
    preview: str = ""
    value: str = ""
    refs: list[str] = Field(default_factory=list)
    count: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)


class ScenarioDirectorSourceIn(BaseModel):
    source_mode: str = "audio"
    source_value: str = ""
    source_preview: str = ""
    source_label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_mode", mode="before")
    @classmethod
    def validate_source_mode(cls, value: Any) -> str:
        normalized = str(value or "audio").strip().lower()
        return normalized if normalized in {"audio", "video_file", "video_link"} else "audio"


class ScenarioDirectorControlsIn(BaseModel):
    contentType: str = "story"
    narrativeMode: str = "cinematic_expand"
    styleProfile: str = "realistic"
    directorNote: str = ""


class ScenarioDirectorGenerateIn(BaseModel):
    source: ScenarioDirectorSourceIn
    context_refs: dict[str, ScenarioDirectorReferenceIn] = Field(default_factory=dict)
    director_controls: ScenarioDirectorControlsIn = Field(default_factory=ScenarioDirectorControlsIn)
    connected_context_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


CONNECT_REFS_MAIN_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "props", "location", "style"]
ANIMAL_LABEL_BY_SPECIES = {
    "dog": "собака",
    "cat": "кот",
    "wolf": "волк",
    "horse": "лошадь",
    "bird": "птица",
}


def _extract_profile_tokens(profile: dict[str, Any] | None) -> str:
    source = profile if isinstance(profile, dict) else {}
    visual_profile = source.get("visualProfile") if isinstance(source.get("visualProfile"), dict) else {}
    fields: list[Any] = [
        source.get("entityType"),
        source.get("detectedEntityType"),
        source.get("expectedEntityType"),
        source.get("invariants"),
        source.get("forbiddenChanges"),
    ]
    fields.extend(list(visual_profile.values()))

    tokens: list[str] = []
    for value in fields:
        if isinstance(value, str):
            tokens.append(value)
        elif isinstance(value, list):
            tokens.extend([str(v) for v in value if isinstance(v, (str, int, float))])
        elif isinstance(value, dict):
            tokens.extend([str(v) for v in value.values() if isinstance(v, (str, int, float))])
        elif isinstance(value, (int, float)):
            tokens.append(str(value))
    return " ".join(tokens).strip().lower()


def _has_any_token(tokens: str, variants: list[str]) -> bool:
    for variant in variants:
        needle = str(variant).strip().lower()
        if not needle:
            continue
        if " " in needle:
            if needle in tokens:
                return True
            continue
        if needle.isascii() and needle.isalpha():
            if re.search(rf"\b{re.escape(needle)}\b", tokens):
                return True
            continue
        if needle in tokens:
            return True
    return False


def _build_human_label(profile: dict[str, Any] | None) -> str:
    source = profile if isinstance(profile, dict) else {}
    visual_profile = source.get("visualProfile") if isinstance(source.get("visualProfile"), dict) else {}
    raw_gender = (
        visual_profile.get("genderPresentation")
        or source.get("genderPresentation")
        or visual_profile.get("gender")
        or source.get("gender")
        or ""
    )
    gender = str(raw_gender).strip().lower()

    tokens = f"{_extract_profile_tokens(profile)} {gender}".strip()

    female_child_tokens = [
        "little girl",
        "young girl",
        "female child",
        "female kid",
        "девочка",
        "ребёнок женского пола",
        "girl",
    ]
    male_child_tokens = [
        "little boy",
        "young boy",
        "male child",
        "male kid",
        "мальчик",
        "ребёнок мужского пола",
        "boy",
    ]
    common_child_tokens = [
        "teenager neutral",
        "teenager",
        "toddler",
        "child",
        "kid",
        "teen",
        "ребёнок",
        "подросток",
    ]
    adult_female_tokens = [
        "young woman",
        "adult woman",
        "feminine",
        "female",
        "woman",
        "женщина",
        "девушка",
    ]
    adult_male_tokens = [
        "young man",
        "adult man",
        "masculine",
        "male",
        "man",
        "мужчина",
        "парень",
    ]

    if _has_any_token(tokens, female_child_tokens):
        return "девочка"
    if _has_any_token(tokens, male_child_tokens):
        return "мальчик"
    if _has_any_token(tokens, common_child_tokens):
        return "ребёнок"
    if _has_any_token(tokens, adult_female_tokens):
        return "женщина"
    if _has_any_token(tokens, adult_male_tokens):
        return "мужчина"
    return "персонаж"


def _build_animal_label(profile: dict[str, Any] | None) -> str:
    source = profile if isinstance(profile, dict) else {}
    visual_profile = source.get("visualProfile") if isinstance(source.get("visualProfile"), dict) else {}
    locked_species = str(
        visual_profile.get("speciesLock")
        or visual_profile.get("species")
        or source.get("speciesLock")
        or source.get("species")
        or ""
    ).strip().lower()
    if locked_species in ANIMAL_LABEL_BY_SPECIES:
        return ANIMAL_LABEL_BY_SPECIES[locked_species]

    tokens = _extract_profile_tokens(profile)
    if _has_any_token(tokens, ["собак", "dog", "canine", "puppy", "hound"]):
        return "собака"
    if _has_any_token(tokens, ["волк", "wolf"]):
        return "волк"
    if _has_any_token(tokens, ["кот", "кошка", "cat", "feline", "kitten"]):
        return "кот"
    if _has_any_token(tokens, ["лошад", "horse", "equine"]):
        return "лошадь"
    if _has_any_token(tokens, ["птиц", "bird", "avian"]):
        return "птица"
    return "животное"


def _build_props_label(profile: dict[str, Any] | None) -> str:
    tokens = _extract_profile_tokens(profile)
    if _has_any_token(tokens, ["motorcycle", "motorbike", "мотоцикл"]):
        return "мотоцикл"
    if _has_any_token(tokens, ["bicycle", "bike", "велосипед"]):
        return "велосипед"
    if _has_any_token(tokens, ["машин", "авто", "car", "automobile", "sedan", "suv", "coupe", "hatchback", "truck", "pickup", "van"]):
        return "машина"
    if _has_any_token(tokens, ["photo camera", "mirrorless", "dslr", "camera", "камера", "фотоаппарат"]):
        return "камера"
    if _has_any_token(tokens, ["smartphone", "android phone", "mobile phone", "iphone", "phone", "телефон", "смартфон"]):
        return "телефон"
    if _has_any_token(tokens, ["laptop", "notebook computer", "computer", "desktop", "pc", "ноутбук", "компьютер"]):
        return "компьютер"
    if _has_any_token(tokens, ["shotgun", "rifle", "pistol", "gun", "weapon", "оружие", "пистолет", "винтовка"]):
        return "оружие"
    if _has_any_token(tokens, ["drill", "hammer", "wrench", "saw", "tool", "инструмент", "дрель", "молоток"]):
        return "инструмент"
    if _has_any_token(tokens, ["device", "gadget", "machine", "equipment", "техника", "устройство", "оборудование", "tech"]):
        return "техника"
    return "предмет"


def _build_location_label(profile: dict[str, Any] | None) -> str:
    tokens = _extract_profile_tokens(profile)
    if _has_any_token(tokens, ["марс", "mars", "martian"]):
        return "Марс"
    if _has_any_token(tokens, ["spaceship", "orbital", "sci-fi planet surface", "space", "космос"]):
        return "космос"
    if _has_any_token(tokens, ["cityscape", "downtown", "street", "city", "urban", "город", "улица"]):
        return "город"
    if _has_any_token(tokens, ["home interior", "living room", "bedroom", "kitchen", "apartment", "flat", "квартир", "дом", "комната"]):
        return "квартира"
    if _has_any_token(tokens, ["interior workspace", "studio interior", "hallway", "office", "офис", "кабинет", "студия"]):
        return "помещение"
    if _has_any_token(tokens, ["лес", "forest", "woodland", "jungle", "trees", "джунгли"]):
        return "лес"
    if _has_any_token(tokens, ["desert", "dunes", "barren sand", "пустыня"]):
        return "пустыня"
    if _has_any_token(tokens, ["riverbank", "beach", "shore", "coast", "ocean", "sea", "море", "берег", "пляж", "река"]):
        return "берег"
    return "локация"


def _build_style_label(profile: dict[str, Any] | None) -> str:
    tokens = _extract_profile_tokens(profile)
    if _has_any_token(tokens, ["synthwave", "cyberpunk", "glow", "neon", "неон"]):
        return "неон"
    if _has_any_token(tokens, ["movie-like", "filmic", "cinematic", "cinema", "кино"]):
        return "кино"
    if _has_any_token(tokens, ["photorealistic", "photoreal", "realistic", "realism", "naturalistic", "реализм", "реал"]):
        return "реализм"
    if _has_any_token(tokens, ["dreamy", "pastel", "gentle", "soft", "мягкий"]):
        return "мягкий"
    if _has_any_token(tokens, ["fashion glossy", "polished commercial", "glossy", "глянец"]):
        return "глянец"
    return "стиль"


def _build_short_label_for_role(role: str, profile: dict[str, Any] | None) -> str:
    if role in {"character_1", "character_2", "character_3"}:
        return _build_human_label(profile)
    if role == "animal":
        return _build_animal_label(profile)
    if role == "group":
        return "группа"
    if role == "props":
        return _build_props_label(profile)
    if role == "location":
        return _build_location_label(profile)
    if role == "style":
        return _build_style_label(profile)
    return "реф"


@router.post("/clip/comfy/plan")
async def clip_comfy_plan(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    raw_body_bytes = await request.body()
    raw_body_text = raw_body_bytes.decode("utf-8", errors="replace")

    logger.info("[clip_comfy_plan] content-type=%s", content_type)
    logger.info("[clip_comfy_plan] raw-body=%s", raw_body_text)

    parsed_json: Any = None
    try:
        parsed_json = json.loads(raw_body_text)
        logger.info("[clip_comfy_plan] parsed-json=%s", parsed_json)
    except Exception as exc:
        logger.exception("[clip_comfy_plan] json-parse-error=%s", exc)
        raise HTTPException(status_code=422, detail=f"Invalid JSON body: {exc}") from exc

    try:
        payload = ClipComfyPlanIn.model_validate(parsed_json or {})
    except ValidationError as exc:
        logger.exception("[clip_comfy_plan] pydantic-validation-error=%s", exc)
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    req = payload.model_dump(mode="json")
    req["refsByRole"] = {
        role: [item.model_dump(mode="json") for item in items]
        for role, items in (payload.refsByRole or {}).items()
    }
    logger.info(
        "[clip_comfy_plan] normalized-audioStoryMode=%s text=%s lyricsText=%s transcriptText=%s spokenHint=%s semanticHints=%s semanticSummary=%s",
        req.get("audioStoryMode"),
        bool(req.get("text")),
        bool(req.get("lyricsText")),
        bool(req.get("transcriptText")),
        bool(req.get("spokenTextHint")),
        bool(req.get("audioSemanticHints")),
        bool(req.get("audioSemanticSummary")),
    )
    return run_comfy_plan(req)


@router.post("/clip/comfy/scenario-director/generate")
async def clip_comfy_scenario_director_generate(payload: ScenarioDirectorGenerateIn) -> dict[str, Any]:
    req = payload.model_dump(mode="json")
    try:
        return run_scenario_director(req)
    except ScenarioDirectorError as exc:
        detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.details:
            detail["details"] = exc.details
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.post("/clip/comfy/prompt-sync")
async def clip_comfy_prompt_sync(payload: ClipComfyPromptSyncIn) -> dict[str, Any]:
    req = payload.model_dump(mode="json")
    return run_comfy_prompt_sync(req)


@router.post("/clip/comfy/connect-refs")
async def clip_comfy_connect_refs(payload: ClipComfyConnectRefsIn) -> dict[str, Any]:
    refs_by_role = {
        role: [item.model_dump(mode="json") for item in items]
        for role, items in (payload.refsByRole or {}).items()
    }
    filtered_refs_by_role: dict[str, list[dict[str, Any]]] = {}
    for role in CONNECT_REFS_MAIN_ROLES:
        role_items = refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []
        clean_items = [item for item in role_items if isinstance(item, dict) and str(item.get("url") or "").strip()]
        if clean_items:
            filtered_refs_by_role[role] = clean_items

    if not filtered_refs_by_role:
        return {
            "ok": True,
            "connectedRefsSummary": [],
            "referenceProfiles": {},
        }

    reference_profiles = build_reference_profiles(filtered_refs_by_role)
    connected_refs_summary: list[dict[str, str]] = []
    for role in CONNECT_REFS_MAIN_ROLES:
        role_profile = reference_profiles.get(role) if isinstance(reference_profiles.get(role), dict) else None
        if not role_profile:
            continue
        connected_refs_summary.append(
            {
                "role": role,
                "label": _build_short_label_for_role(role, role_profile),
            }
        )
    logger.info("[clip_comfy_connect_refs] connected roles=%s", [item.get("role") for item in connected_refs_summary])

    return {
        "ok": True,
        "connectedRefsSummary": connected_refs_summary,
        "referenceProfiles": reference_profiles,
    }


@router.post("/clip/comfy/analyze-ref-node")
async def clip_comfy_analyze_ref_node(payload: ClipComfyAnalyzeRefIn) -> dict[str, Any]:
    role = str(payload.role or "").strip().lower()
    if role not in CONNECT_REFS_MAIN_ROLES:
        raise HTTPException(status_code=422, detail="invalid_ref_role")

    refs = [
        item.model_dump(mode="json") for item in (payload.refs or [])
        if str(item.url or "").strip()
    ]
    if not refs:
        raise HTTPException(status_code=422, detail="empty_ref_list")

    profiles = build_reference_profiles({role: refs})
    profile = profiles.get(role) if isinstance(profiles.get(role), dict) else None
    if not profile:
        raise HTTPException(status_code=500, detail="ref_profile_build_failed")

    return {
        "ok": True,
        "role": role,
        "shortLabel": _build_short_label_for_role(role, profile),
        "profile": profile,
    }
