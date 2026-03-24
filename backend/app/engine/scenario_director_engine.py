import ast
import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.core.config import settings
from app.engine.gemini_rest import post_generate_content

ALLOWED_SOURCE_MODES = {"audio", "video_file", "video_link"}
ALLOWED_LTX_MODES = {"i2v", "i2v_as", "f_l", "f_l_as", "continuation", "lip_sync"}
ALLOWED_NARRATION_MODES = {"full", "duck", "pause"}
DEFAULT_TEXT_MODEL = (getattr(settings, "GEMINI_TEXT_MODEL", None) or "gemini-3.1-pro-preview").strip() or "gemini-3.1-pro-preview"
FALLBACK_TEXT_MODEL = (getattr(settings, "GEMINI_TEXT_MODEL_FALLBACK", None) or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
LEGACY_START_FRAME_ALIASES = {
    "previous_last_frame": "previous_frame",
    "prev_last": "previous_frame",
    "last_frame": "previous_frame",
}
JSON_ONLY_RETRY_SUFFIX = (
    "\n\nRETRY OVERRIDE: Output ONLY one JSON object. No markdown. No commentary. No comments. "
    "No alternative versions. Keep the same backend contract and return flat scene fields. "
    "HARD CONTRACT: narration_mode must be present in every scene, must be a string, must never be null, and allowed values are full, duck, pause. "
    "If unsure use full."
)

logger = logging.getLogger(__name__)

WEAK_SCENE_PATTERNS = (
    "character walks",
    "walks with determination",
    "camera follows",
    "tense cinematic moment",
    "mysterious atmosphere",
    "cinematic scene",
    "dramatic shot",
    "the subject moves",
    "the character looks around",
    "moody lighting",
    "suspenseful moment",
)
GENERIC_SCENE_GOALS = {
    "",
    "scene",
    "moment",
    "transition",
    "build mood",
    "set tone",
    "cinematic moment",
    "dramatic moment",
}
SCENE_PURPOSES = (
    "hook",
    "entry",
    "destabilization",
    "reveal",
    "escalation",
    "confrontation",
    "transition",
    "peak image",
    "emotional climax",
    "final image / ending hold",
)
TIMELINE_START_TOLERANCE_SEC = 0.35
TIMELINE_END_TOLERANCE_SEC = 0.75
TIMELINE_INTERNAL_GAP_WARN_SEC = 1.25
TIMELINE_TAIL_WARN_SEC = 1.0
TIMELINE_COVERAGE_RATIO_WARN = 0.95


class ScenarioDirectorError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ScenarioDirectorScene(BaseModel):
    scene_id: str
    time_start: float = 0.0
    time_end: float = 0.0
    duration: float = 0.0
    actors: list[str] = Field(default_factory=list)
    location: str = ""
    props: list[str] = Field(default_factory=list)
    emotion: str = ""
    scene_goal: str = ""
    frame_description: str = ""
    action_in_frame: str = ""
    camera: str = ""
    image_prompt: str = ""
    video_prompt: str = ""
    ltx_mode: str = "i2v"
    ltx_reason: str = ""
    start_frame_source: str = "new"
    needs_two_frames: bool = False
    continuation_from_previous: bool = False
    narration_mode: str = "full"
    local_phrase: str | None = None
    sfx: str = ""
    music_mix_hint: str = "off"

    @field_validator("scene_id", mode="before")
    @classmethod
    def _validate_scene_id(cls, value: Any) -> str:
        clean = str(value or "").strip()
        return clean or "S1"

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorScene":
        self.time_start = _safe_float(self.time_start, 0.0)
        self.time_end = _safe_float(self.time_end, self.time_start)
        self.duration = _safe_float(self.duration, max(0.0, self.time_end - self.time_start))
        if self.duration <= 0 and self.time_end > self.time_start:
            self.duration = round(self.time_end - self.time_start, 3)
        if self.time_end <= self.time_start and self.duration > 0:
            self.time_end = round(self.time_start + self.duration, 3)
        self.actors = [str(item).strip() for item in (self.actors or []) if str(item).strip()]
        self.props = [str(item).strip() for item in (self.props or []) if str(item).strip()]
        self.location = str(self.location or "").strip()
        self.emotion = str(self.emotion or "").strip()
        self.scene_goal = str(self.scene_goal or "").strip()
        self.frame_description = str(self.frame_description or "").strip()
        self.action_in_frame = str(self.action_in_frame or "").strip()
        self.camera = str(self.camera or "").strip()
        self.image_prompt = str(self.image_prompt or "").strip()
        self.video_prompt = str(self.video_prompt or "").strip()
        self.narration_mode = str(self.narration_mode or "full").strip() or "full"
        self.start_frame_source = _normalize_start_frame_source(self.start_frame_source, continuation=self.continuation_from_previous)
        self.needs_two_frames = _coerce_bool(self.needs_two_frames, False)
        self.continuation_from_previous = _coerce_bool(self.continuation_from_previous, False)
        self.ltx_mode = _normalize_ltx_mode(
            self.ltx_mode,
            continuation=self.continuation_from_previous,
            needs_two_frames=self.needs_two_frames,
            narration_mode=self.narration_mode,
        )
        self.local_phrase = str(self.local_phrase).strip() if self.local_phrase is not None and str(self.local_phrase).strip() else None
        self.sfx = _stringify_sfx(self.sfx)
        self.music_mix_hint = str(self.music_mix_hint or "off").strip() or "off"
        self.ltx_reason = _normalize_ltx_reason(
            str(self.ltx_reason or "").strip(),
            self.ltx_mode,
            narration_mode=self.narration_mode,
        )
        return self


class ScenarioDirectorStoryboardOut(BaseModel):
    story_summary: str = ""
    full_scenario: str = ""
    voice_script: str = ""
    music_prompt: str = ""
    director_summary: str = ""
    scenes: list[ScenarioDirectorScene] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorStoryboardOut":
        self.story_summary = str(self.story_summary or "").strip()
        self.full_scenario = str(self.full_scenario or "").strip()
        self.voice_script = str(self.voice_script or "").strip()
        self.music_prompt = str(self.music_prompt or "").strip()
        self.director_summary = str(self.director_summary or "").strip()
        if not self.scenes:
            raise ValueError("director output must contain at least one scene")
        return self


def _safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return fallback
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return fallback
    return round(parsed, 3)


def _coerce_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    clean = str(value).strip().lower()
    if clean in {"true", "1", "yes", "y", "on"}:
        return True
    if clean in {"false", "0", "no", "n", "off", "null", "none", ""}:
        return False
    return fallback


def _stringify_sfx(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _normalize_start_frame_source(value: Any, *, continuation: bool = False) -> str:
    clean = str(value or "").strip()
    if not clean and continuation:
        return "previous_frame"
    normalized = LEGACY_START_FRAME_ALIASES.get(clean, clean)
    if normalized in {"new", "first_frame", "previous_frame", "generated"}:
        return normalized
    if continuation:
        return "previous_frame"
    if normalized == "":
        return "new"
    return normalized


def _normalize_ltx_mode(value: Any, *, continuation: bool, needs_two_frames: bool, narration_mode: str) -> str:
    clean = str(value or "").strip()
    if clean in ALLOWED_LTX_MODES:
        if clean == "lip_sync" and not _is_music_vocal_mode(narration_mode):
            return "i2v_as"
        return clean
    if continuation:
        return "continuation"
    if needs_two_frames:
        return "f_l"
    return "i2v"


def _normalize_ltx_reason(reason: str, ltx_mode: str, *, narration_mode: str) -> str:
    if reason:
        if ltx_mode == "lip_sync" and not _is_music_vocal_mode(narration_mode):
            return f"{reason}; normalized from lip_sync because narration is not music-vocal driven"
        return reason
    defaults = {
        "i2v": "Static or atmospheric scene with clean single-frame animation.",
        "i2v_as": "Audio-sensitive motion without speech articulation.",
        "f_l": "A-to-B transition that requires two frames.",
        "f_l_as": "Audio-accented A-to-B transition that requires two frames.",
        "continuation": "Direct continuation of the previous shot.",
        "lip_sync": "Music-vocal rhythm shot with visible articulation support.",
    }
    return defaults.get(ltx_mode, "Production render mode selected by Scenario Director.")


def _is_music_vocal_mode(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return any(token in lowered for token in ("music", "vocal", "lyric", "sing", "chorus"))


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            texts = [str(part.get("text") or "") for part in parts if isinstance(part, dict) and str(part.get("text") or "").strip()]
            if texts:
                return "\n".join(texts).strip()
    return ""


def _extract_json_blob(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _extract_balanced_json_candidate(text: str) -> str | None:
    in_string = False
    escape = False
    depth = 0
    start_index: int | None = None
    for index, char in enumerate(text):
        if start_index is None:
            if char == "{":
                start_index = index
                depth = 1
            continue
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_index:index + 1]
    return None


def _clean_dirty_json_blob(raw_text: str) -> str:
    candidate = _extract_json_blob(raw_text)
    candidate = re.sub(r"^```(?:json)?\s*", "", candidate.strip(), flags=re.IGNORECASE)
    candidate = re.sub(r"\s*```$", "", candidate, flags=re.IGNORECASE)
    candidate = _extract_balanced_json_candidate(candidate) or candidate
    candidate = candidate.strip().strip("`")
    return candidate


def _try_parse_dirty_json(text: str) -> dict[str, Any] | None:
    candidate = _clean_dirty_json_blob(text)
    if not candidate.startswith("{"):
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    pythonish = re.sub(r"\btrue\b", "True", candidate, flags=re.IGNORECASE)
    pythonish = re.sub(r"\bfalse\b", "False", pythonish, flags=re.IGNORECASE)
    pythonish = re.sub(r"\bnull\b", "None", pythonish, flags=re.IGNORECASE)
    try:
        parsed = ast.literal_eval(pythonish)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    return _try_parse_dirty_json(raw_text)


def _normalize_legacy_scene_shape(scene: dict) -> dict:
    normalized = dict(scene or {})
    visual = normalized.pop("visual", None)
    audio = normalized.pop("audio", None)
    ltx = normalized.pop("ltx", None)

    applied = False
    if isinstance(visual, dict):
        normalized.setdefault("frame_description", visual.get("frame_description"))
        normalized.setdefault("action_in_frame", visual.get("action_in_frame"))
        normalized.setdefault("camera", visual.get("camera"))
        applied = True
    if isinstance(audio, dict):
        normalized.setdefault("narration_mode", audio.get("narration"))
        normalized.setdefault("local_phrase", audio.get("local_phrase"))
        normalized.setdefault("sfx", audio.get("sfx"))
        applied = True
    if isinstance(ltx, dict):
        normalized.setdefault("ltx_mode", ltx.get("mode"))
        normalized.setdefault("ltx_reason", ltx.get("reason"))
        normalized.setdefault("start_frame_source", ltx.get("start_frame_source"))
        normalized.setdefault("needs_two_frames", ltx.get("needs_two_frames"))
        applied = True

    normalized["start_frame_source"] = _normalize_start_frame_source(
        normalized.get("start_frame_source"),
        continuation=_coerce_bool(normalized.get("continuation_from_previous"), False),
    )
    normalized["needs_two_frames"] = _coerce_bool(normalized.get("needs_two_frames"), False)
    normalized["continuation_from_previous"] = _coerce_bool(
        normalized.get("continuation_from_previous") or normalized.get("start_frame_source") == "previous_frame",
        False,
    )
    if normalized["start_frame_source"] == "previous_frame":
        normalized["continuation_from_previous"] = True
    normalized["sfx"] = _stringify_sfx(normalized.get("sfx"))

    if applied:
        logger.debug(
            "[SCENARIO_DIRECTOR] legacy scene normalized scene_id=%s ltx_mode=%s start_frame=%s",
            str(normalized.get("scene_id") or "").strip() or "unknown",
            str(normalized.get("ltx_mode") or "").strip() or "auto",
            normalized.get("start_frame_source") or "new",
        )
    return normalized


def _normalize_scenario_director_scene_defaults(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    if not isinstance(payload, dict):
        return payload, [], []
    repaired = dict(payload)
    scenes = repaired.get("scenes")
    if not isinstance(scenes, list):
        return repaired, [], []
    normalized_fields: list[str] = []
    warnings: list[str] = []
    normalized_scenes: list[Any] = []
    for idx, raw_scene in enumerate(scenes):
        if not isinstance(raw_scene, dict):
            normalized_scenes.append(raw_scene)
            continue
        scene = dict(raw_scene)
        narration_mode = str(scene.get("narration_mode") or "").strip().lower()
        if narration_mode not in ALLOWED_NARRATION_MODES:
            scene["narration_mode"] = "full"
            normalized_fields.append(f"scenes[{idx}].narration_mode")
            warnings.append("scenario_director_normalized_narration_mode_default")
        normalized_scenes.append(scene)
    repaired["scenes"] = normalized_scenes
    return repaired, normalized_fields, list(dict.fromkeys(warnings))


def _repair_scenario_director_payload(payload: dict) -> dict:
    candidate = payload
    changed = False
    if isinstance(candidate.get("storyboard_out"), dict):
        candidate = candidate["storyboard_out"]
        changed = True
    elif isinstance(candidate.get("storyboardOut"), dict):
        candidate = candidate["storyboardOut"]
        changed = True
    elif isinstance(candidate.get("output"), dict) and isinstance(candidate["output"].get("scenes"), list):
        candidate = candidate["output"]
        changed = True

    repaired = dict(candidate)
    scenes = repaired.get("scenes")
    if isinstance(scenes, list):
        normalized_scenes = [_normalize_legacy_scene_shape(scene) if isinstance(scene, dict) else scene for scene in scenes]
        if normalized_scenes != scenes:
            changed = True
        repaired["scenes"] = normalized_scenes
        repaired, normalized_fields, _ = _normalize_scenario_director_scene_defaults(repaired)
        if normalized_fields:
            changed = True

    if not repaired.get("story_summary"):
        repaired["story_summary"] = repaired.get("summary") or repaired.get("storySummary") or ""
        changed = changed or bool(repaired["story_summary"])
    if not repaired.get("full_scenario"):
        repaired["full_scenario"] = repaired.get("scenario") or repaired.get("fullScenario") or repaired.get("story") or ""
        changed = changed or bool(repaired["full_scenario"])
    if not repaired.get("voice_script"):
        repaired["voice_script"] = repaired.get("voiceScript") or repaired.get("narration_script") or ""
        changed = changed or bool(repaired["voice_script"])
    if not repaired.get("music_prompt"):
        repaired["music_prompt"] = repaired.get("musicPrompt") or repaired.get("music_direction") or ""
        changed = changed or bool(repaired["music_prompt"])
    if not repaired.get("director_summary"):
        repaired["director_summary"] = repaired.get("directorSummary") or repaired.get("direction_summary") or ""
        changed = changed or bool(repaired["director_summary"])

    if changed:
        logger.debug(
            "[SCENARIO_DIRECTOR] repair applied scenes=%s story_summary=%s",
            len(repaired.get("scenes") or []),
            bool(str(repaired.get("story_summary") or "").strip()),
        )
    return repaired


def _build_reference_role_map(payload: dict[str, Any]) -> dict[str, str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    role_map: dict[str, str] = {}
    for role, item in refs.items():
        if not isinstance(item, dict):
            continue
        label = str(item.get("preview") or item.get("label") or item.get("source_label") or role).strip()
        role_map[str(role).strip()] = label or str(role).strip()
    return role_map


def _scene_participants(scene: ScenarioDirectorScene, role_labels: dict[str, str]) -> list[str]:
    participants: list[str] = []
    for actor in scene.actors:
        clean = str(actor or "").strip()
        if not clean:
            continue
        participants.append(role_labels.get(clean, clean))
    return participants


def _build_character_roles(payload: dict[str, Any], role_labels: dict[str, str]) -> list[dict[str, str]]:
    ordered_roles = ["character_1", "character_2", "character_3"]
    role_copy = {
        "character_1": "Главный герой / главный носитель действия",
        "character_2": "Партнёр по сцене / вторичный акцент",
        "character_3": "Поддерживающий персонаж или смысловой объект",
    }
    out: list[dict[str, str]] = []
    for role in ordered_roles:
        label = role_labels.get(role)
        if not label:
            continue
        out.append({"name": label, "role": role_copy.get(role, "Поддерживающая роль")})
    return out


def _build_director_output(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> dict[str, Any]:
    role_labels = _build_reference_role_map(payload)
    history = {
        "summary": storyboard_out.story_summary,
        "fullScenario": storyboard_out.full_scenario,
        "characterRoles": _build_character_roles(payload, role_labels),
        "toneStyleDirection": str(payload.get("director_controls", {}).get("styleProfile") or "").strip() or "Scenario Director tone guidance from Gemini.",
        "directorSummary": storyboard_out.director_summary,
    }
    scenes = []
    video = []
    sound = []
    for scene in storyboard_out.scenes:
        participants = _scene_participants(scene, role_labels)
        scene_item = {
            "sceneId": scene.scene_id,
            "title": scene.scene_id,
            "timeStart": scene.time_start,
            "timeEnd": scene.time_end,
            "duration": scene.duration,
            "participants": participants,
            "location": scene.location,
            "props": scene.props,
            "action": scene.action_in_frame,
            "emotion": scene.emotion,
            "sceneGoal": scene.scene_goal,
            "frameDescription": scene.frame_description,
            "actionInFrame": scene.action_in_frame,
            "cameraIdea": scene.camera,
            "imagePrompt": scene.image_prompt,
            "videoPrompt": scene.video_prompt,
            "ltxMode": scene.ltx_mode,
            "whyThisMode": scene.ltx_reason,
            "startFrameSource": scene.start_frame_source,
            "needsTwoFrames": scene.needs_two_frames,
            "continuation": scene.continuation_from_previous,
            "narrationMode": scene.narration_mode,
            "localPhrase": scene.local_phrase,
            "sfx": scene.sfx,
            "soundNotes": scene.sfx,
            "pauseDuckSilenceNotes": "",
            "musicMixHint": scene.music_mix_hint,
        }
        scenes.append(scene_item)
        video.append(
            {
                "sceneId": scene.scene_id,
                "frameDescription": scene.frame_description,
                "actionInFrame": scene.action_in_frame,
                "cameraIdea": scene.camera,
                "imagePrompt": scene.image_prompt,
                "videoPrompt": scene.video_prompt,
                "ltxMode": scene.ltx_mode,
                "whyThisMode": scene.ltx_reason,
                "startFrameSource": scene.start_frame_source,
                "needsTwoFrames": scene.needs_two_frames,
                "continuation": scene.continuation_from_previous,
            }
        )
        sound.append(
            {
                "sceneId": scene.scene_id,
                "narrationMode": scene.narration_mode,
                "localPhrase": scene.local_phrase,
                "sfx": scene.sfx,
                "soundNotes": scene.sfx,
                "pauseDuckSilenceNotes": "",
            }
        )
    music = {
        "globalMusicPrompt": storyboard_out.music_prompt,
        "mood": str(payload.get("director_controls", {}).get("styleProfile") or "").strip(),
        "style": f"{payload.get('director_controls', {}).get('contentType') or ''} / {payload.get('director_controls', {}).get('styleProfile') or ''}".strip(" /"),
        "pacingHints": "Use the Gemini scene pacing to build intro, escalation, climax, and resolution.",
    }
    return {
        "history": history,
        "scenes": scenes,
        "video": video,
        "sound": sound,
        "music": music,
    }


def _build_brain_package(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    role_labels = _build_reference_role_map(payload)
    entities = [label for _, label in sorted(role_labels.items())]
    return {
        "contentType": controls.get("contentType") or "story",
        "contentTypeLabel": controls.get("contentType") or "story",
        "styleProfile": controls.get("styleProfile") or "realistic",
        "styleLabel": controls.get("styleProfile") or "realistic",
        "sourceMode": str(source.get("source_mode") or "audio").upper(),
        "sourceOrigin": "connected",
        "sourceLabel": source.get("source_mode") or "audio",
        "sourcePreview": source.get("source_preview") or source.get("source_value") or "",
        "connectedContext": summary,
        "entities": entities,
        "sceneLogic": [scene.scene_goal or scene.frame_description or scene.action_in_frame for scene in storyboard_out.scenes],
        "audioStrategy": storyboard_out.voice_script or storyboard_out.music_prompt,
        "directorNote": controls.get("directorNote") or "",
    }


def _apply_scene_count_limit(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if len(storyboard_out.scenes) > 20:
        storyboard_out.scenes = storyboard_out.scenes[:20]
    return storyboard_out


def _scene_text_bundle(scene: ScenarioDirectorScene) -> str:
    return " | ".join(
        part
        for part in [
            scene.scene_goal,
            scene.frame_description,
            scene.action_in_frame,
            scene.camera,
            scene.image_prompt,
            scene.video_prompt,
            scene.ltx_reason,
        ]
        if str(part or "").strip()
    ).strip()


def _count_scene_signal_hits(bundle: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in bundle)


def _scene_specificity_score(scene: ScenarioDirectorScene) -> int:
    score = 0
    bundle = _scene_text_bundle(scene).lower()
    object_markers = (
        "alarm key", "hatch", "beam", "door", "window", "mirror", "monitor", "console", "switch", "helmet",
        "hand", "face", "dust", "light", "shadow", "blood", "machine", "siren", "corridor", "stair",
    )
    action_markers = (
        "freezes", "opens", "shuts", "turns", "presses", "reaches", "stares", "flinches", "steps", "grips",
        "pulls", "reveals", "cuts out", "pulses", "cracks", "ignites", "crosses", "locks", "unlocks",
    )
    camera_markers = (
        "close-up", "locked frontal", "wide", "overhead", "profile", "insert", "tracking", "dolly", "push-in",
        "static", "macro", "two-shot", "over-the-shoulder", "silhouette",
    )
    sensory_markers = (
        "red", "blue", "neon", "steam", "dust", "beam", "glow", "hum", "siren", "pulse", "echo", "flicker",
    )
    if len(bundle) >= 70:
        score += 1
    if len(scene.frame_description.split()) >= 4:
        score += 1
    if len(scene.action_in_frame.split()) >= 2:
        score += 1
    if len(scene.camera.split()) >= 2:
        score += 1
    if scene.location or scene.props:
        score += 1
    if _count_scene_signal_hits(bundle, object_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, action_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, camera_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, sensory_markers) >= 1:
        score += 1
    return score


def _scene_weak_assessment(scene: ScenarioDirectorScene) -> tuple[bool, str]:
    bundle = _scene_text_bundle(scene).lower()
    specificity = _scene_specificity_score(scene)
    directing_fields = sum(
        1
        for field in (scene.frame_description, scene.action_in_frame, scene.camera)
        if str(field or "").strip()
    )
    if any(pattern in bundle for pattern in WEAK_SCENE_PATTERNS):
        return True, "generic"
    if directing_fields == 0:
        return True, "missing_directing"
    if specificity >= 4:
        return False, "short_but_specific" if len(bundle) < 80 else "specific"
    if len(bundle) < 28 and specificity < 2:
        return True, "too_thin"
    if directing_fields < 2 and specificity < 3:
        return True, "missing_directing"
    if len(bundle) < 55 and specificity < 3:
        return True, "vague"
    if specificity < 2:
        return True, "vague"
    return False, "specific"


def _is_scene_weak(scene: ScenarioDirectorScene) -> bool:
    weak, _ = _scene_weak_assessment(scene)
    return weak


def _infer_scene_purpose(scene: ScenarioDirectorScene) -> str:
    bundle = _scene_text_bundle(scene).lower()
    if scene.continuation_from_previous:
        return "transition"
    if any(token in bundle for token in ("final", "last image", "aftertaste", "hold", "lingers", "lingering")):
        return "final image / ending hold"
    if any(token in bundle for token in ("climax", "breaks", "collapse", "scream", "impact", "erupts", "peak")):
        return "emotional climax"
    if any(token in bundle for token in ("reveal", "door opens", "discovers", "unveils", "transformation", "new space")):
        return "reveal"
    if any(token in bundle for token in ("confronts", "faces", "standoff", "conflict")):
        return "confrontation"
    if any(token in bundle for token in ("intrudes", "wrong", "glitch", "alarm", "destabil")):
        return "destabilization"
    if any(token in bundle for token in ("arrives", "enters", "descends", "steps into")):
        return "entry"
    if any(token in bundle for token in ("wide tableau", "iconic image", "silhouette", "burning frame")):
        return "peak image"
    if any(token in bundle for token in ("escalates", "rushes", "tightens", "rises", "closing in")):
        return "escalation"
    return "hook" if scene.time_start <= 0.1 else "transition"


def _repair_missing_scene_goal(scene: ScenarioDirectorScene) -> ScenarioDirectorScene:
    goal = str(scene.scene_goal or "").strip().lower()
    if goal and goal not in GENERIC_SCENE_GOALS:
        return scene
    scene.scene_goal = _infer_scene_purpose(scene)
    return scene


def _filter_or_repair_weak_scenes(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not storyboard_out.scenes:
        return storyboard_out
    kept: list[ScenarioDirectorScene] = []
    weak_count = 0
    allow_repair_only = len(storyboard_out.scenes) <= 3
    for scene in storyboard_out.scenes:
        scene = _repair_missing_scene_goal(scene)
        weak, reason = _scene_weak_assessment(scene)
        if not weak:
            if reason == "short_but_specific":
                logger.debug("[SCENARIO_DIRECTOR] weak scene kept short_but_specific scene_id=%s", scene.scene_id)
            kept.append(scene)
            continue
        weak_count += 1
        logger.debug("[SCENARIO_DIRECTOR] weak scene detected scene_id=%s reason=%s", scene.scene_id, reason)
        if allow_repair_only or len(storyboard_out.scenes) - weak_count < 1:
            if not scene.scene_goal or scene.scene_goal.lower() in GENERIC_SCENE_GOALS:
                scene.scene_goal = _infer_scene_purpose(scene)
            kept.append(scene)
            continue
        if _scene_specificity_score(scene) >= 4:
            kept.append(scene)
    if not kept:
        raise ScenarioDirectorError(
            "scenario_director_empty_after_filter",
            "Scenario Director filtered out all scenes after quality checks.",
            status_code=502,
        )
    if weak_count:
        logger.debug("[SCENARIO_DIRECTOR] weak scenes filtered count=%s", max(0, len(storyboard_out.scenes) - len(kept)))
    storyboard_out.scenes = kept
    return storyboard_out


def _normalize_scene_timeline(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    previous_end = 0.0
    for scene in storyboard_out.scenes:
        original_end = _safe_float(scene.time_end, scene.time_start)
        original_duration = _safe_float(scene.duration, max(0.0, original_end - scene.time_start))
        scene.time_start = max(_safe_float(scene.time_start, previous_end), previous_end)
        if original_end < scene.time_start:
            original_end = round(scene.time_start + max(0.0, original_duration), 3)
        scene.time_end = max(original_end, scene.time_start)
        scene.duration = round(max(0.0, scene.time_end - scene.time_start), 3)
        previous_end = scene.time_end
    return storyboard_out


def _limit_lip_sync_usage(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    lip_sync_seen = 0
    for scene in storyboard_out.scenes:
        if scene.ltx_mode != "lip_sync":
            continue
        lip_sync_seen += 1
        if lip_sync_seen <= 3:
            continue
        scene.ltx_mode = "i2v_as"
        original_reason = str(scene.ltx_reason or "").strip()
        replacement_reason = "Normalized from lip_sync to i2v_as because Scenario Director allows at most 3 lip_sync scenes per output."
        scene.ltx_reason = f"{original_reason}; {replacement_reason}" if original_reason else replacement_reason
    return storyboard_out


def _detect_expected_character_roles(payload: dict[str, Any]) -> list[str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    expected: list[str] = []
    for role in ("character_1", "character_2", "character_3"):
        item = refs.get(role)
        if not isinstance(item, dict):
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        connected = _coerce_bool(meta.get("connected"), False)
        ref_count = len(item.get("refs") or [])
        count = int(item.get("count") or 0)
        if connected or ref_count > 0 or count > 0:
            expected.append(role)
    if len(expected) >= 2:
        return expected[:2]
    implied = connected_summary.get("entities") if isinstance(connected_summary.get("entities"), list) else []
    for role in ("character_1", "character_2"):
        if role in expected:
            continue
        if any(role in str(entity or "") for entity in implied):
            expected.append(role)
    return expected[:2]


def _character_lock_candidate_score(scene: ScenarioDirectorScene, *, scene_index: int, total_scenes: int, companion_roles: set[str]) -> int:
    bundle = _scene_text_bundle(scene).lower()
    purpose = _infer_scene_purpose(scene)
    score = 0
    if purpose in {"reveal", "confrontation", "escalation", "destabilization"}:
        score += 3
    if any(token in bundle for token in ("reveal", "confront", "exchange", "reaction", "watches", "faces", "shared", "between", "answer", "responds", "alarm", "tension")):
        score += 2
    if any(actor in companion_roles for actor in scene.actors):
        score += 2
    if any(token in bundle for token in ("close-up", "isolated", "alone", "empty hallway", "environment only", "abstract", "held image", "lingers on")):
        score -= 3
    if scene_index == 0 and purpose == "hook":
        score -= 2
    if scene_index == total_scenes - 1 and purpose == "final image / ending hold":
        score -= 2
    if len(scene.actors) <= 1 and any(token in bundle for token in ("close-up", "macro", "portrait", "hand", "face")):
        score -= 2
    return score


def _enforce_character_lock(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    expected_roles = _detect_expected_character_roles(payload)
    if len(expected_roles) < 2 or not storyboard_out.scenes:
        return storyboard_out
    role_set = set(expected_roles)
    present_roles = {actor for scene in storyboard_out.scenes for actor in scene.actors if actor in role_set}
    missing_roles = [role for role in expected_roles if role not in present_roles]
    if not missing_roles:
        return storyboard_out
    total_scenes = len(storyboard_out.scenes)
    for role in missing_roles:
        companion_roles = role_set - {role}
        candidates: list[tuple[int, int]] = []
        for index, scene in enumerate(storyboard_out.scenes):
            score = _character_lock_candidate_score(scene, scene_index=index, total_scenes=total_scenes, companion_roles=companion_roles)
            if score >= 3:
                candidates.append((score, index))
        if not candidates:
            logger.debug("[SCENARIO_DIRECTOR] character lock skipped no_candidate role=%s", role)
            continue
        candidates.sort(key=lambda item: (-item[0], item[1]))
        repaired_indexes: list[str] = []
        max_repairs = 1 if total_scenes <= 4 else 2
        for _, index in candidates[:max_repairs]:
            scene = storyboard_out.scenes[index]
            if role not in scene.actors:
                scene.actors.append(role)
            if not scene.scene_goal or scene.scene_goal.lower() in GENERIC_SCENE_GOALS:
                purpose = _infer_scene_purpose(scene)
                if purpose in {"reveal", "confrontation", "escalation", "destabilization"}:
                    scene.scene_goal = purpose
                elif any(actor in companion_roles for actor in scene.actors):
                    scene.scene_goal = "interaction"
            repaired_indexes.append(scene.scene_id)
        if repaired_indexes:
            logger.debug("[SCENARIO_DIRECTOR] character lock repaired role=%s scenes=%s", role, ",".join(repaired_indexes))
        else:
            logger.debug("[SCENARIO_DIRECTOR] character lock skipped no_candidate role=%s", role)
    return storyboard_out


def _has_overly_uniform_timing(storyboard_out: ScenarioDirectorStoryboardOut) -> bool:
    if len(storyboard_out.scenes) < 3:
        return False
    durations = [max(0.1, _safe_float(scene.duration, scene.time_end - scene.time_start)) for scene in storyboard_out.scenes]
    spread = max(durations) - min(durations)
    average = sum(durations) / len(durations)
    max_deviation = max(abs(value - average) for value in durations)
    repeated_fives = sum(1 for value in durations if abs(value - 5.0) <= 0.15)
    half_second_buckets = {round(value * 2) / 2 for value in durations}
    return (spread <= 0.45 and max_deviation <= 0.3) or (len(half_second_buckets) <= 2 and spread <= 0.6) or (repeated_fives >= max(3, len(durations) - 1) and spread <= 0.8)


def _apply_timing_variation(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not _has_overly_uniform_timing(storyboard_out):
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    scene_count = len(storyboard_out.scenes)
    if scene_count < 2:
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    durations = [max(1.2, _safe_float(scene.duration, scene.time_end - scene.time_start)) for scene in storyboard_out.scenes]
    deltas = [0.0] * scene_count
    purposes = [_infer_scene_purpose(scene) for scene in storyboard_out.scenes]
    if durations[0] >= 2.2 and purposes[0] not in {"emotional climax", "final image / ending hold"}:
        deltas[0] -= min(0.35, durations[0] - 1.8)
    if purposes[-1] in {"reveal", "emotional climax", "peak image", "final image / ending hold"} or durations[-1] <= max(durations[:-1]):
        deltas[-1] += 0.35
    if scene_count >= 4 and purposes[-2] in {"reveal", "emotional climax", "confrontation", "escalation"}:
        deltas[-2] += 0.2
    positive_total = round(sum(value for value in deltas if value > 0), 3)
    negative_total = round(-sum(value for value in deltas if value < 0), 3)
    remaining = round(positive_total - negative_total, 3)
    compensation_indexes = [
        index
        for index in range(1, max(1, scene_count - 1))
        if durations[index] > 1.8 and purposes[index] not in {"emotional climax", "final image / ending hold"}
    ]
    if remaining > 0 and compensation_indexes:
        per_scene = min(0.2, round(remaining / len(compensation_indexes), 3))
        for index in compensation_indexes:
            available = max(0.0, durations[index] - 1.4)
            reduction = min(per_scene, available)
            deltas[index] -= reduction
            remaining = round(remaining - reduction, 3)
            if remaining <= 0.02:
                break
    elif remaining < 0 and compensation_indexes:
        deltas[compensation_indexes[len(compensation_indexes) // 2]] += abs(remaining)
    adjusted = [round(max(1.2, duration + delta), 3) for duration, delta in zip(durations, deltas)]
    total_diff = round(sum(durations) - sum(adjusted), 3)
    if abs(total_diff) > 0.01:
        rebalance_index = compensation_indexes[0] if compensation_indexes else min(max(1, scene_count // 2), scene_count - 1)
        adjusted[rebalance_index] = round(max(1.2, adjusted[rebalance_index] + total_diff), 3)
    if max(abs(adjusted[index] - durations[index]) for index in range(scene_count)) < 0.15:
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    cursor = 0.0
    for scene, duration in zip(storyboard_out.scenes, adjusted):
        scene.time_start = round(cursor, 3)
        scene.duration = duration
        scene.time_end = round(scene.time_start + scene.duration, 3)
        cursor = scene.time_end
    delta_total = round(sum(abs(adjusted[index] - durations[index]) for index in range(scene_count)), 3)
    logger.debug("[SCENARIO_DIRECTOR] timing variation applied delta_total=%s", delta_total)
    return storyboard_out


def _scene_has_transition_evidence(scene: ScenarioDirectorScene) -> bool:
    bundle = _scene_text_bundle(scene).lower()
    return any(
        token in bundle
        for token in (
            "door opens", "opens one inch", "entering a new", "new chamber", "new room", "crosses the threshold",
            "reveal", "reveals", "unveils", "transformation", "before", "after", "state shift", "activation",
            "activates", "ignites", "powers on", "hatch opens", "crosses into", "discovers",
        )
    )


def _scene_has_audio_reactive_evidence(scene: ScenarioDirectorScene) -> bool:
    bundle = _scene_text_bundle(scene).lower()
    return any(
        token in bundle
        for token in (
            "pulses", "pulse", "breathing machinery", "machinery", "hum", "audio-reactive", "lights flicker",
            "rhythmic", "throb", "alarm", "siren", "vibration", "subtle response",
        )
    )


def _recover_ltx_mode(scene: ScenarioDirectorScene) -> str:
    if _scene_has_transition_evidence(scene):
        return "f_l_as" if _scene_has_audio_reactive_evidence(scene) else "f_l"
    if _scene_has_audio_reactive_evidence(scene):
        return "i2v_as"
    return "i2v"


def _rebalance_ltx_modes(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not storyboard_out.scenes:
        return storyboard_out
    continuation_total = sum(
        1 for scene in storyboard_out.scenes if scene.ltx_mode == "continuation" or scene.continuation_from_previous or scene.start_frame_source == "previous_frame"
    )
    continuation_limit = max(2, len(storyboard_out.scenes) // 2)
    continuation_seen = 0
    for index, scene in enumerate(storyboard_out.scenes):
        original_mode = scene.ltx_mode
        target_mode = original_mode
        correction_reason = ""
        if original_mode == "lip_sync" and not _is_music_vocal_mode(scene.narration_mode):
            target_mode = "i2v_as"
            correction_reason = "visible articulation is unsupported by narration mode"
        elif original_mode == "continuation":
            continuation_seen += 1
            if index == 0:
                target_mode = _recover_ltx_mode(scene)
                correction_reason = "continuation cannot be the first scene"
            elif continuation_total > continuation_limit and continuation_seen > continuation_limit:
                target_mode = _recover_ltx_mode(scene)
                correction_reason = "continuation was overused"
        elif index == 0 and scene.start_frame_source == "previous_frame":
            target_mode = _recover_ltx_mode(scene)
            correction_reason = "first scene cannot inherit a previous frame"
        elif original_mode in {"f_l", "f_l_as"} and not scene.needs_two_frames and not _scene_has_transition_evidence(scene):
            target_mode = "i2v_as" if original_mode == "f_l_as" and _scene_has_audio_reactive_evidence(scene) else "i2v"
            correction_reason = "two-frame transition evidence is missing"
        elif original_mode == "i2v_as" and not _scene_has_audio_reactive_evidence(scene) and _scene_has_transition_evidence(scene) and scene.needs_two_frames:
            target_mode = "f_l_as"
            correction_reason = "transition evidence is stronger than ambient pulse"
        if target_mode != original_mode:
            scene.ltx_mode = target_mode
            scene.ltx_reason = _normalize_ltx_reason(f"Normalized to {target_mode}: {correction_reason}.", target_mode, narration_mode=scene.narration_mode)
            logger.debug("[SCENARIO_DIRECTOR] ltx normalize corrected scene_id=%s from=%s to=%s", scene.scene_id, original_mode, target_mode)
        else:
            scene.ltx_reason = _normalize_ltx_reason(scene.ltx_reason, scene.ltx_mode, narration_mode=scene.narration_mode)
            logger.debug("[SCENARIO_DIRECTOR] ltx normalize kept original scene_id=%s mode=%s", scene.scene_id, scene.ltx_mode)
        scene.needs_two_frames = scene.ltx_mode in {"f_l", "f_l_as"}
        if scene.ltx_mode == "continuation" and index > 0:
            scene.continuation_from_previous = True
            scene.start_frame_source = "previous_frame"
        elif scene.ltx_mode != "continuation":
            scene.continuation_from_previous = False
            if scene.start_frame_source == "previous_frame":
                scene.start_frame_source = "new"
    return storyboard_out


def _assert_storyboard_quality(storyboard_out: ScenarioDirectorStoryboardOut) -> None:
    scenes = storyboard_out.scenes or []
    if not scenes:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_empty_after_filter", "Scenario Director returned no usable scenes.", status_code=502)
    frame_count = sum(1 for scene in scenes if str(scene.frame_description or "").strip())
    action_count = sum(1 for scene in scenes if str(scene.action_in_frame or "").strip())
    camera_count = sum(1 for scene in scenes if str(scene.camera or "").strip())
    if frame_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without frame_description.", status_code=502)
    if action_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without action_in_frame.", status_code=502)
    if camera_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without camera direction.", status_code=502)
    strong_scene_count = 0
    generic_scene_count = 0
    directing_scene_count = 0
    for scene in scenes:
        weak, reason = _scene_weak_assessment(scene)
        directing_fields = sum(1 for field in (scene.frame_description, scene.action_in_frame, scene.camera) if str(field or "").strip())
        if directing_fields >= 2:
            directing_scene_count += 1
        if not weak or _scene_specificity_score(scene) >= 4:
            strong_scene_count += 1
        if reason == "generic":
            generic_scene_count += 1
    if directing_scene_count == 0 or strong_scene_count == 0 or generic_scene_count == len(scenes):
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned only weak filler scenes.", status_code=502)


def _resolve_audio_duration_sec(payload: dict[str, Any]) -> float:
    candidates = [
        payload.get("audioDurationSec"),
        (payload.get("metadata") or {}).get("audioDurationSec") if isinstance(payload.get("metadata"), dict) else None,
        (payload.get("source") or {}).get("audioDurationSec") if isinstance(payload.get("source"), dict) else None,
        ((payload.get("source") or {}).get("metadata") or {}).get("audioDurationSec")
        if isinstance(payload.get("source"), dict) and isinstance((payload.get("source") or {}).get("metadata"), dict)
        else None,
    ]
    for value in candidates:
        duration = _safe_float(value, 0.0)
        if duration > 0:
            return duration
    return 0.0


def _validate_audio_timeline_coverage(scenes: list[ScenarioDirectorScene], audio_duration_sec: float) -> dict[str, Any]:
    if audio_duration_sec <= 0:
        return {
            "audioDurationSec": 0.0,
            "timelineStartSec": 0.0,
            "timelineEndSec": 0.0,
            "timelineCoverageSec": 0.0,
            "timelineCoverageRatio": 0.0,
            "uncoveredTailSec": 0.0,
            "internalGapCount": 0,
            "timelineCoverageStatus": "ok",
            "warnings": [],
        }
    if not scenes:
        return {
            "audioDurationSec": audio_duration_sec,
            "timelineStartSec": 0.0,
            "timelineEndSec": 0.0,
            "timelineCoverageSec": 0.0,
            "timelineCoverageRatio": 0.0,
            "uncoveredTailSec": audio_duration_sec,
            "internalGapCount": 0,
            "timelineCoverageStatus": "invalid",
            "warnings": ["timeline_coverage_too_short", "timeline_does_not_reach_audio_end", "timeline_has_uncovered_audio_tail"],
        }
    sorted_scenes = sorted(scenes, key=lambda scene: (scene.time_start, scene.time_end))
    first_start = _safe_float(sorted_scenes[0].time_start, 0.0)
    last_end = _safe_float(sorted_scenes[-1].time_end, first_start)
    total_coverage = 0.0
    gap_count = 0
    warnings: list[str] = []
    cursor = 0.0
    for scene in sorted_scenes:
        start = _safe_float(scene.time_start, cursor)
        end = max(start, _safe_float(scene.time_end, start))
        gap = round(max(0.0, start - cursor), 3)
        if gap >= TIMELINE_INTERNAL_GAP_WARN_SEC:
            gap_count += 1
            warnings.append("timeline_has_large_internal_gap")
        total_coverage += max(0.0, end - start)
        cursor = max(cursor, end)
    uncovered_tail = round(max(0.0, audio_duration_sec - last_end), 3)
    coverage_ratio = round((total_coverage / audio_duration_sec) if audio_duration_sec > 0 else 0.0, 4)
    if first_start > TIMELINE_START_TOLERANCE_SEC:
        warnings.append("timeline_does_not_start_at_zero")
    if audio_duration_sec - last_end > TIMELINE_END_TOLERANCE_SEC:
        warnings.append("timeline_does_not_reach_audio_end")
    if uncovered_tail > TIMELINE_TAIL_WARN_SEC:
        warnings.append("timeline_has_uncovered_audio_tail")
    if coverage_ratio < TIMELINE_COVERAGE_RATIO_WARN:
        warnings.append("timeline_coverage_too_short")
    status = "ok"
    unique_warnings = list(dict.fromkeys(warnings))
    if any(code in unique_warnings for code in ("timeline_does_not_reach_audio_end", "timeline_has_uncovered_audio_tail", "timeline_coverage_too_short")):
        status = "invalid"
    elif unique_warnings:
        status = "warning"
    return {
        "audioDurationSec": round(audio_duration_sec, 3),
        "timelineStartSec": round(first_start, 3),
        "timelineEndSec": round(last_end, 3),
        "timelineCoverageSec": round(total_coverage, 3),
        "timelineCoverageRatio": coverage_ratio,
        "uncoveredTailSec": uncovered_tail,
        "internalGapCount": gap_count,
        "timelineCoverageStatus": status,
        "warnings": unique_warnings,
    }


def _harden_storyboard_out(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> ScenarioDirectorStoryboardOut:
    storyboard_out = _apply_scene_count_limit(storyboard_out)
    storyboard_out = _filter_or_repair_weak_scenes(storyboard_out)
    storyboard_out = _enforce_character_lock(payload, storyboard_out)
    storyboard_out = _apply_timing_variation(storyboard_out)
    storyboard_out = _rebalance_ltx_modes(storyboard_out)
    storyboard_out = _normalize_scene_timeline(storyboard_out)
    storyboard_out = _limit_lip_sync_usage(storyboard_out)
    _assert_storyboard_quality(storyboard_out)
    return storyboard_out


def _build_request_text(payload: dict[str, Any], *, strict_json_retry: bool = False) -> str:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    director_controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    connected_context_summary = payload.get("connected_context_summary", {})
    metadata = payload.get("metadata", {})
    audio_duration_sec = _resolve_audio_duration_sec(payload)
    request_text = (
        "You are Scenario Director for PhotoStudio COMFY.\n"
        "Gemini is the planning brain. Do not delegate planning to heuristics.\n"
        "Return a single JSON object only. No markdown, no commentary.\n"
        "The storyboard_out must be production-usable for downstream Storyboard execution.\n"
        "SOURCE PRIORITY (strict):\n"
        "1) user source-of-truth / story brief / scenario note\n"
        "2) connected visual references\n"
        "3) director notes\n"
        "4) project style profile\n"
        "5) only then free dramatization\n"
        "ANTI-DRIFT LOCKS:\n"
        "- Preserve the exact count of core characters implied by the source and refs.\n"
        "- If two connected refs imply two women, keep two women unless the user explicitly changes that.\n"
        "- Do not collapse connected characters into generic operative/target/action archetypes.\n"
        "- Preserve relationship tension, emotional roles, gender presentation, and visual identity anchors from the refs.\n"
        "- Preserve implied genre: horror, claustrophobic tension, industrial dread, surreal unease, emotional darkness, intimacy, mystery.\n"
        "- Do not flatten unique tone into generic espionage thriller or safe corporate cinematic filler.\n"
        "- Preserve the environment identity from the source or refs: bunker, abyss, industrial shaft, abandoned corridor, concrete hall, strange facility, ritual room, flooded station, etc.\n"
        "SHORT-FORM DIRECTING RULES:\n"
        "- Think like a premium short-film director + trailer editor + music-video storyboard artist.\n"
        "- The first 1-3 seconds must hook immediately with a specific image, not vague mood text.\n"
        "- Every scene must contain a specific image idea, a physical action, camera intent, and dramatic purpose.\n"
        "- Every scene must either reveal, intensify, transform, or leave a memorable afterimage.\n"
        "- Prefer fewer strong scenes over many weak scenes.\n"
        "- Build escalation: hook -> entry/destabilization -> reveal/complication -> escalation -> peak image/emotional climax -> final image that stays in memory.\n"
        "- Avoid safe filler and generic thriller language; convert vague mood text into concrete story-specific visuals.\n"
        "- Preserve horror, intimacy, dread, surreal industrial tension, or other source-implied genre DNA.\n"
        "- Use the environment as an active dramatic force, not passive background.\n"
        "- Do not flatten unique story DNA into generic content.\n"
        "- If connected refs imply two key characters, build interplay, contrast, and relationship energy across the scenario.\n"
        "TIMING RULES:\n"
        "- Do not force evenly sliced 5-second blocks.\n"
        "- Typical useful scene duration is about 4-9 seconds, but hook, reveal, climax, and final hold may be shorter or longer when justified by the drama.\n"
        "- Let timing breathe and follow emotional rhythm.\n"
        "- For longer videos, vary rhythm like short / medium / medium / short / long / climax / final hold.\n"
        "LTX MODE RULES:\n"
        "- i2v: strong single-image motion.\n"
        "- i2v_as: audio-sensitive motion, environmental pulsing, breathing tension, subtle rhythm response, but no literal speech articulation.\n"
        "- f_l: controlled A-to-B reveal, door opening, object transformation, pose shift, environmental change, or two-state transition.\n"
        "- f_l_as: transition or reveal that also needs audio-driven hit timing.\n"
        "- continuation: preserve continuity from the previous scene's visual endpoint when that is the strongest choice. Do not overuse it.\n"
        "- lip_sync: only if visible vocal articulation is truly required and the narration/audio mode supports it.\n"
        "- Every scene must include a short concrete ltx_reason that explains the production intent.\n"
        "Hard constraints:\n"
        "- Use only real LTX modes: i2v, i2v_as, f_l, f_l_as, continuation, lip_sync.\n"
        "- Never use fake modes like intro_lock, hero_peak, motion_follow, ending_hold.\n"
        "- lip_sync is allowed only for music-driven vocal rhythm with visible articulation support.\n"
        "- Do not use lip_sync for ordinary narration or generic voice-over.\n"
        "- Scenario Director is the main planning node. Storyboard executes your storyboard_out and should not rethink the plan.\n"
        "- Build scenes from story meaning, source-of-truth, connected refs, and director controls.\n"
        "- Keep timing coherent and use floats in seconds.\n"
        '- If "audioDurationSec" is present and > 0, scene timeline MUST span the full audio duration from 0.0 to audioDurationSec.\n'
        "- If audioDurationSec > 0: first scene starts at 0.0, final scene reaches near audioDurationSec, and every major audio interval belongs to some scene.\n"
        "- No large uncovered audio tail at the end. No large silent timeline gap unless explicitly intended as a scene beat.\n"
        "- If story climax happens early but audio continues, add natural late-stage scenes (aftermath, reaction, realization, escape continuation, tension tail, unresolved closing image, outro suspense).\n"
        "- Around 60 seconds, 6 scenes can be acceptable only if they truly cover full audio; add scenes when timing is too compressed.\n"
        "- Every scene must include concise but useful video/audio planning fields.\n"
        "- Keep the backend-compatible flat scene fields only. Do not output nested visual/audio/ltx blocks in final JSON.\n"
        "CONTRACT HARD RULE FOR narration_mode:\n"
        '- Every scene MUST include "narration_mode" explicitly.\n'
        '- narration_mode MUST always be a string and MUST NEVER be null.\n'
        '- Allowed values: "full", "duck", "pause".\n'
        '- If unsure, use "full".\n'
        '- Never output null for narration_mode and never omit narration_mode.\n'
        "Output contract:\n"
        "{\n"
        '  "story_summary": "",\n'
        '  "full_scenario": "",\n'
        '  "voice_script": "",\n'
        '  "music_prompt": "",\n'
        '  "director_summary": "",\n'
        '  "scenes": [\n'
        "    {\n"
        '      "scene_id": "S1",\n'
        '      "time_start": 0.0,\n'
        '      "time_end": 6.0,\n'
        '      "duration": 6.0,\n'
        '      "actors": ["character_1"],\n'
        '      "location": "",\n'
        '      "props": [],\n'
        '      "emotion": "",\n'
        '      "scene_goal": "",\n'
        '      "frame_description": "",\n'
        '      "action_in_frame": "",\n'
        '      "camera": "",\n'
        '      "image_prompt": "",\n'
        '      "video_prompt": "",\n'
        '      "ltx_mode": "i2v",\n'
        '      "ltx_reason": "",\n'
        '      "start_frame_source": "new",\n'
        '      "needs_two_frames": false,\n'
        '      "continuation_from_previous": false,\n'
        '      "narration_mode": "full",\n'
        '      "local_phrase": null,\n'
        '      "sfx": "",\n'
        '      "music_mix_hint": "off"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"Runtime payload:\n{json.dumps({'source': source, 'context_refs': context_refs, 'director_controls': director_controls, 'connected_context_summary': connected_context_summary, 'metadata': metadata, 'audioDurationSec': audio_duration_sec if audio_duration_sec > 0 else None}, ensure_ascii=False, indent=2)}"
    )
    if strict_json_retry:
        request_text += JSON_ONLY_RETRY_SUFFIX
    return request_text


def _build_audio_coverage_refinement_prompt(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut, coverage: dict[str, Any]) -> str:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    director_controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    compact_scenes: list[dict[str, Any]] = []
    for scene in storyboard_out.scenes:
        compact_scenes.append(
            {
                "scene_id": scene.scene_id,
                "time_start": scene.time_start,
                "time_end": scene.time_end,
                "duration": scene.duration,
                "scene_goal": scene.scene_goal,
                "frame_description": scene.frame_description,
                "action_in_frame": scene.action_in_frame,
                "camera": scene.camera,
            }
        )
    return (
        "Timeline repair pass (not a rewrite). Return strict JSON object only with the same contract keys.\n"
        "Preserve current story direction and keep strong existing scenes.\n"
        "Repair timeline to fully cover audio. Extend or add scenes only where needed.\n"
        "Keep progression natural and cinematic.\n"
        "Final scene must reach audioDurationSec.\n"
        "If audioDurationSec is > 0: first scene must start at 0.0, no large uncovered tail, and no large internal uncovered gap.\n"
        "Prefer preserving story quality while fixing coverage boundaries.\n"
        f"Coverage diagnostics: {json.dumps(coverage, ensure_ascii=False)}\n"
        f"Runtime payload: {json.dumps({'source': source, 'context_refs': context_refs, 'director_controls': director_controls, 'metadata': metadata, 'audioDurationSec': coverage.get('audioDurationSec')}, ensure_ascii=False)}\n"
        f"Current storyboard snapshot: {json.dumps({'story_summary': storyboard_out.story_summary, 'full_scenario': storyboard_out.full_scenario, 'voice_script': storyboard_out.voice_script, 'music_prompt': storyboard_out.music_prompt, 'director_summary': storyboard_out.director_summary, 'scenes': compact_scenes}, ensure_ascii=False)}"
    )


def _send_director_request(api_key: str, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[str]]:
    attempted_models: list[str] = []
    response: dict[str, Any] | None = None
    model_used = DEFAULT_TEXT_MODEL
    for candidate_model in [DEFAULT_TEXT_MODEL, FALLBACK_TEXT_MODEL]:
        if candidate_model in attempted_models:
            continue
        attempted_models.append(candidate_model)
        response = post_generate_content(api_key, candidate_model, body, timeout=120)
        model_used = candidate_model
        if not isinstance(response, dict) or not response.get("__http_error__"):
            break
    return response, model_used, attempted_models


def _parse_storyboard_payload(raw_text: str) -> dict[str, Any]:
    logger.debug("[SCENARIO_DIRECTOR] raw response received chars=%s", len(str(raw_text or "")))
    extracted = _extract_json_object(raw_text)
    if extracted is None:
        raise ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for Scenario Director: could not extract JSON object.",
            status_code=502,
            details={"rawPreview": str(raw_text or "")[:1000]},
        )
    logger.debug("[SCENARIO_DIRECTOR] json extracted keys=%s", ",".join(list(extracted.keys())[:8]))
    repaired = _repair_scenario_director_payload(extracted)
    return repaired


def run_scenario_director(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ScenarioDirectorError(
            "gemini_api_key_missing",
            "GEMINI_API_KEY is missing for Scenario Director generation.",
            status_code=503,
        )

    request_text = _build_request_text(payload)
    body = {
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "You are the production Scenario Director for PhotoStudio COMFY. Return strict JSON only. "
                        "Hard contract: narration_mode must always be a non-null string in every scene (full|duck|pause, default full). "
                        "If audioDurationSec > 0, scene timeline MUST span full audio from 0.0 to audioDurationSec."
                    ),
                }
            ]
        },
        "contents": [{"role": "user", "parts": [{"text": request_text}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
    }

    response, model_used, attempted_models = _send_director_request(api_key, body)

    if not isinstance(response, dict):
        raise ScenarioDirectorError("gemini_request_failed", "Gemini did not return a JSON object.", status_code=502)
    if response.get("__http_error__"):
        status_code = int(response.get("status") or 502)
        raise ScenarioDirectorError(
            "gemini_request_failed",
            f"Gemini request failed with HTTP {status_code}: {str(response.get('text') or '')[:400]}",
            status_code=502,
            details={"httpStatus": status_code},
        )

    raw_text = _extract_gemini_text(response)
    retried_for_json = False
    try:
        parsed_payload = _parse_storyboard_payload(raw_text)
    except ScenarioDirectorError as first_exc:
        retried_for_json = True
        retry_body = {
            **body,
            "contents": [{"role": "user", "parts": [{"text": _build_request_text(payload, strict_json_retry=True)}]}],
        }
        retry_response, retry_model_used, retry_attempts = _send_director_request(api_key, retry_body)
        attempted_models.extend(model for model in retry_attempts if model not in attempted_models)
        if not isinstance(retry_response, dict) or retry_response.get("__http_error__"):
            raise first_exc
        raw_text = _extract_gemini_text(retry_response)
        model_used = retry_model_used
        parsed_payload = _parse_storyboard_payload(raw_text)

    parsed_payload, normalized_contract_fields, normalization_warnings = _normalize_scenario_director_scene_defaults(parsed_payload)

    try:
        storyboard_out = ScenarioDirectorStoryboardOut.model_validate(parsed_payload)
        logger.debug("[SCENARIO_DIRECTOR] validation ok scenes=%s retry=%s", len(storyboard_out.scenes), retried_for_json)
    except ValidationError as exc:
        logger.debug("[SCENARIO_DIRECTOR] validation failed errors=%s", len(exc.errors()))
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini Scenario Director response does not match the required contract.",
            status_code=502,
            details={"validationErrors": exc.errors(), "rawPreview": raw_text[:1000]},
        ) from exc

    storyboard_out = _harden_storyboard_out(storyboard_out, payload)
    audio_duration_sec = _resolve_audio_duration_sec(payload)
    coverage = _validate_audio_timeline_coverage(storyboard_out.scenes, audio_duration_sec)
    coverage_warnings: list[str] = list(coverage.get("warnings") or [])
    timeline_refinement_attempted = False
    timeline_refinement_succeeded = False
    if audio_duration_sec > 0 and coverage.get("timelineCoverageStatus") == "invalid":
        timeline_refinement_attempted = True
        refinement_body = {
            **body,
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": _build_audio_coverage_refinement_prompt(payload, storyboard_out, coverage)}],
                }
            ],
        }
        refinement_response, refinement_model_used, refinement_attempted_models = _send_director_request(api_key, refinement_body)
        attempted_models.extend(model for model in refinement_attempted_models if model not in attempted_models)
        if isinstance(refinement_response, dict) and not refinement_response.get("__http_error__"):
            refinement_raw_text = _extract_gemini_text(refinement_response)
            try:
                refinement_parsed = _parse_storyboard_payload(refinement_raw_text)
                refinement_parsed, _, refinement_normalization_warnings = _normalize_scenario_director_scene_defaults(refinement_parsed)
                refined_storyboard = ScenarioDirectorStoryboardOut.model_validate(refinement_parsed)
                refined_storyboard = _harden_storyboard_out(refined_storyboard, payload)
                refined_coverage = _validate_audio_timeline_coverage(refined_storyboard.scenes, audio_duration_sec)
                if refined_coverage.get("timelineCoverageStatus") == "ok":
                    storyboard_out = refined_storyboard
                    coverage = refined_coverage
                    coverage_warnings = list(dict.fromkeys([*coverage_warnings, *refinement_normalization_warnings, *list(refined_coverage.get("warnings") or [])]))
                    timeline_refinement_succeeded = True
                    model_used = refinement_model_used
            except (ScenarioDirectorError, ValidationError):
                coverage_warnings.append("timeline_refinement_contract_invalid")
        else:
            coverage_warnings.append("timeline_refinement_request_failed")
    if audio_duration_sec > 0 and coverage.get("timelineCoverageStatus") == "invalid":
        raise ScenarioDirectorError(
            "contract_invalid_for_timeline",
            "Scenario Director timeline does not fully cover audioDurationSec after refinement.",
            status_code=502,
            details=coverage,
        )

    director_output = _build_director_output(storyboard_out, payload)
    brain_package = _build_brain_package(storyboard_out, payload)
    return {
        "ok": True,
        "storyboardOut": storyboard_out.model_dump(mode="json"),
        "directorOutput": director_output,
        "scenario": storyboard_out.full_scenario,
        "voiceScript": storyboard_out.voice_script,
        "bgMusicPrompt": storyboard_out.music_prompt,
        "brainPackage": brain_package,
        "meta": {
            "plannerSource": "gemini",
            "modelUsed": model_used,
            "attemptedModels": attempted_models,
            "retriedForJson": retried_for_json,
            "rawGeminiTextPreview": raw_text[:2000],
            "contractNormalizationApplied": bool(normalized_contract_fields),
            "normalizedContractFields": normalized_contract_fields,
            "audioDurationSec": coverage.get("audioDurationSec"),
            "timelineStartSec": coverage.get("timelineStartSec"),
            "timelineEndSec": coverage.get("timelineEndSec"),
            "timelineCoverageSec": coverage.get("timelineCoverageSec"),
            "timelineCoverageRatio": coverage.get("timelineCoverageRatio"),
            "uncoveredTailSec": coverage.get("uncoveredTailSec"),
            "internalGapCount": coverage.get("internalGapCount"),
            "timelineCoverageStatus": coverage.get("timelineCoverageStatus"),
            "timelineCoverageWarnings": coverage.get("warnings") or [],
            "timelineRefinementAttempted": timeline_refinement_attempted,
            "timelineRefinementSucceeded": timeline_refinement_succeeded,
            "warnings": list(dict.fromkeys([*normalization_warnings, *coverage_warnings])),
        },
    }
