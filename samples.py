"""Product sample requests between a Creatr and a supplier.

The shipping address is stored only as line, suburb, region and postcode.
Suppliers see it after approval, and it is removed 30 days after delivery.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

import db
from models import SampleRequest, utcnow

STATUSES = ("requested", "approved", "declined", "shipped", "delivered", "posted", "overdue")
ADDRESS_VISIBLE = {"approved", "shipped", "delivered", "posted", "overdue"}
DISCLOSURE_HELP = (
    "A gifted post has to be clearly labelled, for example with #Ad or the word gifted. "
    "That is what the ACCC influencer guidance asks for."
)


def _now(moment=None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def due_days(value: int) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = 14
    return min(30, max(7, days))


def post_due(delivered: datetime, days: int) -> date:
    return _now(delivered).date() + timedelta(days=due_days(days))


def effective_status(row: dict, *, now=None) -> str:
    now = _now(now)
    status = row.get("status") or "requested"
    if row.get("post_links"):
        return "posted"
    if status in {"delivered", "posted", "overdue"} and row.get("post_due_on") and now.date() > row["post_due_on"]:
        return "overdue"
    return status


def reminder_kind(row: dict, *, now=None) -> str:
    now = _now(now)
    if effective_status(row, now=now) != "delivered":
        return ""
    due = row.get("post_due_on")
    if not due:
        return ""
    left = (due - now.date()).days
    if 0 <= left <= 2:
        return "2"
    if 3 <= left <= 7:
        return "7"
    return ""


def address_for(row: dict, *, viewer: str, now=None) -> str:
    """Suppliers see the address only after approval, and not 30 days after delivery."""
    if viewer != "supplier":
        if row.get("address_redacted"):
            return ""
        return _format_address(row)
    status = effective_status(row, now=now)
    if status not in ADDRESS_VISIBLE or row.get("address_redacted"):
        return ""
    delivered = row.get("delivered_at")
    if delivered and _now(now) >= _now(delivered) + timedelta(days=30):
        return ""
    return _format_address(row)


def _format_address(row: dict) -> str:
    parts = [row.get("address_line"), row.get("suburb"), row.get("region"), row.get("postcode")]
    return ", ".join(str(part) for part in parts if part)


def _settings():
    if not hasattr(db, "memory_sample_settings"):
        db.memory_sample_settings = {}
    return db.memory_sample_settings


def settings_for(shop_id: str) -> dict:
    if db.configured():
        from models import Shop

        with db.session_scope() as session:
            shop = session.get(Shop, shop_id)
            if shop is None:
                return {"max_per_month": 5, "post_due_days": 14}
            return {
                "max_per_month": int(shop.sample_max_per_month or 5),
                "post_due_days": due_days(shop.sample_post_due_days or 14),
            }
    found = _settings().get(shop_id) or {}
    return {"max_per_month": int(found.get("max_per_month") or 5), "post_due_days": due_days(found.get("post_due_days") or 14)}


def save_settings(shop_id: str, *, max_per_month: int, post_due_days: int) -> dict:
    stored = {"max_per_month": max(0, int(max_per_month)), "post_due_days": due_days(post_due_days)}
    if db.configured():
        from models import Shop

        with db.session_scope() as session:
            shop = session.get(Shop, shop_id)
            if shop is not None:
                shop.sample_max_per_month = stored["max_per_month"]
                shop.sample_post_due_days = stored["post_due_days"]
        return stored
    _settings()[shop_id] = stored
    return stored


def samples_enabled(product: dict) -> bool:
    attrs = product.get("attributes") or {}
    return bool(attrs.get("samples_enabled"))


def _month_count(shop_id: str, now: datetime) -> int:
    count = 0
    for row in list_for_shop(shop_id):
        created = row.get("created_at")
        if created and _now(created).year == now.year and _now(created).month == now.month and row.get("status") != "declined":
            count += 1
    return count


def request_sample(*, shop_id: str, creator_id: str, product: dict, note: str, address: dict) -> dict:
    if not samples_enabled(product):
        raise HTTPException(status_code=422, detail="This product is not open for samples.")
    now = _now()
    settings = settings_for(shop_id)
    if _month_count(shop_id, now) >= settings["max_per_month"]:
        raise HTTPException(status_code=422, detail="This supplier has reached the sample limit for the month.")
    row = {
        "id": str(uuid.uuid4()),
        "shop_id": shop_id,
        "creator_id": creator_id,
        "product_id": product.get("id"),
        "product_title": product.get("title") or "",
        "note": (note or "").strip(),
        "status": "requested",
        "decline_reason": "",
        "address_line": (address.get("address_line") or "").strip(),
        "suburb": (address.get("suburb") or "").strip(),
        "region": (address.get("region") or "").strip(),
        "postcode": (address.get("postcode") or "").strip(),
        "tracking": "",
        "shipped_at": None,
        "delivered_at": None,
        "post_due_on": None,
        "posted_at": None,
        "post_links": "",
        "disclosure_confirmed": False,
        "reminder_7_sent": False,
        "reminder_2_sent": False,
        "address_redacted": False,
        "created_at": now,
    }
    if not row["address_line"] or not row["postcode"]:
        raise HTTPException(status_code=422, detail="Add the address line and postcode.")
    _insert(row)
    return row


def decide(sample_id: str, shop_id: str, *, approved: bool, reason: str = "") -> dict:
    row = _get(sample_id)
    if row["shop_id"] != shop_id or row["status"] != "requested":
        raise HTTPException(status_code=404, detail="Sample request not found")
    row["status"] = "approved" if approved else "declined"
    row["decline_reason"] = "" if approved else (reason or "").strip()
    _update(row)
    return row


def ship(sample_id: str, shop_id: str, *, tracking: str = "") -> dict:
    row = _get(sample_id)
    if row["shop_id"] != shop_id or row["status"] != "approved":
        raise HTTPException(status_code=404, detail="Sample request not found")
    row["status"] = "shipped"
    row["tracking"] = (tracking or "").strip()
    row["shipped_at"] = _now()
    _update(row)
    return row


def mark_delivered(sample_id: str, *, actor_shop: str = "", actor_creator: str = "") -> dict:
    row = _get(sample_id)
    if actor_shop and row["shop_id"] != actor_shop:
        raise HTTPException(status_code=404, detail="Sample request not found")
    if actor_creator and row["creator_id"] != actor_creator:
        raise HTTPException(status_code=404, detail="Sample request not found")
    if row["status"] not in {"shipped", "approved"}:
        raise HTTPException(status_code=422, detail="This sample is not ready to mark delivered.")
    delivered = _now()
    row["status"] = "delivered"
    row["delivered_at"] = delivered
    row["post_due_on"] = post_due(delivered, settings_for(row["shop_id"])["post_due_days"])
    _update(row)
    return row


def submit_post(sample_id: str, creator_id: str, *, links: str, disclosure: bool) -> dict:
    row = _get(sample_id)
    if row["creator_id"] != creator_id:
        raise HTTPException(status_code=404, detail="Sample request not found")
    if effective_status(row) not in {"delivered", "overdue"}:
        raise HTTPException(status_code=422, detail="Post the link after the sample is delivered.")
    if not disclosure:
        raise HTTPException(status_code=422, detail="Tick the box to confirm the post is labelled #Ad or gifted.")
    if not (links or "").strip():
        raise HTTPException(status_code=422, detail="Add the link to the post.")
    row["post_links"] = links.strip()
    row["disclosure_confirmed"] = True
    row["posted_at"] = _now()
    row["status"] = "posted"
    _update(row)
    return row


def refresh(row: dict, *, now=None, email: str = "") -> dict:
    now = _now(now)
    status = effective_status(row, now=now)
    if status == "overdue" and row.get("status") != "overdue":
        row["status"] = "overdue"
    delivered = row.get("delivered_at")
    if delivered and not row.get("address_redacted") and now >= _now(delivered) + timedelta(days=30):
        row["address_line"] = ""
        row["suburb"] = ""
        row["region"] = ""
        row["postcode"] = ""
        row["address_redacted"] = True
    kind = reminder_kind(row, now=now)
    if kind == "7" and not row.get("reminder_7_sent"):
        row["reminder_7_sent"] = True
        _email(email, "sample_due_7", row)
    if kind == "2" and not row.get("reminder_2_sent"):
        row["reminder_2_sent"] = True
        _email(email, "sample_due_2", row)
    _update(row)
    return row


def _email(email: str, template: str, row: dict) -> None:
    if not email:
        return
    import emailer

    emailer.send(template, email, product=row.get("product_title") or "your sample", due=str(row.get("post_due_on") or ""))


def list_for_shop(shop_id: str) -> list[dict]:
    return [row for row in _all() if row["shop_id"] == shop_id]


def list_for_creator(creator_id: str) -> list[dict]:
    return [row for row in _all() if row["creator_id"] == creator_id]


def list_all(status: str = "") -> list[dict]:
    rows = _all()
    if status:
        rows = [row for row in rows if effective_status(row) == status]
    return rows


def _all() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            return [_from_model(row) for row in session.scalars(select(SampleRequest)).all()]
    if not hasattr(db, "memory_samples"):
        db.memory_samples = []
    return list(db.memory_samples)


def _get(sample_id: str) -> dict:
    found = next((row for row in _all() if row["id"] == sample_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Sample request not found")
    return found


def _insert(row: dict) -> None:
    if db.configured():
        with db.session_scope() as session:
            session.add(SampleRequest(**_model_fields(row)))
        return
    if not hasattr(db, "memory_samples"):
        db.memory_samples = []
    db.memory_samples.append(row)


def _update(row: dict) -> None:
    if db.configured():
        with db.session_scope() as session:
            stored = session.get(SampleRequest, row["id"])
            if stored is None:
                return
            for key, value in _model_fields(row).items():
                setattr(stored, key, value)
        return
    for index, existing in enumerate(db.memory_samples):
        if existing["id"] == row["id"]:
            db.memory_samples[index] = row
            return


def _model_fields(row: dict) -> dict:
    return {key: row.get(key) for key in (
        "id", "shop_id", "creator_id", "product_id", "note", "status", "decline_reason",
        "address_line", "suburb", "region", "postcode", "tracking", "shipped_at", "delivered_at",
        "post_due_on", "posted_at", "post_links", "disclosure_confirmed", "reminder_7_sent",
        "reminder_2_sent", "address_redacted", "created_at",
    )}


def _from_model(row: SampleRequest) -> dict:
    return {
        "id": row.id,
        "shop_id": row.shop_id,
        "creator_id": row.creator_id,
        "product_id": row.product_id,
        "product_title": "",
        "note": row.note or "",
        "status": row.status,
        "decline_reason": row.decline_reason or "",
        "address_line": row.address_line or "",
        "suburb": row.suburb or "",
        "region": row.region or "",
        "postcode": row.postcode or "",
        "tracking": row.tracking or "",
        "shipped_at": row.shipped_at,
        "delivered_at": row.delivered_at,
        "post_due_on": row.post_due_on,
        "posted_at": row.posted_at,
        "post_links": row.post_links or "",
        "disclosure_confirmed": bool(row.disclosure_confirmed),
        "reminder_7_sent": bool(row.reminder_7_sent),
        "reminder_2_sent": bool(row.reminder_2_sent),
        "address_redacted": bool(row.address_redacted),
        "created_at": row.created_at,
    }
