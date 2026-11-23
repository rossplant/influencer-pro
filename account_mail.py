"""Transactional account email. Nothing is sent when Resend is unset.

Tokens are signed and expire with the session secret. No password is included
in the message. This does not send marketing.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import auth
import db
import emailer

logger = logging.getLogger("plantr.account_mail")


def _account(email: str):
    cleaned = (email or "").strip().lower()
    if db.configured():
        from sqlalchemy import select

        from models import PortalAccount

        with db.session_scope() as session:
            row = session.scalar(select(PortalAccount).where(PortalAccount.email == cleaned))
            if row is None:
                return None
            return SimpleNamespace(email=row.email, role=row.role, shop_id=row.shop_id, creator_id=row.creator_id)
    row = next((item for item in db.memory_portal_accounts if item.email == cleaned), None)
    if row is None:
        return None
    return SimpleNamespace(
        email=row.email,
        role=row.role,
        shop_id=getattr(row, "shop_id", None),
        creator_id=getattr(row, "creator_id", None),
    )


def request_verification(email: str, base_url: str) -> None:
    account = _account(email)
    if account is None:
        logger.info("signup verification skipped; no account for that address")
        return
    token = auth.issue_form_token(f"verify:{account.email}")
    emailer.send("signup_verification", account.email, url=f"{base_url}/account/verify/{token}")


def request_password_reset(email: str, base_url: str) -> None:
    account = _account(email)
    if account is None:
        logger.info("password reset skipped; no account for that address")
        return
    token = auth.issue_form_token(f"reset:{account.email}")
    emailer.send("password_reset", account.email, url=f"{base_url}/account/reset/{token}")


def read_account_token(token: str, kind: str):
    payload = auth._read_signed(token)
    if payload is None:
        return None
    purpose = str(payload.get("purpose") or "")
    prefix = f"{kind}:"
    if not purpose.startswith(prefix):
        return None
    return _account(purpose[len(prefix):])
