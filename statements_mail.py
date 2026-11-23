"""Monthly commission statements, then one transactional email per new statement."""

from __future__ import annotations

import commission
import db
import emailer
from models import PortalAccount


def _email(role: str, subject_id: str) -> str:
    if not subject_id or not db.configured():
        return ""
    from sqlalchemy import select

    with db.session_scope() as session:
        query = select(PortalAccount).where(PortalAccount.role == role)
        if role == "seller":
            query = query.where(PortalAccount.shop_id == subject_id)
        else:
            query = query.where(PortalAccount.creator_id == subject_id)
        row = session.scalar(query)
        return "" if row is None else row.email


def run_month(period: str, *, actor: str = "system", shop_id: str | None = None) -> dict:
    result = commission.generate_statements(period, shop_id=shop_id, actor=actor)
    for statement_id in result.get("created") or []:
        detail = commission.statement_detail(statement_id, viewer="admin")
        label = str(detail.get("period") or period)
        for role, subject in (("seller", detail.get("shop_id")), ("creator", detail.get("creator_id"))):
            address = _email(role, str(subject or ""))
            if address:
                emailer.send("statement_ready", address, period=label)
    return result
