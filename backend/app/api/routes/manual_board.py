from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.static_paths import STATIC_DIR, ensure_static_dirs
from app.core.tokens import verify_token

router = APIRouter(prefix="/manual-board")

_COOKIE_NAME = "ps_token"
_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.@%=-]+")
_MAX_BOARD_BYTES = 25 * 1024 * 1024


class ManualBoardSaveIn(BaseModel):
    node_id: str | None = Field(default=None, alias="nodeId")
    account_key: str | None = Field(default=None, alias="accountKey")
    project: dict[str, Any]

    model_config = {"populate_by_name": True}


def _safe_path_segment(value: str | None, fallback: str = "guest") -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or "").strip()).strip("._/")
    return cleaned[:160] or fallback


def _current_user_account_key(request: Request) -> str:
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return ""
    verified = verify_token(token)
    user_id = verified[0] if verified else None
    return f"user_{user_id}" if user_id else ""


def _resolve_account_key(request: Request, supplied: str | None = None) -> str:
    return _safe_path_segment(_current_user_account_key(request) or supplied or "guest")


def _manual_board_dir(account_key: str) -> Path:
    ensure_static_dirs()
    root = STATIC_DIR / "manual_boards" / _safe_path_segment(account_key)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _manual_board_path(account_key: str, node_id: str) -> Path:
    safe_node_id = _safe_path_segment(node_id, fallback="default")
    return _manual_board_dir(account_key) / f"{safe_node_id}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> int:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > _MAX_BOARD_BYTES:
        raise HTTPException(status_code=413, detail="manual board snapshot is too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
    return len(data)


@router.post("/save")
def save_manual_board(payload: ManualBoardSaveIn, request: Request):
    project = payload.project if isinstance(payload.project, dict) else {}
    node_id = str(
        payload.node_id
        or project.get("sourceNodeId")
        or project.get("ownerNodeId")
        or project.get("nodeId")
        or ""
    ).strip()
    if not node_id:
        raise HTTPException(status_code=400, detail="nodeId is required")

    account_key = _resolve_account_key(request, payload.account_key)
    now = datetime.now(timezone.utc).isoformat()
    board = {
        **project,
        "nodeId": str(project.get("nodeId") or node_id),
        "sourceNodeId": str(project.get("sourceNodeId") or node_id),
        "ownerNodeId": str(project.get("ownerNodeId") or project.get("sourceNodeId") or node_id),
        "durable_saved_at": now,
    }
    envelope = {
        "ok": True,
        "schema": "photostudio_manual_board_durable_v1",
        "account_key": account_key,
        "nodeId": node_id,
        "saved_at": now,
        "project": board,
    }
    path = _manual_board_path(account_key, node_id)
    written_bytes = _atomic_write_json(path, envelope)
    return {"ok": True, "nodeId": node_id, "accountKey": account_key, "bytes": written_bytes, "updatedAt": now}


@router.get("/load/{node_id}")
def load_manual_board(
    node_id: str,
    request: Request,
    account_key: str | None = Query(default=None, alias="accountKey"),
):
    account = _resolve_account_key(request, account_key)
    path = _manual_board_path(account, node_id)
    if not path.is_file():
        return {"ok": True, "found": False, "nodeId": node_id, "accountKey": account, "project": None}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="manual board snapshot is corrupted") from exc
    project = data.get("project") if isinstance(data, dict) else None
    return {
        "ok": True,
        "found": isinstance(project, dict),
        "nodeId": node_id,
        "accountKey": account,
        "updatedAt": data.get("saved_at") if isinstance(data, dict) else None,
        "project": project if isinstance(project, dict) else None,
    }
