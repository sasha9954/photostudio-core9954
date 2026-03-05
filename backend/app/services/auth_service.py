import os
import uuid
import hashlib
from datetime import datetime
from typing import Optional, Dict, Any
from app.db.sqlite import db

def _now():
    return datetime.utcnow().isoformat() + "Z"

def _hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return dk.hex()

def create_user(email: str, name: str, password: str) -> Dict[str, Any]:
    email_n = email.strip().lower()
    if not email_n or "@" not in email_n:
        raise ValueError("Некорректный email")
    if not password or len(password) < 6:
        raise ValueError("Пароль слишком короткий (минимум 6 символов)")
    salt = os.urandom(16)
    pwd_hash = _hash_password(password, salt)
    uid = "u_" + uuid.uuid4().hex[:16]
    with db() as con:
        try:
            con.execute(
                "INSERT INTO users(id,email,name,pwd_hash,pwd_salt,created_at) VALUES(?,?,?,?,?,?)",
                (uid, email_n, name.strip() or email_n.split("@")[0], pwd_hash, salt.hex(), _now())
            )
        except Exception as ex:
            msg = str(ex).lower()
            if "unique" in msg or "constraint" in msg:
                raise ValueError("Этот email уже зарегистрирован")
            raise
    # стартовый баланс 0 (пополнение отдельно)
    return get_user_by_id(uid)

def verify_login(email: str, password: str) -> Dict[str, Any]:
    email_n = email.strip().lower()
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email_n,)).fetchone()
    if not row:
        raise ValueError("Неверный email или пароль")
    salt = bytes.fromhex(row["pwd_salt"])
    pwd_hash = _hash_password(password, salt)
    if pwd_hash != row["pwd_hash"]:
        raise ValueError("Неверный email или пароль")
    return get_user_by_id(row["id"])

def get_user_by_id(user_id: str) -> Dict[str, Any]:
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("Пользователь не найден")
        # balance = sum ledger
        bal_row = con.execute(
            "SELECT COALESCE(SUM(delta),0) as bal FROM ledger WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        bal = int(bal_row["bal"] or 0)

        # Защита от старых данных: если в БД уже накопился отрицательный баланс
        # (например, из-за прежнего бага/ручных тестов), автоматически выравниваем до 0.
        # Это важно, потому что add_ledger запрещает уходить в минус и дальше система
        # может "заклинить" на отрицательном балансе.
        if bal < 0:
            lid = "l_" + uuid.uuid4().hex[:16]
            con.execute(
                "INSERT INTO ledger(id,user_id,delta,reason,ref,created_at) VALUES(?,?,?,?,?,?)",
                (lid, user_id, int(-bal), "AUTO_CORRECTION", "NEGATIVE_BALANCE", _now()),
            )
            bal = 0
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "created_at": row["created_at"],
        "credits": bal,
    }

def add_ledger(user_id: str, delta: int, reason: str, ref: str = None) -> Dict[str, Any]:
    if not isinstance(delta, int) or delta == 0:
        raise ValueError("delta должен быть целым и не 0")
    lid = "l_" + uuid.uuid4().hex[:16]
    with db() as con:
        # Запрещаем уходить в минус (никаких "кредитов в долг").
        # BEGIN IMMEDIATE — чтобы параллельные запросы не прошли чек одновременно.
        con.execute("BEGIN IMMEDIATE")

        # Idempotency: if the same (user_id, reason, ref) was already recorded,
        # do NOT insert again and do NOT re-check balance.
        if ref:
            existing = con.execute(
                "SELECT id FROM ledger WHERE user_id=? AND reason=? AND ref=? LIMIT 1",
                (user_id, reason, ref),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "idempotent": True}

        row = con.execute(
            "SELECT COALESCE(SUM(delta), 0) AS bal FROM ledger WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        bal = int(row["bal"]) if row is not None else 0
        next_bal = bal + int(delta)
        if int(delta) < 0 and next_bal < 0:
            raise ValueError("Недостаточно кредитов")

        con.execute(
            "INSERT INTO ledger(id,user_id,delta,reason,ref,created_at) VALUES(?,?,?,?,?,?)",
            (lid, user_id, int(delta), reason, ref, _now())
        )
    return {"id": lid}

def list_ledger(user_id: str, limit: int = 50):
    lim = max(1, min(int(limit or 50), 200))
    with db() as con:
        rows = con.execute(
            "SELECT id,delta,reason,ref,created_at FROM ledger WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, lim)
        ).fetchall()
    return [dict(r) for r in rows]
