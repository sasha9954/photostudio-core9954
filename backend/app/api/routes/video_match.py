from __future__ import annotations

import subprocess
import uuid
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_user

router = APIRouter(prefix="/video-match")

VIDEO_MATCH_OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "static" / "assets" / "video_match_outputs"
VIDEO_MATCH_OVERRIDES_DIR = Path(__file__).resolve().parents[2] / "static" / "assets" / "video_match_overrides"


class VideoMatchBlock(BaseModel):
    id: str
    audioSceneId: str | None = None
    targetStartSec: float = 0
    targetEndSec: float = 0
    sourceVideoStartSec: float = 0
    sourceVideoEndSec: float = 0
    overrideVideoPath: str | None = None
    overrideVideoUrl: str | None = None
    candidateType: str | None = None
    sourceKind: str | None = None


class AssembleVideoMatchRequest(BaseModel):
    sourceVideoPath: str
    includeAudio: bool = False
    audioPath: str | None = None
    audioUrl: str | None = None
    outputFormat: Literal["16:9"] = "16:9"
    previewQuality: Literal["720p"] = "720p"
    blocks: list[VideoMatchBlock] = Field(default_factory=list)


def _run_ffmpeg(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ffmpeg_not_found") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=400, detail={"code": "ffmpeg_failed", "message": exc.stderr[-1200:]}) from exc


def _probe_duration_sec(path: Path) -> float:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return max(0.0, float((proc.stdout or "0").strip() or 0))
    except Exception:
        return 0.0


@router.get("/output/{filename}")
async def get_video_match_output(filename: str, _user=Depends(get_current_user)):
    safe_name = Path(filename).name
    if safe_name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail={"code": "invalid_filename"})
    output_path = VIDEO_MATCH_OUTPUTS_DIR / safe_name
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail={"code": "output_not_found", "message": safe_name})
    return FileResponse(output_path, media_type="video/mp4", filename=safe_name)


@router.get("/override/{filename}")
async def get_video_match_override(filename: str, _user=Depends(get_current_user)):
    safe_name = Path(filename).name
    if safe_name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail={"code": "invalid_filename"})
    path = VIDEO_MATCH_OVERRIDES_DIR / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "override_not_found"})
    return FileResponse(path, media_type="video/mp4", filename=safe_name)


@router.post("/override-upload")
async def upload_video_match_override(
    file: UploadFile = File(...),
    nodeId: str | None = Form(default=None),
    segmentId: str | None = Form(default=None),
    candidateType: str = Form(default="user_override"),
    _user=Depends(get_current_user),
):
    _ = (nodeId, segmentId)
    original_name = str(file.filename or "override.mp4").strip() or "override.mp4"
    suffix = Path(original_name).suffix.lower()
    allowed_ext = {".mp4", ".mov", ".webm", ".mkv"}
    content_type = str(file.content_type or "").lower()
    if not (content_type.startswith("video/") or suffix in allowed_ext):
        raise HTTPException(status_code=400, detail={"code": "invalid_override_type"})
    safe_ext = suffix if suffix in allowed_ext else ".mp4"
    VIDEO_MATCH_OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
    stored_filename = f"video_match_override_{uuid.uuid4().hex}{safe_ext}"
    stored_path = VIDEO_MATCH_OVERRIDES_DIR / stored_filename
    data = await file.read()
    stored_path.write_bytes(data)
    duration_sec = _probe_duration_sec(stored_path)
    return {
        "ok": True,
        "candidateType": candidateType,
        "overrideVideoPath": str(stored_path),
        "overrideVideoUrl": f"/api/video-match/override/{stored_filename}",
        "filename": original_name,
        "storedFilename": stored_filename,
        "durationSec": round(float(duration_sec or 0), 3),
    }


@router.post("/assemble")
async def assemble_video_match_preview(payload: AssembleVideoMatchRequest = Body(...), _user=Depends(get_current_user)):
    source_path = Path(payload.sourceVideoPath).expanduser()
    if not source_path.is_file():
        raise HTTPException(status_code=400, detail={"code": "source_video_not_found", "message": str(source_path)})

    blocks = sorted(payload.blocks, key=lambda item: float(item.targetStartSec or 0))
    if not blocks:
        raise HTTPException(status_code=400, detail={"code": "blocks_empty", "message": "No video blocks provided"})

    VIDEO_MATCH_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    work_dir = VIDEO_MATCH_OUTPUTS_DIR / f"tmp_{job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        clip_paths: list[Path] = []
        warnings: list[str] = []
        override_used_count = 0
        for idx, block in enumerate(blocks):
            override_path = Path(str(block.overrideVideoPath or "")).expanduser() if block.overrideVideoPath else None
            target_duration = max(0.01, float(block.targetEndSec or 0) - float(block.targetStartSec or 0))
            source_for_clip = source_path
            start = max(0.0, float(block.sourceVideoStartSec or 0))
            end = max(start, float(block.sourceVideoEndSec or start))
            duration = max(0.01, end - start)
            if override_path:
                if override_path.is_file():
                    override_used_count += 1
                    source_for_clip = override_path
                    start = 0.0
                    override_duration = _probe_duration_sec(override_path)
                    if override_duration > 0 and override_duration < target_duration:
                        warnings.append(f"override_shorter_than_target:{block.id}")
                    duration = max(0.01, min(override_duration if override_duration > 0 else target_duration, target_duration))
                else:
                    warnings.append(f"override_video_missing:{block.id}")
            clip_path = work_dir / f"clip_{idx:04d}.mp4"
            _run_ffmpeg([
                "ffmpeg", "-y", "-ss", f"{start:.6f}", "-i", str(source_for_clip), "-t", f"{duration:.6f}",
                "-an", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(clip_path),
            ])
            clip_paths.append(clip_path)

        concat_list = work_dir / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in clip_paths), encoding="utf-8")
        merged_video = work_dir / "merged_video.mp4"
        _run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(merged_video)])

        output_name = f"video_match_preview_{job_id}.mp4"
        output_path = VIDEO_MATCH_OUTPUTS_DIR / output_name
        audio_path_raw = str(payload.audioPath or "").strip()
        audio_input = Path(audio_path_raw).expanduser() if audio_path_raw else None
        has_audio = False
        if payload.includeAudio:
            if not audio_path_raw:
                raise HTTPException(status_code=400, detail={"code": "AUDIO_PATH_REQUIRED", "message": "Для сборки с аудио укажите путь к файлу"})
            if not (audio_input and audio_input.is_file()):
                raise HTTPException(status_code=400, detail={"code": "AUDIO_PATH_NOT_FOUND", "message": "Аудиофайл не найден по указанному пути"})
            if not os.access(audio_input, os.R_OK):
                raise HTTPException(status_code=400, detail={"code": "AUDIO_PATH_NOT_FOUND", "message": "Аудиофайл не найден по указанному пути"})
            has_audio = True
        elif audio_input and audio_input.is_file():
            has_audio = True

        if has_audio:
            _run_ffmpeg([
                "ffmpeg", "-y", "-i", str(merged_video), "-i", str(audio_input), "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-shortest", str(output_path),
            ])
        else:
            _run_ffmpeg(["ffmpeg", "-y", "-i", str(merged_video), "-c", "copy", str(output_path)])

        duration_sec = _probe_duration_sec(output_path)
        result = {
            "ok": True,
            "outputUrl": f"/api/video-match/output/{output_name}",
            "outputPath": str(output_path),
            "durationSec": round(duration_sec, 3),
            "audioUsed": has_audio,
            "overrideUsedCount": override_used_count,
            "warnings": warnings + [f"override_used_count:{override_used_count}"],
        }
        return result
    finally:
        for path in sorted(work_dir.glob("**/*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        work_dir.rmdir()
