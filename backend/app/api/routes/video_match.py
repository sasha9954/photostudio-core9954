from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_user

router = APIRouter(prefix="/video-match")

VIDEO_MATCH_OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "static" / "assets" / "video_match_outputs"


class VideoMatchBlock(BaseModel):
    id: str
    audioSceneId: str | None = None
    targetStartSec: float = 0
    targetEndSec: float = 0
    sourceVideoStartSec: float = 0
    sourceVideoEndSec: float = 0


class AssembleVideoMatchRequest(BaseModel):
    sourceVideoPath: str
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
        for idx, block in enumerate(blocks):
            start = max(0.0, float(block.sourceVideoStartSec or 0))
            end = max(start, float(block.sourceVideoEndSec or start))
            duration = max(0.01, end - start)
            clip_path = work_dir / f"clip_{idx:04d}.mp4"
            _run_ffmpeg([
                "ffmpeg", "-y", "-ss", f"{start:.6f}", "-i", str(source_path), "-t", f"{duration:.6f}",
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
        audio_warning = ""
        audio_input = Path(str(payload.audioPath or "")).expanduser() if payload.audioPath else None
        has_audio = bool(audio_input and audio_input.is_file())

        if has_audio:
            _run_ffmpeg([
                "ffmpeg", "-y", "-i", str(merged_video), "-i", str(audio_input), "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-shortest", str(output_path),
            ])
        else:
            _run_ffmpeg(["ffmpeg", "-y", "-i", str(merged_video), "-c", "copy", str(output_path)])
            if payload.audioPath or payload.audioUrl:
                audio_warning = "audio_missing_backend_path"

        duration_sec = _probe_duration_sec(output_path)
        result = {
            "ok": True,
            "outputUrl": f"/api/video-match/output/{output_name}",
            "outputPath": str(output_path),
            "durationSec": round(duration_sec, 3),
            "audioUsed": has_audio,
        }
        if audio_warning:
            result["warning"] = audio_warning
        return result
    finally:
        for path in sorted(work_dir.glob("**/*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        work_dir.rmdir()
