from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field, ConfigDict
import time

from app.core.tokens import verify_token
from app.services.auth_service import add_ledger, list_ledger, get_user_by_id

router = APIRouter()
COOKIE_NAME = "ps_token"


def _uid(req: Request):
    tok = req.cookies.get(COOKIE_NAME)
    if not tok:
        return None
    v = verify_token(tok)
    return v[0] if v else None


class TopupIn(BaseModel):
    # поддерживаем старый и новый формат фронта
    amount: int | None = Field(None, gt=0, le=100000)
    credits: int | None = Field(None, gt=0, le=100000)
    ref: str | None = None
    reason: str | None = None

    model_config = ConfigDict(
        extra="ignore",
        validate_by_name=True,
    )


@router.post("/credits/topup")
def topup(payload: TopupIn, request: Request):
    uid = _uid(request)
    if not uid:
        return {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "Нужно войти"}}

    amt = int((payload.amount or payload.credits or 0) or 0)
    if amt <= 0 or amt > 100000:
        return {"ok": False, "error": {"code": "BAD_AMOUNT", "message": "Некорректная сумма"}}

    # add_ledger(user_id, delta, reason, ref=None)
    reason = payload.reason or "TOPUP"
    # ref можно передать с фронта; если нет — генерируем уникальную
    ref = payload.ref or f"topup:{amt}:{int(time.time()*1000)}"
    add_ledger(uid, amt, reason, ref=ref)

    user = get_user_by_id(uid)
    return {"ok": True, "user": user}


@router.get("/credits/ledger")
def ledger(request: Request, limit: int = 50):
    uid = _uid(request)
    if not uid:
        return {"ok": False, "rows": []}

    limit = int(limit or 50)
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    rows = list_ledger(uid, limit=limit)
    return {"ok": True, "rows": rows}


class SpendReq(BaseModel):
    amount: int = Field(..., gt=0, le=10000)
    reason: str = Field("SPEND", max_length=120)
    ref: str = Field("", max_length=200)


@router.post("/credits/spend")
def credits_spend(req: SpendReq, request: Request):
    uid = _uid(request)
    if not uid:
        return {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "Нужно войти"}}

    user_before = get_user_by_id(uid)
    bal_before = int((user_before or {}).get("credits") or 0)

    amt = int(req.amount or 0)
    if amt <= 0 or amt > 10000:
        return {"ok": False, "error": {"code": "BAD_AMOUNT", "message": "Некорректная сумма"}}

    if bal_before < amt:
        return {
            "ok": False,
            "error": {"code": "INSUFFICIENT_CREDITS", "message": "Недостаточно кредитов."},
            "balance": bal_before,
        }

    reason = (req.reason or "SPEND").strip()[:120]
    ref = (req.ref or f"-{amt}").strip()[:200]

    # списание = отрицательный delta
    # add_ledger may raise on invalid balances; return structured error
    try:
        add_ledger(uid, -amt, reason, ref=ref)
    except Exception as e:
        return {"ok": False, "error": {"code": "SPEND_FAILED", "message": str(e) or "Не удалось списать"}}

    user_after = get_user_by_id(uid)
    bal_after = int((user_after or {}).get("credits") or 0)

    return {"ok": True, "user": user_after, "balance": bal_after}


class RefundReq(BaseModel):
    amount: int = Field(..., gt=0, le=10000)
    reason: str = Field("REFUND", max_length=120)
    ref: str = Field("", max_length=200)


@router.post("/credits/refund")
def credits_refund(req: RefundReq, request: Request):
    uid = _uid(request)
    if not uid:
        return {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "Нужно войти"}}

    amt = int(req.amount or 0)
    if amt <= 0 or amt > 10000:
        return {"ok": False, "error": {"code": "BAD_AMOUNT", "message": "Некорректная сумма"}}

    reason = (req.reason or "REFUND").strip()[:120]
    ref = (req.ref or f"+{amt}").strip()[:200]

    try:
        add_ledger(uid, amt, reason, ref=ref)
    except Exception as e:
        return {"ok": False, "error": {"code": "REFUND_FAILED", "message": str(e) or "Не удалось вернуть"}}

    user_after = get_user_by_id(uid)
    bal_after = int((user_after or {}).get("credits") or 0)
    return {"ok": True, "user": user_after, "balance": bal_after}
