import os
import json
import uuid
import shutil
import subprocess
import tempfile
import time
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Body

from app.core.config import settings
from app.api.deps import get_current_user
from app.db.sqlite import db
from app.services.auth_service import add_ledger

# Engine
from app.engine.video_engine import generate_video

# Engine
from app.engine.video_engine import generate_video

router = APIRouter(prefix="/video")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -----------------------
# Jobs (server-side) — so generation continues after leaving page
# -----------------------


def _job_create(uid: str, action: str) -> str:
    job_id = f"vd_{uuid.uuid4().hex[:16]}"
    now = _now_iso()
    with db() as con:
        con.execute(
            "INSERT INTO video_jobs(job_id, user_id, action, state, progress, result_json, error, spent, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, uid, action, "queued", 0, None, None, 0, now, now),
        )
    return job_id


def _job_update(job_id: str, **fields):
    if not job_id:
        return
    allowed = {"state", "progress", "result_json", "error", "spent"}
    sets = []
    vals = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        vals.append(v)
    sets.append("updated_at=?")
    vals.append(_now_iso())
    vals.append(job_id)
    with db() as con:
        con.execute(f"UPDATE video_jobs SET {', '.join(sets)} WHERE job_id=?", tuple(vals))


def _job_get(uid: str, job_id: str) -> dict | None:
    with db() as con:
        row = con.execute(
            "SELECT job_id, user_id, action, state, progress, result_json, error, spent, created_at, updated_at FROM video_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if not row:
            return None
        if row["user_id"] != uid:
            return None
        out = dict(row)
        try:
            out["result"] = json.loads(out.get("result_json") or "null")
        except Exception:
            out["result"] = None
        out.pop("result_json", None)
        return out


def _job_find_running(uid: str, action: str | None = None) -> str | None:
    q = "SELECT job_id FROM video_jobs WHERE user_id=? AND state IN ('queued','running')"
    args = [uid]
    if action:
        q += " AND action=?"
        args.append(action)
    q += " ORDER BY updated_at DESC LIMIT 1"
    with db() as con:
        row = con.execute(q, tuple(args)).fetchone()
        return row["job_id"] if row else None


def _ensure_videos_dir() -> Path:
    base_app = Path(__file__).resolve().parents[2]  # backend/app
    videos_dir = base_app / "static" / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    return videos_dir


def _public_url_for_video(filename: str) -> str:
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/static/videos/{filename}"


@router.post("/upload")
async def upload_video(file: UploadFile = File(...), user=Depends(get_current_user)):
    # NOTE: user dependency is important for account scoping in future.
    if not file:
        raise HTTPException(status_code=400, detail="No file")
    ct = (file.content_type or "").lower()
    if not (ct.startswith("video/") or file.filename.lower().endswith((".mp4", ".webm"))):
        raise HTTPException(status_code=400, detail="Unsupported file type")

    videos_dir = _ensure_videos_dir()
    ext = ".mp4" if file.filename.lower().endswith(".mp4") else (".webm" if file.filename.lower().endswith(".webm") else ".mp4")
    safe_name = f"clip_{user['id']}_{abs(hash(file.filename))}{ext}"
    out_path = videos_dir / safe_name
    with out_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    return {"url": _public_url_for_video(safe_name)}


@router.post("/generate")
async def generate(payload: dict = Body(...), user=Depends(get_current_user)):
    """Generate a short video (Kling or Veo) from 1..3 reference images.

    Accepts flexible payload keys to avoid 422 issues:
      provider: "kling" | "veo"
      sourceImages: [url|dataUrl, ...]   (or source_images)
      aspectRatio: "9:16" | "1:1" | "16:9" (or format)
      seconds: 5|8|10
      prompt, camera, lighting
      count: number of videos (default 1)
    """
    provider = (payload.get("provider") or payload.get("engine") or "kling").strip().lower()

    # Images (allow multiple keys)
    srcs = payload.get("sourceImages") or payload.get("source_images") or payload.get("images") or []
    if isinstance(srcs, str):
        # allow passing single string
        srcs = [srcs]
    if not isinstance(srcs, list) or not srcs or not any(srcs):
        raise HTTPException(status_code=400, detail={"code": "NO_SOURCE_IMAGES", "message": "sourceImages is required"})

    # Format
    fmt = (payload.get("aspectRatio") or payload.get("format") or "9:16").strip()
    if fmt not in ("9:16", "1:1", "16:9"):
        fmt = "9:16"

    seconds = payload.get("seconds") or payload.get("durationSeconds") or payload.get("duration") or 5
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 5

    prompt = (payload.get("prompt") or "").strip()
    camera = (payload.get("camera") or "static").strip()
    lighting = (payload.get("lighting") or "soft").strip()

    count = payload.get("count") or 1
    try:
        count = int(count)
    except Exception:
        count = 1
    count = max(1, min(3, count))

    model = "classic" if provider in ("kling", "standard") else "premium"
    # For Veo: pass list (up to 3) to engine; for Kling: pass first image only
    source_for_engine = srcs if model == "premium" else (srcs[0] if srcs else "")

    videos: List[str] = []
    last_frames: List[Optional[str]] = []
    warnings: List[Optional[str]] = []

    for _ in range(count):
        res = generate_video(
            kind="video_from_image",
            source_image=source_for_engine,
            fmt=fmt,
            model=model,
            camera=camera,
            prompt=prompt,
            seconds=seconds,
            lighting=lighting,
        )
        if not isinstance(res, dict) or not res.get("ok"):
            # normalize error
            code = (res or {}).get("code") if isinstance(res, dict) else None
            msg = (res or {}).get("message") if isinstance(res, dict) else "Generation failed"
            raise HTTPException(status_code=400, detail={"code": code or "GEN_FAILED", "message": msg, "raw": res})

        url = res.get("videoUrl") or res.get("video_url") or res.get("url")
        lf = res.get("lastFrameUrl") or res.get("last_frame_url")
        warn = res.get("warning")
        if url:
            videos.append(url)
            last_frames.append(lf)
            warnings.append(warn)

    return {"ok": True, "provider": provider, "videos": videos, "lastFrames": last_frames, "warnings": warnings}


@router.post("/generateJob")
async def generate_job(payload: dict = Body(...), user=Depends(get_current_user)):
    """Create a long-running generation job so UI can safely refresh/leave and resume."""
    uid = user["id"]

    existing = _job_find_running(uid, action="generate")
    if existing:
        return {"ok": True, "jobId": existing, "resumed": True}

    provider = (payload.get("provider") or payload.get("engine") or "kling").strip().lower()
    srcs = payload.get("sourceImages") or payload.get("source_images") or payload.get("images") or []
    if isinstance(srcs, str):
        srcs = [srcs]
    if not isinstance(srcs, list) or not srcs or not any(srcs):
        raise HTTPException(status_code=400, detail={"code": "NO_SOURCE_IMAGES", "message": "sourceImages is required"})

    fmt = (payload.get("aspectRatio") or payload.get("format") or "9:16").strip()
    if fmt not in ("9:16", "1:1", "16:9"):
        fmt = "9:16"

    seconds = payload.get("seconds") or payload.get("durationSeconds") or payload.get("duration") or 5
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 5
    seconds = max(3, min(15, seconds))

    prompt = (payload.get("prompt") or "").strip()
    camera = (payload.get("camera") or "static").strip()
    lighting = (payload.get("lighting") or "soft").strip()

    model = "classic" if provider in ("kling", "standard") else "premium"
    source_for_engine = srcs if model == "premium" else (srcs[0] if srcs else "")

    # Pricing
    # ULTRA(Veo/premium): фикс 2 кредита за одну генерацию (независимо от числа рефов 1–3)
    # STANDARD(Kling/classic): 1 кредит за 5s, 2 кредита за 10s
    cost = 2 if model == "premium" else (2 if int(seconds) >= 10 else 1)

    # Veo стандарт: 8 секунд фикс
    if model == "premium":
        seconds = 8

    job_id = _job_create(uid, action="generate")

    def _run():
        try:
            _job_update(job_id, state="running", progress=5)
            try:
                add_ledger(uid, -cost, "VIDEO_GENERATE", ref=f"{job_id}")
                _job_update(job_id, spent=cost)
            except Exception as e:
                _job_update(job_id, state="error", progress=100, error=str(e))
                return

            _job_update(job_id, progress=15)
            res = generate_video(
                kind="video_from_image",
                source_image=source_for_engine,
                fmt=fmt,
                model=model,
                camera=camera,
                prompt=prompt,
                seconds=seconds,
                lighting=lighting,
            )
            if not isinstance(res, dict) or not res.get("ok"):
                code = (res or {}).get("code") if isinstance(res, dict) else None
                msg = (res or {}).get("message") if isinstance(res, dict) else "Generation failed"
                _job_update(job_id, state="error", progress=100, error=json.dumps({"code": code or "GEN_FAILED", "message": msg, "raw": res}, ensure_ascii=False))
                try:
                    add_ledger(uid, cost, "REFUND", ref=f"VIDEO:{job_id}")
                except Exception:
                    pass
                return

            url = res.get("videoUrl") or res.get("video_url") or res.get("url")
            lf = res.get("lastFrameUrl") or res.get("last_frame_url")
            warn = res.get("warning")
            out = {
                "provider": provider,
                "videos": [url] if url else [],
                "lastFrames": [lf] if lf else [None],
                "warnings": [warn] if warn else [None],
                "format": fmt,
                "seconds": seconds,
            }
            _job_update(job_id, state="done", progress=100, result_json=json.dumps(out, ensure_ascii=False))
        except Exception as e:
            _job_update(job_id, state="error", progress=100, error=str(e))
            try:
                add_ledger(uid, cost, "REFUND", ref=f"VIDEO:{job_id}")
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "jobId": job_id}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user=Depends(get_current_user)):
    uid = user["id"]
    j = _job_get(uid, job_id)
    if not j:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Job not found"})
    return {"ok": True, "job": j}


@router.post("/merge")
async def merge_videos(payload: dict, user=Depends(get_current_user)):
    clip_urls: List[str] = payload.get("clipUrls") or []
    if not isinstance(clip_urls, list) or len(clip_urls) < 2:
        raise HTTPException(status_code=400, detail="clipUrls must contain at least 2 urls")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found. Install ffmpeg and add to PATH")

    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    prefix = f"{base}/static/videos/" if base else "/static/videos/"
    videos_dir = _ensure_videos_dir()

    # Map public urls -> local files (only our own static/videos)
    local_files: List[Path] = []
    for u in clip_urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u:
            continue
        if base and u.startswith(prefix):
            fname = u.split("/static/videos/")[-1]
        elif (not base) and "/static/videos/" in u:
            fname = u.split("/static/videos/")[-1]
        else:
            raise HTTPException(status_code=400, detail="Only /static/videos/* urls are allowed")
        p = videos_dir / fname
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Missing clip: {fname}")
        local_files.append(p)

    if len(local_files) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 valid local clips")

    out_name = f"merge_{user['id']}_{int(time.time()*1000)}.mp4"
    out_path = videos_dir / out_name

    # Safer merge: concat + re-encode to avoid codec mismatch between providers
    with tempfile.TemporaryDirectory() as td:
        list_path = Path(td) / "list.txt"
        lines = []
        for p in local_files:
            escaped = str(p).replace("'", "'\''")
            lines.append(f"file '{escaped}'")
        list_path.write_text("\n".join(lines), encoding="utf-8")

        cmd = [
            ffmpeg,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-vf", "fps=30,format=yuv420p",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"ffmpeg failed: {proc.stderr[-400:]}" )

    videos_dir = _ensure_videos_dir()

    def _to_local_video_filename(u: str) -> str:
        """Normalize a user-provided url/path into a filename inside static/videos.

        We accept:
          - /static/videos/<file>
          - http(s)://host/static/videos/<file>
          - static/videos/<file>
          - /videos/<file>  (legacy)
          - clip_<...>.mp4|webm (bare filename)
        and reject anything else.
        """
        if not isinstance(u, str):
            return ""
        s = unquote(u.strip())
        if not s:
            return ""
        s = s.replace("\\", "/")

        # absolute url -> take only the path
        if s.startswith("http://") or s.startswith("https://"):
            try:
                s = urlparse(s).path or ""
            except Exception:
                s = ""

        # tolerate legacy /videos/<file>
        if s.startswith("/videos/"):
            s = "/static" + s

        # tolerate missing leading slash
        if s.startswith("static/videos/"):
            s = "/" + s

        # bare filename
        if s.startswith("clip_") and (s.endswith(".mp4") or s.endswith(".webm")):
            return s

        marker = "/static/videos/"
        if marker in s:
            return s.split(marker, 1)[1]

        return ""

    # Map public urls -> local files (only our own static/videos)
    local_files: List[Path] = []
    for u in clip_urls:
        fname = _to_local_video_filename(u)
        if not fname:
            raise HTTPException(status_code=400, detail="Only /static/videos/* urls are allowed")
        p = videos_dir / fname
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Missing clip: {fname}")
        local_files.append(p)

    if len(local_files) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 valid local clips")

    out_name = f"merge_{user['id']}_{int(time.time()*1000)}.mp4"
    out_path = videos_dir / out_name

    # Safer merge: concat + re-encode to avoid codec mismatch between providers
    with tempfile.TemporaryDirectory() as td:
        list_path = Path(td) / "list.txt"
        lines = []
        for p in local_files:
            escaped = str(p).replace("'", "'\''")
            lines.append(f"file '{escaped}'")
        list_path.write_text("\n".join(lines), encoding="utf-8")

        cmd = [
            ffmpeg,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-vf", "fps=30,format=yuv420p",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"ffmpeg failed: {proc.stderr[-400:]}" )

    return {"url": _public_url_for_video(out_name)}