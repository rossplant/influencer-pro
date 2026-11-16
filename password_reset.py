"""Single-use password links for seller and creator sign-in.

The link expires after 24 hours and works once. The raw token is returned to
the operator who creates it and is stored only as a hash. Email is not sent.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

import auth
import db
import portal
from models import PasswordReset, utcnow

TTL = timedelta(hours=24)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(*, role: str, email: str, shop_id: Optional[str], creator_id: Optional[str], created_by: str) -> str:
    """Invalidate older unused links for this account and return a new raw token."""
    cleaned = (email or "").strip().lower()
    if role not in {"seller", "creator"}:
        raise HTTPException(status_code=422, detail="Choose a seller or a creator")
    if "@" not in cleaned:
        raise HTTPException(status_code=422, detail="A sign-in email is required before a reset link can be created")
    token = secrets.token_urlsafe(32)
    now = utcnow()
    expires = now + TTL
    digest = _hash(token)
    if db.configured():
        with db.session_scope() as session:
            older = session.scalars(_match_query(role, shop_id, creator_id)).all()
            for row in older:
                if row.used_at is None:
                    row.used_at = now
            session.add(
                PasswordReset(
                    id=str(uuid.uuid4()),
                    token_hash=digest,
                    role=role,
                    email=cleaned,
                    shop_id=shop_id if role == "seller" else None,
                    creator_id=creator_id if role == "creator" else None,
                    expires_at=expires,
                    used_at=None,
                    created_at=now,
                    created_by=(created_by or "")[:255],
                )
            )
        return token
    for row in db.memory_password_resets:
        same = row.role == role and (row.shop_id == shop_id if role == "seller" else row.creator_id == creator_id)
        if same and row.used_at is None:
            row.used_at = now
    db.memory_password_resets.append(
        _memory_row(digest, role, cleaned, shop_id, creator_id, expires, now, created_by)
    )
    return token


def peek(token: str):
    row = _find(token)
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        return None
    return row


def redeem(token: str, password: str, password_confirm: str) -> dict:
    row = _find(token)
    now = utcnow()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise HTTPException(status_code=410, detail="This link has expired or was already used. Ask Plantr for a new one.")
    if len(password or "") < portal.MIN_PASSWORD:
        raise HTTPException(status_code=422, detail="Use at least 12 characters.")
    if not auth.passwords_match(password or "", password_confirm or ""):
        raise HTTPException(status_code=422, detail="Enter the same password twice.")
    result = portal.create_login(
        role=row.role,
        email=row.email,
        password=password,
        password_confirm=password_confirm,
        shop_id=row.shop_id,
        creator_id=row.creator_id,
    )
    _mark_used(row, now)
    return result


def _match_query(role: str, shop_id: Optional[str], creator_id: Optional[str]):
    query = select(PasswordReset).where(PasswordReset.role == role)
    if role == "seller":
        return query.where(PasswordReset.shop_id == shop_id)
    return query.where(PasswordReset.creator_id == creator_id)


def _find(token: str):
    digest = _hash(token or "")
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(PasswordReset).where(PasswordReset.token_hash == digest))
            if row is None:
                return None
            return _copy(row)
    return next((row for row in db.memory_password_resets if row.token_hash == digest), None)


def _mark_used(row, now) -> None:
    if db.configured():
        with db.session_scope() as session:
            stored = session.get(PasswordReset, row.id)
            if stored is not None and stored.used_at is None:
                stored.used_at = now
        return
    row.used_at = now


def _copy(row: PasswordReset):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=row.id,
        token_hash=row.token_hash,
        role=row.role,
        email=row.email,
        shop_id=row.shop_id,
        creator_id=row.creator_id,
        expires_at=row.expires_at,
        used_at=row.used_at,
        created_at=row.created_at,
        created_by=row.created_by,
    )


def _memory_row(digest, role, email, shop_id, creator_id, expires, now, created_by):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=str(uuid.uuid4()),
        token_hash=digest,
        role=role,
        email=email,
        shop_id=shop_id if role == "seller" else None,
        creator_id=creator_id if role == "creator" else None,
        expires_at=expires,
        used_at=None,
        created_at=now,
        created_by=(created_by or "")[:255],
    )
