"""Read-only admin queries, plus the two shop flags an operator can change."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import select

import db
import services
from models import AdminUser, Brief, Creator, Event, Product, Shop, Shortlist, ShortlistItem, utcnow


def admin_exists(email: str) -> bool:
    return find_admin(email) is not None


def find_admin(email: str):
    key = (email or "").strip().lower()
    if not key:
        return None
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(AdminUser).where(AdminUser.email == key))
            if row is None:
                return None
            return _admin_copy(row)
    return db.memory_admin_users.get(key)


def create_admin(email: str, password_hash: str):
    key = email.strip().lower()
    now = utcnow()
    if db.configured():
        with db.session_scope() as session:
            row = AdminUser(
                id=str(uuid.uuid4()),
                email=key,
                password_hash=password_hash,
                created_at=now,
            )
            session.add(row)
            session.flush()
            return _admin_copy(row)
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        email=key,
        password_hash=password_hash,
        created_at=now,
        last_login_at=None,
    )
    db.memory_admin_users[key] = row
    return row


def mark_admin_login(email: str) -> None:
    key = email.strip().lower()
    now = utcnow()
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(AdminUser).where(AdminUser.email == key))
            if row is not None:
                row.last_login_at = now
        return
    row = db.memory_admin_users.get(key)
    if row is not None:
        row.last_login_at = now


def _admin_copy(row: AdminUser):
    return SimpleNamespace(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
    )


def summary() -> dict:
    shops = _shops()
    creators = _creators()
    products = _products()
    shortlists = _shortlists()
    items = _items()
    return {
        "shops": len(shops),
        "creators": len(creators),
        "products": len(products),
        "shortlists": len(shortlists),
        "accepted_matches": sum(1 for item in items if item.get("accepted_at")),
        "declined_matches": sum(1 for item in items if item.get("declined_at")),
        "briefs_sent": sum(1 for brief in _briefs() if brief.get("sent_at")),
    }


def list_shops(q: str, page: int, page_size: int) -> dict:
    rows = _shops()
    return _page(rows, q, page, page_size, lambda row: f"{row.get('name') or ''} {row.get('shopify_domain') or ''}")


def shop_detail(shop_id: str) -> Optional[dict]:
    return next((row for row in _shops() if row["id"] == shop_id), None)


def update_shop_flags(
    shop_id: str,
    *,
    is_pilot: bool,
    is_underserved: bool,
    underserved_reason: Optional[str],
) -> Optional[dict]:
    reason = (underserved_reason or "").strip() or None
    if not is_underserved:
        reason = None
    if db.configured():
        with db.session_scope() as session:
            shop = session.get(Shop, shop_id)
            if shop is None:
                return None
            shop.is_pilot = bool(is_pilot)
            shop.is_underserved = bool(is_underserved)
            shop.underserved_reason = reason
            session.flush()
            return services.shop_dict(shop)
    shop = db.memory_shops.get(shop_id)
    if shop is None:
        return None
    shop.is_pilot = bool(is_pilot)
    shop.is_underserved = bool(is_underserved)
    shop.underserved_reason = reason
    return services.shop_dict(shop)


def list_creators(q: str, page: int, page_size: int) -> dict:
    rows = _creators()
    return _page(rows, q, page, page_size, lambda row: f"{row.get('handle') or ''} {row.get('name') or ''}")


def creator_detail(creator_id: str) -> Optional[dict]:
    return next((row for row in _creators(full=True) if row["id"] == creator_id), None)


def list_products(q: str, page: int, page_size: int) -> dict:
    rows = _products()
    return _page(rows, q, page, page_size, lambda row: f"{row.get('title') or ''} {row.get('category') or ''}")


def product_detail(product_id: str) -> Optional[dict]:
    return next((row for row in _products() if row["id"] == product_id), None)


def list_shortlists(q: str, page: int, page_size: int) -> dict:
    rows = _shortlists()
    return _page(
        rows,
        q,
        page,
        page_size,
        lambda row: f"{row.get('id') or ''} {row.get('shop_id') or ''} {row.get('product_id') or ''}",
    )


def shortlist_detail(shortlist_id: str) -> Optional[dict]:
    row = next((item for item in _shortlists() if item["id"] == shortlist_id), None)
    if row is None:
        return None
    detail = dict(row)
    detail["items"] = [item for item in _items() if item.get("shortlist_id") == shortlist_id]
    return detail


def list_events(q: str, page: int, page_size: int) -> dict:
    rows = _events()
    return _page(
        rows,
        q,
        page,
        page_size,
        lambda row: f"{row.get('event_name') or ''} {row.get('entity_type') or ''} {row.get('entity_id') or ''}",
    )


def event_detail(event_id: str) -> Optional[dict]:
    return next((row for row in _events() if row["id"] == event_id), None)


def _page(rows: list, q: str, page: int, page_size: int, haystack) -> dict:
    needle = q.strip().lower()
    if needle:
        rows = [row for row in rows if needle in haystack(row).lower()]
    start = (page - 1) * page_size
    return {"items": rows[start : start + page_size], "page": page, "page_size": page_size, "total": len(rows)}


def _shops() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Shop).order_by(Shop.created_at.desc())).all()
            return [services.shop_dict(row) for row in rows]
    rows = list(db.memory_shops.values())
    rows.sort(key=lambda row: str(getattr(row, "created_at", "")), reverse=True)
    return [services.shop_dict(row) for row in rows]


def _creators(full: bool = False) -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Creator).order_by(Creator.created_at.desc())).all()
            return [_creator_from_model(row, full) for row in rows]
    rows = []
    for creator_id, record in db.memory_creators.items():
        profile = record if isinstance(record, dict) else {}
        rows.append(_creator_from_profile(creator_id, profile, full))
    return rows


def _creator_from_model(row: Creator, full: bool) -> dict:
    profile = row.profile if isinstance(row.profile, dict) else {}
    item = _creator_from_profile(row.id, profile, full)
    item["created_at"] = row.created_at
    item["stripe_account_id"] = row.stripe_account_id
    item["payouts_enabled"] = bool(row.payouts_enabled)
    item["details_submitted"] = bool(row.details_submitted)
    item["stripe_requirements_due"] = list(row.stripe_requirements_due or [])
    return item


def _creator_from_profile(creator_id: str, profile: dict, full: bool) -> dict:
    item = {
        "id": profile.get("influencer_id") or creator_id,
        "handle": profile.get("handle"),
        "name": profile.get("name"),
        "active_categories": list(profile.get("active_categories") or []),
        "platforms": list(profile.get("platforms") or []),
        "stripe_account_id": (db.memory_creator_stripe.get(creator_id) or {}).get("stripe_account_id"),
        "payouts_enabled": bool((db.memory_creator_stripe.get(creator_id) or {}).get("payouts_enabled")),
        "details_submitted": bool((db.memory_creator_stripe.get(creator_id) or {}).get("details_submitted")),
        "stripe_requirements_due": list((db.memory_creator_stripe.get(creator_id) or {}).get("stripe_requirements_due") or []),
    }
    if full:
        item["profile"] = profile
    return item


def _products() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Product).order_by(Product.created_at.desc())).all()
            return [_product(row) for row in rows]
    rows = list(db.memory_products.values())
    rows.sort(key=lambda row: str(getattr(row, "created_at", "")), reverse=True)
    return [_product(row) for row in rows]


def _product(row: Any) -> dict:
    price = getattr(row, "price", None)
    commission = getattr(row, "commission_rate", None)
    return {
        "id": row.id,
        "shop_id": getattr(row, "shop_id", None),
        "title": getattr(row, "title", None),
        "category": getattr(row, "category", None),
        "subcategory": getattr(row, "subcategory", None),
        "price": float(price) if price is not None else None,
        "currency": getattr(row, "currency", None),
        "commission_rate": float(commission) if commission is not None else None,
        "tags": list(getattr(row, "tags", None) or []),
        "attributes": dict(getattr(row, "attributes", None) or {}),
        "claims": list(getattr(row, "claims", None) or []),
        "source": getattr(row, "source", None),
        "created_at": getattr(row, "created_at", None),
    }


def _shortlists() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Shortlist).order_by(Shortlist.created_at.desc())).all()
            return [_shortlist(row) for row in rows]
    rows = list(db.memory_shortlists)
    rows.sort(key=lambda row: str(getattr(row, "created_at", "")), reverse=True)
    return [_shortlist(row) for row in rows]


def _shortlist(row: Any) -> dict:
    return {
        "id": row.id,
        "shop_id": getattr(row, "shop_id", None),
        "product_id": getattr(row, "product_id", None),
        "match_version": getattr(row, "match_version", None),
        "optimise_for": getattr(row, "optimise_for", None),
        "created_at": getattr(row, "created_at", None),
    }


def _items() -> list[dict]:
    creators = {row["id"]: row for row in _creators()}
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(ShortlistItem)).all()
            return [_item(row, creators) for row in rows]
    return [_item(row, creators) for row in db.memory_shortlist_items]


def _item(row: Any, creators: dict) -> dict:
    creator = creators.get(getattr(row, "creator_id", None)) or {}
    return {
        "id": row.id,
        "shortlist_id": getattr(row, "shortlist_id", None),
        "creator_id": getattr(row, "creator_id", None),
        "handle": creator.get("handle"),
        "score": getattr(row, "score", None),
        "rank": getattr(row, "rank", None),
        "accepted_at": getattr(row, "accepted_at", None),
        "declined_at": getattr(row, "declined_at", None),
        "decline_reason": getattr(row, "decline_reason", None),
    }


def _briefs() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Brief)).all()
            return [{"id": row.id, "sent_at": row.sent_at} for row in rows]
    return [{"id": row.id, "sent_at": getattr(row, "sent_at", None)} for row in db.memory_briefs]


def _events() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Event).order_by(Event.occurred_at.desc())).all()
            return [_event(row) for row in rows]
    rows = list(db.memory_events)
    rows.sort(key=lambda row: str(getattr(row, "occurred_at", "")), reverse=True)
    return [_event(row) for row in rows]


def _event(row: Any) -> dict:
    return {
        "id": row.id,
        "occurred_at": getattr(row, "occurred_at", None),
        "event_name": getattr(row, "event_name", None),
        "entity_type": getattr(row, "entity_type", None),
        "entity_id": getattr(row, "entity_id", None),
        "shop_id": getattr(row, "shop_id", None),
        "actor": getattr(row, "actor", None),
        "properties": dict(getattr(row, "properties", None) or {}),
    }
