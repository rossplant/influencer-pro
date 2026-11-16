"""Light commission tracking for the pilot. Plantr never holds or moves commission money.

Owed amounts are estimates from tracked sales. The seller pays the creator directly and
marks it paid; the creator confirms or disputes. An overdue unpaid amount, or an open
dispute, blocks only NEW collab offers and NEW shortlist acceptances for that seller.
Live collabs, links, and edits or corrections to existing briefs are never blocked.

No bank or payout fields exist. Free text that looks like bank details is rejected.
Every action is written to commission_audit_log (append-only).
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import functools
import threading
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import or_, select

import db
from models import (
    AbnWarningAck,
    AttributedOrder,
    Brief,
    CollabAgreement,
    CollabBlockLift,
    CommissionAuditLog,
    CommissionCorrection,
    CommissionStatement,
    CommissionStatementLine,
    Creator,
    Product,
    SellerPaymentResponse,
    Shop,
    Shortlist,
    ShortlistItem,
    utcnow,
)

ESTIMATE_LABEL = "Estimate based on tracked sales. Your agreement with the creator governs the amount payable."
UNCONFIRMED_LABEL = "Marked paid by Seller, not confirmed"
ABN_WARNING_VERSION = "abn-warning-v1"
ABN_WARNING = (
    "This creator hasn't given Plantr an ABN. If you pay a supplier who doesn't quote an ABN, "
    "you may need to withhold 47% of the payment for tax (no-ABN withholding). "
    "Please check with your accountant."
)
SHOPIFY_COLLABS_WARNING = (
    "This collab is paid through Shopify Collabs. If you also pay this creator outside Shopify Collabs "
    "for the same sales, you could pay twice. Check your Shopify Collabs payouts before paying separately."
)
UNDER_REVIEW_LABEL = "Under review"
OPEN_CORRECTION = ("pending_creator", "disagreed")
HOW_TO_CLEAR = (
    "Pay the creator directly and mark the amount paid, or ask the creator to withdraw their dispute. "
    "Disputes open for 14 days or more can be reviewed by Plantr. Your live collabs keep running "
    "and you can still edit or correct existing briefs."
)
PAID_VIA = {"plantr_tracked", "tiktok_shop", "shopify_collabs"}
PLATFORM_PAID = {"tiktok_shop", "shopify_collabs"}
CENT = Decimal("0.01")

BANK_PATTERNS = [
    re.compile(r"\bBSB\b", re.I),
    re.compile(r"\bIBAN\b", re.I),
    re.compile(r"\bSWIFT\b", re.I),
    re.compile(r"\bPAY\s*ID\b", re.I),
    re.compile(r"\bPAYPAL\b", re.I),
    re.compile(r"\bacc(?:oun)?t\.?\s*(?:no|num|number|#)", re.I),
    re.compile(r"\b\d{3}[-\s]?\d{3}\b.*\b\d{6,}\b"),
    re.compile(r"\d[\d\s-]{7,}\d"),
]


_queue = threading.local()


def _emits(fn):
    """Queue analytics events and send them only after the database work has committed."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        outer = getattr(_queue, "items", None)
        if outer is not None:
            return fn(*args, **kwargs)
        _queue.items = []
        try:
            result = fn(*args, **kwargs)
            pending = list(_queue.items)
        finally:
            _queue.items = None
        for key, shop_id, props in pending:
            _send(key, shop_id, props)
        return result

    return wrapper


# ---------------------------------------------------------------- settings

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def confirm_window_days() -> int:
    return _int_env("COMMISSION_CONFIRM_WINDOW_DAYS", 14)


def dispute_resolve_after_days() -> int:
    return _int_env("COMMISSION_DISPUTE_RESOLVE_AFTER_DAYS", 14)


def overdue_rule() -> str:
    """month_end (Ross, 16 Nov; Risk): overdue 30 days after the end of the month the sale was made.
    payment_date (Legal 4.9 alternative): 7 days after the agreed payment date, else 30 days after month end."""
    value = os.getenv("COMMISSION_OVERDUE_RULE", "month_end").strip().lower()
    return value if value in {"payment_date", "month_end"} else "month_end"


def overdue_days_after_month_end() -> int:
    return _int_env("COMMISSION_OVERDUE_DAYS", 30)


def overdue_grace_days() -> int:
    return _int_env("COMMISSION_OVERDUE_GRACE_DAYS", 7)


def exclude_platform_paid() -> bool:
    return os.getenv("COMMISSION_EXCLUDE_PLATFORM_PAID", "true").strip().lower() not in {"0", "false", "no", "off"}


def commission_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("COMMISSION_TIMEZONE", "Australia/Sydney").strip() or "Australia/Sydney")
    except Exception:
        return ZoneInfo("Australia/Sydney")


def pays_on_time_visibility() -> str:
    """Ross, 16 Nov: the seller and creators viewing that seller's briefs. Never public."""
    value = os.getenv("PAYS_ON_TIME_VISIBILITY", "seller_and_brief_creators").strip().lower()
    return value if value in {"seller", "seller_and_brief_creators"} else "seller_and_brief_creators"


def correction_window_days() -> int:
    return _int_env("COMMISSION_CORRECTION_WINDOW_DAYS", 14)


def terms_version() -> str:
    return os.getenv("COLLAB_TERMS_VERSION", "collab-terms-4.9-draft").strip() or "collab-terms-4.9-draft"


def require_db() -> None:
    if not db.configured():
        raise HTTPException(status_code=503, detail="Commission tracking needs the database")


def looks_like_bank_details(text: Optional[str]) -> bool:
    return bool(text) and any(p.search(text) for p in BANK_PATTERNS)


def _clean_text(value: Optional[str], *, field: str, max_len: int, required: bool = False, min_len: int = 0) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        if required:
            raise HTTPException(status_code=422, detail=f"{field} is required")
        return None
    if len(text) < min_len:
        raise HTTPException(status_code=422, detail=f"{field} is too short")
    if len(text) > max_len:
        raise HTTPException(status_code=422, detail=f"{field} must be {max_len} characters or fewer")
    if looks_like_bank_details(text):
        raise HTTPException(status_code=422, detail="Please don't enter bank or payment account details. Plantr doesn't store them.")
    return text


def _today(now: Optional[datetime] = None) -> date:
    return (now or utcnow()).astimezone(commission_tz()).date()


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- dates

def period_bounds(period: str) -> tuple[datetime, datetime, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period or ""):
        raise HTTPException(status_code=422, detail="period must be YYYY-MM")
    year, month = int(period[:4]), int(period[5:])
    tz = commission_tz()
    start = datetime(year, month, 1, tzinfo=tz)
    last = date(year, month, calendar.monthrange(year, month)[1])
    end = datetime(last.year, last.month, last.day, tzinfo=tz) + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), last


def overdue_on(period_end: date, payment_date: Optional[date]) -> date:
    if overdue_rule() == "payment_date" and payment_date is not None:
        return payment_date + timedelta(days=overdue_grace_days())
    return period_end + timedelta(days=overdue_days_after_month_end())


# ---------------------------------------------------------------- statements

def _agreement_for(session, shop_id: str, creator_id: str, product_id: Optional[str]) -> Optional[CollabAgreement]:
    rows = session.scalars(
        select(CollabAgreement)
        .where(
            CollabAgreement.shop_id == shop_id,
            CollabAgreement.creator_id == creator_id,
            CollabAgreement.status.in_(("active", "ended")),
            or_(CollabAgreement.product_id == product_id, CollabAgreement.product_id.is_(None)),
        )
        .order_by(CollabAgreement.creator_accepted_at.desc())
    ).all()
    exact = [row for row in rows if row.product_id == product_id]
    return (exact or rows or [None])[0]


@_emits
def generate_statements(period: str, *, shop_id: Optional[str] = None, actor: str = "system", now: Optional[datetime] = None) -> dict:
    """Create statements for a closed month. Idempotent: an existing statement is never rebuilt.

    Lines: sales in the month, plus adjustments for earlier orders whose net changed
    (refunds) or that settled late. Platform-paid collabs are skipped when configured.
    """
    require_db()
    start, end, last = period_bounds(period)
    if _today(now) <= last:
        raise HTTPException(status_code=409, detail="Statements are generated after the month ends")
    created, skipped_existing = [], 0
    with db.session_scope() as session:
        query = select(AttributedOrder).where(
            AttributedOrder.attributed.is_(True),
            AttributedOrder.creator_id.is_not(None),
            AttributedOrder.occurred_at < end,
        )
        if shop_id:
            query = query.where(AttributedOrder.shop_id == shop_id)
        groups: dict[tuple, list] = defaultdict(list)
        for order in session.scalars(query).all():
            net = _money(order.net_gmv) if order.settled else Decimal("0.00")
            delta = net - _money(order.statement_reported_net)
            occurred = order.occurred_at if order.occurred_at.tzinfo else order.occurred_at.replace(tzinfo=timezone.utc)
            in_period = start <= occurred < end
            if not in_period and delta == 0:
                continue
            if in_period and delta == 0 and net == 0:
                continue
            groups[(order.shop_id, order.creator_id, (order.currency or "AUD").upper())].append((order, delta, in_period))
        for (g_shop, g_creator, currency), items in groups.items():
            existing = session.scalar(
                select(CommissionStatement).where(
                    CommissionStatement.shop_id == g_shop,
                    CommissionStatement.creator_id == g_creator,
                    CommissionStatement.period == period,
                    CommissionStatement.currency == currency,
                )
            )
            if existing is not None:
                skipped_existing += 1
                continue
            statement = CommissionStatement(
                id=str(uuid.uuid4()), shop_id=g_shop, creator_id=g_creator, period=period, currency=currency,
                net_sales=0, amount_owed=0, orders_count=0, status="open", issued_at=utcnow(), overdue_on=last,
            )
            lines, total_net, total_owed, rate_missing, terms = [], Decimal("0.00"), Decimal("0.00"), False, []
            for order, delta, in_period in items:
                agreement = _agreement_for(session, g_shop, g_creator, order.product_id)
                if agreement is not None and agreement.paid_via in PLATFORM_PAID and exclude_platform_paid():
                    continue
                rate = None
                if agreement is not None:
                    rate = _money(agreement.commission_rate)
                    if agreement.payment_terms_days is not None:
                        terms.append(int(agreement.payment_terms_days))
                elif order.product_id:
                    product = session.get(Product, order.product_id)
                    if product is not None and product.commission_rate is not None:
                        rate = _money(product.commission_rate)
                commission = (delta * rate / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP) if rate is not None else Decimal("0.00")
                if rate is None:
                    rate_missing = True
                kind = "sale" if in_period and _money(order.statement_reported_net) == 0 else "adjustment"
                lines.append(
                    CommissionStatementLine(
                        id=str(uuid.uuid4()), statement_id=statement.id, order_id=order.id, kind=kind,
                        net_sales=float(delta), commission_rate=float(rate) if rate is not None else None,
                        commission=float(commission), collab_id=agreement.id if agreement is not None else None, created_at=utcnow(),
                    )
                )
                total_net += delta
                total_owed += commission
                order.statement_reported_net = float(_money(order.statement_reported_net) + delta)
            if not lines:
                continue
            payment_date = last + timedelta(days=min(terms)) if terms else None
            statement.net_sales = float(total_net)
            statement.amount_owed = float(total_owed)
            statement.orders_count = len(lines)
            statement.rate_missing = rate_missing
            statement.payment_date = payment_date
            statement.overdue_on = overdue_on(last, payment_date)
            session.add(statement)
            session.flush()
            for line in lines:
                session.add(line)
            _audit(session, statement, "admin" if actor != "system" else "system", actor, "owed_calculated",
                   details={"amount": str(total_owed), "net_sales": str(total_net), "lines": len(lines), "currency": currency})
            created.append(statement.id)
            _track("owed_calculated", statement, amount=float(total_owed))
    return {"period": period, "created": created, "skipped_existing": skipped_existing}


def display_status(st: CommissionStatement, now: Optional[datetime] = None) -> dict:
    now = now or utcnow()
    today = _today(now)
    if st.status == "open":
        if _money(st.amount_owed) <= 0:
            return {"code": "nothing_owed", "label": "Nothing owed"}
        if today > st.overdue_on:
            return {"code": "overdue", "label": f"Overdue since {st.overdue_on + timedelta(days=1)}"}
        return {"code": "open", "label": "Not yet marked paid"}
    if st.status == "marked_paid":
        if st.marked_paid_at and now > st.marked_paid_at + timedelta(days=confirm_window_days()):
            return {"code": "unconfirmed", "label": UNCONFIRMED_LABEL}
        return {"code": "awaiting_confirmation", "label": "Marked paid by Seller, waiting for the creator to confirm"}
    if st.status == "confirmed":
        return {"code": "confirmed", "label": "Payment confirmed by creator"}
    if st.status == "disputed":
        return {"code": "disputed", "label": "Disputed by creator"}
    return {"code": "resolved", "label": "Dispute closed by Plantr review"}


def _statement_dict(session, st: CommissionStatement, *, viewer: str, include_lines: bool = False, now=None) -> dict:
    creator = session.get(Creator, st.creator_id)
    shop = session.get(Shop, st.shop_id)
    out = {
        "id": st.id,
        "shop_id": st.shop_id,
        "shop_name": getattr(shop, "name", None),
        "creator_id": st.creator_id,
        "creator_handle": getattr(creator, "handle", None),
        "period": st.period,
        "currency": st.currency,
        "net_sales": float(st.net_sales),
        "amount_owed": float(st.amount_owed),
        "orders_count": st.orders_count,
        "rate_missing": st.rate_missing,
        "status": st.status,
        "display": display_status(st, now),
        "payment_date": st.payment_date,
        "overdue_on": st.overdue_on,
        "marked_paid_on": st.marked_paid_on,
        "paid_reference": st.paid_reference,
        "confirmed_at": st.confirmed_at,
        "disputed_at": st.disputed_at,
        "dispute_reason": st.dispute_reason,
        "dispute_amount": float(st.dispute_amount) if st.dispute_amount is not None else None,
        "resolved_at": st.resolved_at,
        "resolution_reason": st.resolution_reason,
        "label": ESTIMATE_LABEL,
    }
    if st.id in _under_review_ids(session, statement_id=st.id):
        out["display"] = {"code": "under_review", "label": UNDER_REVIEW_LABEL}
    if viewer == "seller" and creator is not None and not creator.abn:
        acked = session.scalar(
            select(AbnWarningAck.id).where(
                AbnWarningAck.shop_id == st.shop_id, AbnWarningAck.creator_id == st.creator_id,
                AbnWarningAck.context == "statement", AbnWarningAck.reference_id == st.id,
            )
        ) is not None
        out["abn_warning"] = {"text": ABN_WARNING, "version": ABN_WARNING_VERSION, "acknowledged": acked}
    if include_lines:
        lines = session.scalars(select(CommissionStatementLine).where(CommissionStatementLine.statement_id == st.id)).all()
        rows = []
        for line in lines:
            order = session.get(AttributedOrder, line.order_id)
            rows.append({
                "kind": line.kind,
                "order": (order.raw_payload or {}).get("name") or order.external_order_id if order else None,
                "order_date": order.attribution_date if order else None,
                "method": order.attribution_method if order else None,
                "source_platform": (getattr(order, "source_platform", None) or "unknown") if order else "unknown",
                "channel_kind": getattr(order, "channel_kind", None) if order else None,
                "net_sales": float(line.net_sales),
                "commission_rate": float(line.commission_rate) if line.commission_rate is not None else None,
                "commission": float(line.commission),
            })
        out["lines"] = rows
        out["corrections"] = [
            _correction_dict(c)
            for c in session.scalars(
                select(CommissionCorrection).where(CommissionCorrection.statement_id == st.id).order_by(CommissionCorrection.requested_at)
            ).all()
        ]
        out["log"] = audit_log(statement_id=st.id, _session=session)
    return out


def list_statements(*, shop_id: Optional[str] = None, creator_id: Optional[str] = None, viewer: str) -> list[dict]:
    require_db()
    with db.session_scope() as session:
        _settle_expired(session, None)
        session.flush()
        query = select(CommissionStatement).order_by(CommissionStatement.period.desc())
        if shop_id:
            query = query.where(CommissionStatement.shop_id == shop_id)
        if creator_id:
            query = query.where(CommissionStatement.creator_id == creator_id)
        return [_statement_dict(session, st, viewer=viewer) for st in session.scalars(query).all()]


def statement_detail(statement_id: str, *, viewer: str, shop_id: Optional[str] = None, creator_id: Optional[str] = None) -> dict:
    require_db()
    with db.session_scope() as session:
        _settle_expired(session, None)
        session.flush()
        st = _owned(session, statement_id, shop_id=shop_id, creator_id=creator_id)
        return _statement_dict(session, st, viewer=viewer, include_lines=True)


def statement_csv(statement_id: str, *, shop_id: str) -> str:
    """Order lines plus sales and amounts by source platform. The split does not change the amount owed."""
    detail = statement_detail(statement_id, viewer="seller", shop_id=shop_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["note", "Reporting only. Splitting by source platform does not change the amount owed."])
    writer.writerow(
        ["section", "creator", "period", "currency", "source_platform", "channel_kind", "order", "order_date", "kind", "orders", "net_sales", "commission_rate", "commission"]
    )
    grouped: dict[str, dict] = defaultdict(lambda: {"orders": 0, "net_sales": Decimal("0.00"), "commission": Decimal("0.00")})
    for line in detail.get("lines") or []:
        platform = line.get("source_platform") or "unknown"
        writer.writerow([
            "line", detail.get("creator_handle") or detail["creator_id"], detail["period"], detail["currency"],
            platform, line.get("channel_kind") or "", line.get("order") or "", line.get("order_date") or "", line["kind"], "",
            line["net_sales"], "" if line["commission_rate"] is None else line["commission_rate"], line["commission"],
        ])
        bucket = grouped[platform]
        bucket["orders"] += 1
        bucket["net_sales"] += _money(line["net_sales"])
        bucket["commission"] += _money(line["commission"])
    for platform in sorted(grouped):
        bucket = grouped[platform]
        writer.writerow([
            "summary", detail.get("creator_handle") or detail["creator_id"], detail["period"], detail["currency"],
            platform, "", "", "", "", bucket["orders"], float(bucket["net_sales"]), "", float(bucket["commission"]),
        ])
    writer.writerow([
        "total", detail.get("creator_handle") or detail["creator_id"], detail["period"], detail["currency"],
        "", "", "", "", "", detail["orders_count"], detail["net_sales"], "", detail["amount_owed"],
    ])
    return buffer.getvalue()


def source_report(shop_id: str, period: str) -> dict:
    """Sales and estimated amounts by creator and source platform for one month.

    Reads the same settled, attributed orders a statement would. It does not write a statement
    or change amounts owed. Platform-paid collabs contribute sales with an amount of 0 when excluded.
    """
    require_db()
    start, end, _last = period_bounds(period)
    with db.session_scope() as session:
        orders = session.scalars(
            select(AttributedOrder).where(
                AttributedOrder.shop_id == shop_id,
                AttributedOrder.attributed.is_(True),
                AttributedOrder.creator_id.is_not(None),
                AttributedOrder.settled.is_(True),
            )
        ).all()
        grouped: dict[tuple, dict] = {}
        for order in orders:
            occurred = order.occurred_at if order.occurred_at.tzinfo else order.occurred_at.replace(tzinfo=timezone.utc)
            if not (start <= occurred < end):
                continue
            net = _money(order.net_gmv)
            if net == 0:
                continue
            platform = order.source_platform or "unknown"
            currency = (order.currency or "AUD").upper()
            key = (order.creator_id, platform, currency)
            bucket = grouped.get(key)
            if bucket is None:
                creator = session.get(Creator, order.creator_id)
                bucket = {
                    "creator_id": order.creator_id,
                    "creator_handle": getattr(creator, "handle", None),
                    "source_platform": platform,
                    "currency": currency,
                    "orders": 0,
                    "net_sales": Decimal("0.00"),
                    "amount": Decimal("0.00"),
                }
                grouped[key] = bucket
            amount, excluded = _estimated_commission(session, order, net)
            bucket["orders"] += 1
            bucket["net_sales"] += net
            bucket["amount"] += amount
            if excluded:
                bucket["excluded_from_owed"] = True
        rows = []
        for bucket in grouped.values():
            rows.append({
                **bucket,
                "net_sales": float(bucket["net_sales"]),
                "amount": float(bucket["amount"]),
            })
        rows.sort(key=lambda row: (row.get("creator_handle") or "", row["source_platform"], row["currency"]))
    return {
        "period": period,
        "shop_id": shop_id,
        "note": "Reporting only. Splitting by source platform does not change the amount owed.",
        "rows": rows,
        "totals": {
            "orders": sum(row["orders"] for row in rows),
            "net_sales": round(sum(row["net_sales"] for row in rows), 2),
            "amount": round(sum(row["amount"] for row in rows), 2),
        },
    }


def _estimated_commission(session, order: AttributedOrder, net: Decimal) -> tuple[Decimal, bool]:
    agreement = _agreement_for(session, order.shop_id, order.creator_id, order.product_id)
    if agreement is not None and agreement.paid_via in PLATFORM_PAID and exclude_platform_paid():
        return Decimal("0.00"), True
    rate = None
    if agreement is not None:
        rate = _money(agreement.commission_rate)
    elif order.product_id:
        product = session.get(Product, order.product_id)
        if product is not None and product.commission_rate is not None:
            rate = _money(product.commission_rate)
    if rate is None:
        return Decimal("0.00"), False
    return (net * rate / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP), False


def _owned(session, statement_id: str, *, shop_id=None, creator_id=None) -> CommissionStatement:
    st = session.get(CommissionStatement, statement_id)
    if st is None or (shop_id and st.shop_id != shop_id) or (creator_id and st.creator_id != creator_id):
        raise HTTPException(status_code=404, detail="Statement not found")
    return st


# ---------------------------------------------------------------- actions

@_emits
def mark_paid(shop_id: str, statement_id: str, *, paid_on: str, reference: Optional[str], actor: str) -> dict:
    require_db()
    ref = _clean_text(reference, field="Reference", max_len=120)
    try:
        paid_date = date.fromisoformat(str(paid_on or "").strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="paid_on must be YYYY-MM-DD") from None
    if paid_date > _today():
        raise HTTPException(status_code=422, detail="The payment date can't be in the future")
    with db.session_scope() as session:
        st = _owned(session, statement_id, shop_id=shop_id)
        if st.status != "open":
            raise HTTPException(status_code=409, detail="This amount is not waiting to be marked paid")
        before = _reasons(session, shop_id)
        st.status = "marked_paid"
        st.marked_paid_on = paid_date
        st.marked_paid_at = utcnow()
        st.paid_reference = ref
        _audit(session, st, "seller", actor, "owed_marked_paid", details={"paid_on": paid_date.isoformat(), "reference": ref})
        session.flush()
        _maybe_unblocked(session, shop_id, before)
        _track("owed_marked_paid", st, amount=float(st.amount_owed))
        return _statement_dict(session, st, viewer="seller")


@_emits
def confirm(creator_id: str, statement_id: str, *, actor: str) -> dict:
    require_db()
    with db.session_scope() as session:
        st = _owned(session, statement_id, creator_id=creator_id)
        if st.status not in {"open", "marked_paid"}:
            raise HTTPException(status_code=409, detail="This amount can't be confirmed now")
        before = _reasons(session, st.shop_id)
        st.status = "confirmed"
        st.confirmed_at = utcnow()
        _audit(session, st, "creator", actor, "owed_confirmed")
        session.flush()
        _maybe_unblocked(session, st.shop_id, before)
        _track("owed_confirmed", st, amount=float(st.amount_owed))
        return _statement_dict(session, st, viewer="creator")


@_emits
def dispute(creator_id: str, statement_id: str, *, reason: str, amount: Any, actor: str) -> dict:
    require_db()
    text = _clean_text(reason, field="Reason", max_len=1000, required=True, min_len=5)
    try:
        disputed_amount = _money(amount)
    except Exception:
        raise HTTPException(status_code=422, detail="amount must be a number") from None
    if amount in (None, "") or disputed_amount < 0:
        raise HTTPException(status_code=422, detail="Enter the amount you believe is owed")
    with db.session_scope() as session:
        st = _owned(session, statement_id, creator_id=creator_id)
        if st.status not in {"open", "marked_paid"}:
            raise HTTPException(status_code=409, detail="This amount can't be disputed now")
        if st.status == "marked_paid" and st.marked_paid_at and utcnow() > st.marked_paid_at + timedelta(days=confirm_window_days()):
            raise HTTPException(status_code=409, detail="The confirm window has closed. Contact Plantr support if something is wrong.")
        was_blocked = bool(_reasons(session, st.shop_id))
        st.status = "disputed"
        st.disputed_at = utcnow()
        st.dispute_reason = text
        st.dispute_amount = float(disputed_amount)
        _audit(session, st, "creator", actor, "owed_disputed", reason=text, details={"amount": str(disputed_amount)})
        session.flush()
        _track("owed_disputed", st, amount=float(st.amount_owed))
        if not was_blocked and _reasons(session, st.shop_id):
            _track("seller_blocked", st, reason="disputed")
        return _statement_dict(session, st, viewer="creator")


@_emits
def withdraw_dispute(creator_id: str, statement_id: str, *, actor: str) -> dict:
    require_db()
    with db.session_scope() as session:
        st = _owned(session, statement_id, creator_id=creator_id)
        if st.status != "disputed":
            raise HTTPException(status_code=409, detail="There is no open dispute on this amount")
        before = _reasons(session, st.shop_id)
        st.status = "marked_paid" if st.marked_paid_at else "open"
        _audit(session, st, "creator", actor, "owed_dispute_withdrawn")
        session.flush()
        _maybe_unblocked(session, st.shop_id, before)
        _track("owed_dispute_withdrawn", st)
        return _statement_dict(session, st, viewer="creator")


@_emits
def resolve_dispute(statement_id: str, *, reason: str, outcome: str, admin: str, now: Optional[datetime] = None) -> dict:
    """Plantr reviews both sides and clears or keeps the block. It never decides the amount."""
    require_db()
    text = _clean_text(reason, field="Reason", max_len=2000, required=True, min_len=10)
    if outcome not in {"clear_block", "keep_block"}:
        raise HTTPException(status_code=422, detail="outcome must be clear_block or keep_block")
    now = now or utcnow()
    with db.session_scope() as session:
        st = _owned(session, statement_id)
        if st.status != "disputed":
            raise HTTPException(status_code=409, detail="There is no open dispute on this amount")
        ready = st.disputed_at + timedelta(days=dispute_resolve_after_days())
        if now < ready:
            raise HTTPException(status_code=409, detail=f"Plantr can review this dispute from {ready.date().isoformat()}")
        before = _reasons(session, st.shop_id)
        if outcome == "clear_block":
            st.status = "resolved"
            st.resolved_at = now
            st.resolved_by = admin
            st.resolution_reason = text
        _audit(session, st, "admin", admin, "owed_dispute_resolved" if outcome == "clear_block" else "owed_dispute_kept",
               reason=text, details={"outcome": outcome})
        session.flush()
        _maybe_unblocked(session, st.shop_id, before)
        _track("owed_dispute_resolved", st, outcome=outcome)
        return _statement_dict(session, st, viewer="admin")


@_emits
def lift_block(shop_id: str, *, reason: str, admin: str) -> dict:
    require_db()
    text = _clean_text(reason, field="Reason", max_len=2000, required=True, min_len=10)
    with db.session_scope() as session:
        reasons = _reasons(session, shop_id)
        if not reasons:
            raise HTTPException(status_code=409, detail="This seller isn't blocked")
        ids = [r["statement_id"] for r in reasons]
        session.add(CollabBlockLift(id=str(uuid.uuid4()), shop_id=shop_id, statement_ids=ids, reason=text, lifted_by=admin, created_at=utcnow()))
        session.add(CommissionAuditLog(id=str(uuid.uuid4()), shop_id=shop_id, statement_id=None, actor_role="admin", actor_id=admin,
                                       action="block_lifted", reason=text, details={"statement_ids": ids}, created_at=utcnow()))
        session.flush()
        _track_shop("seller_unblocked", shop_id, reason="admin_lift")
    return collab_block(shop_id)


@_emits
def overdue_sweep(now: Optional[datetime] = None) -> dict:
    """Record overdue amounts once (event + log). Run daily. Sends no email yet."""
    require_db()
    today = _today(now)
    flagged = []
    with db.session_scope() as session:
        rows = session.scalars(
            select(CommissionStatement).where(CommissionStatement.status == "open", CommissionStatement.overdue_notified_at.is_(None))
        ).all()
        for st in rows:
            if _money(st.amount_owed) > 0 and today > st.overdue_on:
                was_blocked = bool(_reasons(session, st.shop_id, exclude={st.id}))
                st.overdue_notified_at = utcnow()
                _audit(session, st, "system", "system", "owed_overdue")
                _track("owed_overdue", st, amount=float(st.amount_owed))
                if not was_blocked:
                    _track("seller_blocked", st, reason="overdue")
                flagged.append(st.id)
    return {"overdue": flagged}


# ---------------------------------------------------------------- block

def _reasons(session, shop_id: str, *, now: Optional[datetime] = None, exclude: Optional[set] = None) -> list[dict]:
    today = _today(now)
    lifted: set = set()
    for lift in session.scalars(select(CollabBlockLift).where(CollabBlockLift.shop_id == shop_id)).all():
        lifted.update(lift.statement_ids or [])
    out = []
    rows = session.scalars(
        select(CommissionStatement).where(CommissionStatement.shop_id == shop_id, CommissionStatement.status.in_(("open", "disputed")))
    ).all()
    for st in rows:
        if st.id in lifted or (exclude and st.id in exclude) or _money(st.amount_owed) <= 0:
            continue
        if st.status == "disputed":
            out.append({"statement_id": st.id, "creator_id": st.creator_id, "period": st.period, "amount": float(st.amount_owed),
                        "currency": st.currency, "kind": "disputed", "since": st.disputed_at.date().isoformat() if st.disputed_at else None})
        elif today > st.overdue_on:
            out.append({"statement_id": st.id, "creator_id": st.creator_id, "period": st.period, "amount": float(st.amount_owed),
                        "currency": st.currency, "kind": "overdue", "since": (st.overdue_on + timedelta(days=1)).isoformat()})
    return out


def collab_block(shop_id: str, now: Optional[datetime] = None) -> dict:
    if not db.configured():
        return {"blocked": False, "reasons": []}
    with db.session_scope() as session:
        reasons = _reasons(session, shop_id, now=now)
    return {"blocked": bool(reasons), "reasons": reasons, "how_to_clear": HOW_TO_CLEAR if reasons else None}


def ensure_not_blocked(shop_id: Optional[str]) -> None:
    """Call before creating a NEW collab or brief. Never call on edits or live collabs."""
    if not shop_id:
        return
    state = collab_block(shop_id)
    if state["blocked"]:
        raise HTTPException(
            status_code=409,
            detail={"message": "New collabs are paused until an owed commission is sorted out.", **state},
        )


def _maybe_unblocked(session, shop_id: str, before: list) -> None:
    if before and not _reasons(session, shop_id):
        _track_shop("seller_unblocked", shop_id, reason="cleared")


# ---------------------------------------------------------------- pays-on-time record

def pays_on_time(shop_id: str, now: Optional[datetime] = None) -> dict:
    require_db()
    today = _today(now)
    with db.session_scope() as session:
        _settle_expired(session, now)
        session.flush()
        under_review = _under_review_ids(session, shop_id=shop_id)
        rows = session.scalars(select(CommissionStatement).where(CommissionStatement.shop_id == shop_id)).all()
        counted = []
        for st in rows:
            if _money(st.amount_owed) <= 0 or st.status == "disputed" or st.id in under_review:
                continue
            if st.marked_paid_on is None and today <= st.overdue_on:
                continue
            counted.append(st)
        on_time = sum(1 for st in counted if st.marked_paid_on is not None and st.marked_paid_on <= st.overdue_on)
        periods = sorted(st.period for st in counted)
        response = session.get(SellerPaymentResponse, shop_id)
        if counted:
            text = f"{on_time} of {len(counted)} paid on time"
        else:
            text = "No payments have fallen due yet"
        return {
            "text": text,
            "on_time": on_time,
            "total": len(counted),
            "under_review": len([i for i in under_review if i in {st.id for st in rows}]),
            "from_period": periods[0] if periods else None,
            "to_period": periods[-1] if periods else None,
            "how_calculated": (
                "Counts owed amounts that have fallen due. On time means the seller marked it paid on or before "
                "the date it would become overdue. Disputed amounts, and entries under review after a seller "
                "correction request, are left out until they are settled. "
                "Based on dates entered by sellers and creators."
            ),
            "seller_response": response.text if response is not None else None,
        }


def set_response(shop_id: str, text: Optional[str], *, actor: str) -> dict:
    require_db()
    clean = _clean_text(text, field="Response", max_len=280)
    with db.session_scope() as session:
        row = session.get(SellerPaymentResponse, shop_id)
        if clean is None:
            if row is not None:
                session.delete(row)
        elif row is None:
            session.add(SellerPaymentResponse(shop_id=shop_id, text=clean, updated_at=utcnow()))
        else:
            row.text = clean
            row.updated_at = utcnow()
        session.add(CommissionAuditLog(id=str(uuid.uuid4()), shop_id=shop_id, actor_role="seller", actor_id=actor,
                                       action="pays_on_time_response", reason=clean, created_at=utcnow()))
    return pays_on_time(shop_id)


def creator_can_view_record(creator_id: str, shop_id: str) -> bool:
    if pays_on_time_visibility() != "seller_and_brief_creators":
        return False
    with db.session_scope() as session:
        if session.scalar(select(CollabAgreement.id).where(CollabAgreement.shop_id == shop_id, CollabAgreement.creator_id == creator_id)):
            return True
        if session.scalar(select(Brief.id).where(Brief.shop_id == shop_id, Brief.creator_id == creator_id)):
            return True
        return session.scalar(
            select(ShortlistItem.id)
            .join(Shortlist, Shortlist.id == ShortlistItem.shortlist_id)
            .where(Shortlist.shop_id == shop_id, ShortlistItem.creator_id == creator_id, ShortlistItem.accepted_at.is_not(None))
        ) is not None


# ---------------------------------------------------------------- seller corrections

def _under_review_ids(session, *, shop_id: Optional[str] = None, statement_id: Optional[str] = None) -> set:
    query = select(CommissionCorrection.statement_id).where(CommissionCorrection.status.in_(OPEN_CORRECTION))
    if shop_id:
        query = query.where(CommissionCorrection.shop_id == shop_id)
    if statement_id:
        query = query.where(CommissionCorrection.statement_id == statement_id)
    return set(session.scalars(query).all())


def _correction_dict(c: CommissionCorrection) -> dict:
    return {
        "id": c.id, "statement_id": c.statement_id, "entry_kind": c.entry_kind, "status": c.status, "reason": c.reason,
        "evidence_text": c.evidence_text, "evidence_url": c.evidence_url, "proposed_paid_on": c.proposed_paid_on,
        "original_status": c.original_status, "original_paid_on": c.original_paid_on, "requested_at": c.requested_at,
        "respond_by": c.respond_by, "creator_response_at": c.creator_response_at, "creator_reason": c.creator_reason,
        "decided_at": c.decided_at, "decision_reason": c.decision_reason,
    }


def _entry_kind(st: CommissionStatement, today: date) -> Optional[str]:
    if _money(st.amount_owed) <= 0 or st.status == "disputed":
        return None
    if st.marked_paid_on is not None and st.marked_paid_on > st.overdue_on:
        return "late"
    if st.marked_paid_on is None and st.status == "open" and today > st.overdue_on:
        return "missed"
    return None


def _clean_url(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > 500 or not re.fullmatch(r"https?://[^\s]+", text):
        raise HTTPException(status_code=422, detail="Evidence link must be an http(s) URL")
    if looks_like_bank_details(text):
        raise HTTPException(status_code=422, detail="Please don't enter bank or payment account details. Plantr doesn't store them.")
    return text


def request_correction(shop_id: str, statement_id: str, *, reason: str, proposed_paid_on: str, evidence_text: Optional[str] = None,
                       evidence_url: Optional[str] = None, actor: str, now: Optional[datetime] = None) -> dict:
    """The seller asks to correct a late or missed entry. It shows 'Under review' and leaves the record until settled."""
    require_db()
    now = now or utcnow()
    today = _today(now)
    why = _clean_text(reason, field="Reason", max_len=1000, required=True, min_len=10)
    evidence = _clean_text(evidence_text, field="Evidence", max_len=1000)
    link = _clean_url(evidence_url)
    try:
        paid = date.fromisoformat(str(proposed_paid_on or "").strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="proposed_paid_on must be YYYY-MM-DD") from None
    if paid > today:
        raise HTTPException(status_code=422, detail="The payment date can't be in the future")
    with db.session_scope() as session:
        _settle_expired(session, now)
        session.flush()
        st = _owned(session, statement_id, shop_id=shop_id)
        kind = _entry_kind(st, today)
        if kind is None:
            raise HTTPException(status_code=409, detail="Only a late or missed entry in your record can be corrected")
        if _under_review_ids(session, statement_id=st.id):
            raise HTTPException(status_code=409, detail="A correction for this entry is already under review")
        if paid > st.overdue_on:
            raise HTTPException(status_code=422, detail="That date would still be after the due date, so the entry wouldn't change")
        corr = CommissionCorrection(
            id=str(uuid.uuid4()), statement_id=st.id, shop_id=shop_id, creator_id=st.creator_id, entry_kind=kind,
            status="pending_creator", reason=why, evidence_text=evidence, evidence_url=link, proposed_paid_on=paid,
            original_status=st.status, original_paid_on=st.marked_paid_on, requested_by=actor, requested_at=now,
            respond_by=now + timedelta(days=correction_window_days()),
        )
        session.add(corr)
        _audit(session, st, "seller", actor, "correction_requested", reason=why,
               details={"correction_id": corr.id, "entry_kind": kind, "proposed_paid_on": paid.isoformat(),
                        "evidence_text": evidence, "evidence_url": link})
        session.flush()
        return _correction_dict(corr)


def respond_to_correction(creator_id: str, correction_id: str, *, agree: bool, reason: Optional[str], actor: str,
                          now: Optional[datetime] = None) -> dict:
    require_db()
    now = now or utcnow()
    with db.session_scope() as session:
        _settle_expired(session, now)
        session.flush()
        corr = session.get(CommissionCorrection, correction_id)
        if corr is None or corr.creator_id != creator_id:
            raise HTTPException(status_code=404, detail="Correction not found")
        if corr.status != "pending_creator":
            raise HTTPException(status_code=409, detail="This correction is no longer waiting for your answer")
        st = session.get(CommissionStatement, corr.statement_id)
        corr.creator_response_at = now
        if agree:
            corr.creator_reason = _clean_text(reason, field="Reason", max_len=1000)
            corr.status = "agreed"
            _apply_correction(st, corr, confirmed=True)
            _audit(session, st, "creator", actor, "correction_agreed", reason=corr.creator_reason,
                   details={"correction_id": corr.id, "marked_paid_on": corr.proposed_paid_on.isoformat()})
        else:
            corr.creator_reason = _clean_text(reason, field="Reason", max_len=1000, required=True, min_len=5)
            corr.status = "disagreed"
            _audit(session, st, "creator", actor, "correction_disagreed", reason=corr.creator_reason,
                   details={"correction_id": corr.id})
        session.flush()
        return _correction_dict(corr)


def decide_correction(correction_id: str, *, outcome: str, reason: str, admin: str, now: Optional[datetime] = None) -> dict:
    """After a creator disagrees, a Plantr admin corrects or keeps the entry. The reason is logged."""
    require_db()
    now = now or utcnow()
    why = _clean_text(reason, field="Reason", max_len=2000, required=True, min_len=10)
    if outcome not in {"correct", "keep"}:
        raise HTTPException(status_code=422, detail="outcome must be correct or keep")
    with db.session_scope() as session:
        corr = session.get(CommissionCorrection, correction_id)
        if corr is None:
            raise HTTPException(status_code=404, detail="Correction not found")
        if corr.status != "disagreed":
            raise HTTPException(status_code=409, detail="Only a correction the creator disagreed with goes to Plantr")
        st = session.get(CommissionStatement, corr.statement_id)
        corr.status = "admin_corrected" if outcome == "correct" else "admin_kept"
        corr.decided_at = now
        corr.decided_by = admin
        corr.decision_reason = why
        if outcome == "correct":
            _apply_correction(st, corr, confirmed=False)
        _audit(session, st, "admin", admin, "correction_" + ("corrected" if outcome == "correct" else "kept"), reason=why,
               details={"correction_id": corr.id, "marked_paid_on": st.marked_paid_on.isoformat() if st.marked_paid_on else None})
        session.flush()
        return _correction_dict(corr)


def settle_expired_corrections(now: Optional[datetime] = None) -> dict:
    """Silence counts as agreement: apply corrections the creator didn't answer in time."""
    require_db()
    with db.session_scope() as session:
        settled = _settle_expired(session, now)
    return {"deemed_agreed": settled}


def list_corrections(*, shop_id: Optional[str] = None, creator_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    require_db()
    with db.session_scope() as session:
        _settle_expired(session, None)
        session.flush()
        query = select(CommissionCorrection).order_by(CommissionCorrection.requested_at.desc())
        if shop_id:
            query = query.where(CommissionCorrection.shop_id == shop_id)
        if creator_id:
            query = query.where(CommissionCorrection.creator_id == creator_id)
        if status:
            query = query.where(CommissionCorrection.status == status)
        return [_correction_dict(c) for c in session.scalars(query).all()]


def _settle_expired(session, now: Optional[datetime]) -> list[str]:
    now = now or utcnow()
    done = []
    rows = session.scalars(
        select(CommissionCorrection).where(CommissionCorrection.status == "pending_creator", CommissionCorrection.respond_by <= now)
    ).all()
    for corr in rows:
        st = session.get(CommissionStatement, corr.statement_id)
        corr.status = "deemed_agreed"
        corr.decided_at = now
        _apply_correction(st, corr, confirmed=False)
        _audit(session, st, "system", "system", "correction_deemed_agreed",
               reason=f"No answer from the creator within {correction_window_days()} days",
               details={"correction_id": corr.id, "marked_paid_on": corr.proposed_paid_on.isoformat()})
        done.append(corr.id)
    return done


def _apply_correction(st: CommissionStatement, corr: CommissionCorrection, *, confirmed: bool) -> None:
    st.marked_paid_on = corr.proposed_paid_on
    if st.status == "open":
        st.status = "confirmed" if confirmed else "marked_paid"
        st.marked_paid_at = st.marked_paid_at or corr.requested_at
        if confirmed:
            st.confirmed_at = utcnow()


# ---------------------------------------------------------------- ABN

def creator_abn_status(creator_id: str) -> dict:
    with db.session_scope() as session:
        creator = session.get(Creator, creator_id)
        if creator is None:
            raise HTTPException(status_code=404, detail="Creator not found")
        return {"has_abn": bool(creator.abn)}


def set_creator_abn(creator_id: str, raw: Optional[str]) -> dict:
    """ABN is optional for creators. Blank clears it."""
    require_db()
    import legal

    try:
        clean = legal.clean_abn(raw or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    with db.session_scope() as session:
        creator = session.get(Creator, creator_id)
        if creator is None:
            raise HTTPException(status_code=404, detail="Creator not found")
        creator.abn = clean
    return {"has_abn": clean is not None}


def abn_warning_for(session, creator_id: str) -> Optional[dict]:
    creator = session.get(Creator, creator_id)
    if creator is None or creator.abn:
        return None
    return {"text": ABN_WARNING, "version": ABN_WARNING_VERSION}


def acknowledge_abn_warning(shop_id: str, creator_id: str, *, context: str, reference_id: Optional[str], actor: str, ip: Optional[str]) -> dict:
    require_db()
    if context not in {"collab_view", "collab_offer", "statement"}:
        raise HTTPException(status_code=422, detail="Unknown context")
    with db.session_scope() as session:
        if abn_warning_for(session, creator_id) is None:
            return {"acknowledged": False, "needed": False}
        if context == "statement":
            st = session.get(CommissionStatement, reference_id or "")
            if st is None or st.shop_id != shop_id or st.creator_id != creator_id:
                raise HTTPException(status_code=404, detail="Statement not found")
        if context == "collab_view":
            ag = session.get(CollabAgreement, reference_id or "")
            if ag is None or ag.shop_id != shop_id or ag.creator_id != creator_id:
                raise HTTPException(status_code=404, detail="Collab not found")
        _record_ack(session, shop_id, creator_id, context, reference_id, actor, ip)
    return {"acknowledged": True, "needed": True}


def _record_ack(session, shop_id, creator_id, context, reference_id, actor, ip) -> None:
    session.add(AbnWarningAck(
        id=str(uuid.uuid4()), shop_id=shop_id, creator_id=creator_id, context=context, reference_id=reference_id,
        warning_version=ABN_WARNING_VERSION, warning_text=ABN_WARNING, acknowledged_by=actor, ip=(ip or None), acknowledged_at=utcnow(),
    ))


# ---------------------------------------------------------------- collab agreements

def render_terms(*, shop_name: str, creator_handle: str, product_title: Optional[str], rate: Decimal,
                 payment_terms_days: Optional[int], paid_via: str) -> str:
    when = (f"within {payment_terms_days} days after the end of each month" if payment_terms_days is not None
            else "by the date shown on each monthly statement")
    channel = {
        "plantr_tracked": "Plantr Collab tracked links and codes",
        "tiktok_shop": "TikTok Shop (paid by TikTok Shop, not tracked for commission here)",
        "shopify_collabs": "Shopify Collabs (paid by Shopify Collabs, not tracked for commission here)",
    }[paid_via]
    return (
        f"Collab between {shop_name} (Seller) and {creator_handle} (Creator)"
        + (f" for {product_title}" if product_title else "")
        + f". Commission: {rate}% of net sales (excluding shipping and GST, less refunds) from {channel}. "
        f"The Seller pays the Creator directly {when}. Plantr Collab does not hold, pay or collect commission. "
        "Plantr shows an estimate based on tracked sales; this agreement governs the amount payable. "
        "The Creator must disclose the paid partnership in every post. "
        "Plantr Collab Terms 4.9 apply. [Draft wording pending Legal v0.4.9 sign-off.]"
    )


def create_offer(shop_id: str, *, actor: str, ip: Optional[str], creator_id: str, product_id: Optional[str],
                 shortlist_item_id: Optional[str], commission_rate: Any, payment_terms_days: Optional[int],
                 paid_via: str = "plantr_tracked", abn_warning_acknowledged: bool = False) -> dict:
    require_db()
    ensure_not_blocked(shop_id)
    if paid_via not in PAID_VIA:
        raise HTTPException(status_code=422, detail="paid_via must be plantr_tracked, tiktok_shop or shopify_collabs")
    try:
        rate = _money(commission_rate)
    except Exception:
        raise HTTPException(status_code=422, detail="commission_rate must be a number") from None
    if rate < 0 or rate > 100:
        raise HTTPException(status_code=422, detail="commission_rate must be between 0 and 100")
    if payment_terms_days is not None and not (0 <= int(payment_terms_days) <= 60):
        raise HTTPException(status_code=422, detail="payment_terms_days must be between 0 and 60")
    with db.session_scope() as session:
        shop = session.get(Shop, shop_id)
        creator = session.get(Creator, creator_id)
        if shop is None or creator is None:
            raise HTTPException(status_code=404, detail="Creator not found")
        product = session.get(Product, product_id) if product_id else None
        if product_id and (product is None or product.shop_id != shop_id):
            raise HTTPException(status_code=404, detail="Product not found")
        if shortlist_item_id:
            item = session.get(ShortlistItem, shortlist_item_id)
            shortlist = session.get(Shortlist, item.shortlist_id) if item is not None else None
            if item is None or item.creator_id != creator_id or shortlist is None or shortlist.shop_id != shop_id:
                raise HTTPException(status_code=404, detail="Shortlist item not found")
        warning = abn_warning_for(session, creator_id)
        if warning is not None and not abn_warning_acknowledged:
            raise HTTPException(status_code=422, detail={"message": "Please read and acknowledge the ABN warning.", "abn_warning": warning})
        text = render_terms(shop_name=shop.name, creator_handle=creator.handle, product_title=getattr(product, "title", None),
                            rate=rate, payment_terms_days=payment_terms_days, paid_via=paid_via)
        now = utcnow()
        agreement = CollabAgreement(
            id=str(uuid.uuid4()), shop_id=shop_id, creator_id=creator_id, product_id=product_id, shortlist_item_id=shortlist_item_id,
            commission_rate=float(rate), payment_terms_days=payment_terms_days, paid_via=paid_via, status="offered",
            terms_version=terms_version(), terms_text=text, terms_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            seller_accepted_at=now, seller_accepted_by=actor, seller_ip=ip, created_at=now,
        )
        session.add(agreement)
        session.flush()
        if warning is not None:
            _record_ack(session, shop_id, creator_id, "collab_offer", agreement.id, actor, ip)
        session.add(CommissionAuditLog(id=str(uuid.uuid4()), shop_id=shop_id, actor_role="seller", actor_id=actor,
                                       action="collab_offered", details={"agreement_id": agreement.id, "terms_version": agreement.terms_version,
                                                                         "terms_sha256": agreement.terms_sha256}, created_at=now))
        return _agreement_dict(session, agreement, viewer="seller")


@_emits
def accept_offer(creator_id: str, agreement_id: str, *, terms_version_shown: str, terms_sha256_shown: str, actor: str, ip: Optional[str]) -> dict:
    require_db()
    with db.session_scope() as session:
        ag = session.get(CollabAgreement, agreement_id)
        if ag is None or ag.creator_id != creator_id:
            raise HTTPException(status_code=404, detail="Offer not found")
        if ag.status != "offered":
            raise HTTPException(status_code=409, detail="This offer is no longer open")
        if terms_version_shown != ag.terms_version or terms_sha256_shown != ag.terms_sha256:
            raise HTTPException(status_code=409, detail="The terms changed. Please reload and read them again.")
        ag.status = "active"
        ag.creator_accepted_at = utcnow()
        ag.creator_accepted_by = actor
        ag.creator_ip = ip
        session.add(CommissionAuditLog(id=str(uuid.uuid4()), shop_id=ag.shop_id, actor_role="creator", actor_id=actor,
                                       action="collab_accepted", details={"agreement_id": ag.id, "terms_sha256": ag.terms_sha256}, created_at=utcnow()))
        _track_shop("collab_agreed", ag.shop_id, creator_id=creator_id, collab_id=ag.id)
        return _agreement_dict(session, ag, viewer="creator")


def list_agreements(*, shop_id: Optional[str] = None, creator_id: Optional[str] = None, viewer: str) -> list[dict]:
    require_db()
    with db.session_scope() as session:
        query = select(CollabAgreement).order_by(CollabAgreement.created_at.desc())
        if shop_id:
            query = query.where(CollabAgreement.shop_id == shop_id)
        if creator_id:
            query = query.where(CollabAgreement.creator_id == creator_id)
        return [_agreement_dict(session, ag, viewer=viewer) for ag in session.scalars(query).all()]


def agreement_detail(agreement_id: str, *, viewer: str, shop_id: Optional[str] = None, creator_id: Optional[str] = None) -> dict:
    require_db()
    with db.session_scope() as session:
        ag = session.get(CollabAgreement, agreement_id)
        if ag is None or (shop_id and ag.shop_id != shop_id) or (creator_id and ag.creator_id != creator_id):
            raise HTTPException(status_code=404, detail="Collab not found")
        return _agreement_dict(session, ag, viewer=viewer)


def _agreement_dict(session, ag: CollabAgreement, *, viewer: str) -> dict:
    out = {
        "id": ag.id, "shop_id": ag.shop_id, "creator_id": ag.creator_id, "product_id": ag.product_id,
        "commission_rate": float(ag.commission_rate), "payment_terms_days": ag.payment_terms_days, "paid_via": ag.paid_via,
        "excluded_from_owed": ag.paid_via in PLATFORM_PAID and exclude_platform_paid(),
        "status": ag.status, "terms_version": ag.terms_version, "terms_text": ag.terms_text, "terms_sha256": ag.terms_sha256,
        "seller_accepted_at": ag.seller_accepted_at, "creator_accepted_at": ag.creator_accepted_at,
    }
    if viewer == "seller":
        warning = abn_warning_for(session, ag.creator_id)
        if warning is not None:
            out["abn_warning"] = warning
        if ag.paid_via == "shopify_collabs":
            out["double_payment_warning"] = SHOPIFY_COLLABS_WARNING
        import spark_ads

        out["spark_ads_helper"] = spark_ads.HELPER
    return out


# ---------------------------------------------------------------- audit + events

def audit_log(*, shop_id: Optional[str] = None, statement_id: Optional[str] = None, _session=None) -> list[dict]:
    def run(session):
        query = select(CommissionAuditLog).order_by(CommissionAuditLog.created_at.asc())
        if shop_id:
            query = query.where(CommissionAuditLog.shop_id == shop_id)
        if statement_id:
            query = query.where(CommissionAuditLog.statement_id == statement_id)
        return [
            {"at": r.created_at, "role": r.actor_role, "actor": r.actor_id if r.actor_role in {"admin", "system"} else r.actor_role,
             "action": r.action, "reason": r.reason, "details": r.details}
            for r in session.scalars(query).all()
        ]

    if _session is not None:
        return run(_session)
    require_db()
    with db.session_scope() as session:
        return run(session)


def _audit(session, st: CommissionStatement, role: str, actor: str, action: str, *, reason=None, details=None) -> None:
    session.add(CommissionAuditLog(id=str(uuid.uuid4()), shop_id=st.shop_id, statement_id=st.id, actor_role=role,
                                   actor_id=str(actor or "unknown")[:255], action=action, reason=reason, details=details, created_at=utcnow()))


def _track(key: str, st: CommissionStatement, **props) -> None:
    _track_shop(key, st.shop_id, creator_id=st.creator_id, owed_id=st.id, currency=st.currency, suffix=st.id, **props)


def _track_shop(key: str, shop_id: str, suffix: Optional[str] = None, **props) -> None:
    props["_suffix"] = suffix
    items = getattr(_queue, "items", None)
    if items is None:
        _send(key, shop_id, props)
    else:
        items.append((key, shop_id, props))


def _send(key: str, shop_id: str, props: dict) -> None:
    import logging
    import tracking

    props = dict(props)
    suffix = props.pop("_suffix", None)
    try:
        tracking.track(key, event_id=f"{key}:{suffix or shop_id}:{uuid.uuid4().hex[:8]}", entity_type="shop", entity_id=shop_id,
                       shop_id=shop_id, **props)
    except Exception:
        logging.getLogger("plantr.commission").exception("Could not record %s", key)
