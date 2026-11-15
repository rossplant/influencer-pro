"""Stripe phase 1: Customers for shops and Accounts v2 recipients for creators.

No invoices, transfers, or commission maths. Fee rates are placeholders.
The app keeps running when the Stripe env vars are unset.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import db
from models import Creator, PortalAccount, Shop, StripeEvent, utcnow

logger = logging.getLogger("plantr.stripe")

HANDLED_EVENT_TYPES = (
    "account.updated",
    "v2.core.account.updated",
    "invoice.paid",
    "invoice.payment_failed",
)
NOT_CONFIGURED = "Stripe is not configured"


def configured() -> bool:
    return bool(secret_key())


def secret_key() -> str:
    return os.getenv("STRIPE_SECRET_KEY", "").strip()


def webhook_secret() -> str:
    return os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()


def publishable_key() -> str:
    return os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()


def fee_placeholders() -> dict:
    """Configurable rates for a later phase. Nothing is charged from these."""
    return {
        "platform_fee_percent": os.getenv("STRIPE_PLATFORM_FEE_PERCENT", "").strip(),
        "creator_commission_percent": os.getenv("STRIPE_CREATOR_COMMISSION_PERCENT", "").strip(),
        "currency": "AUD",
        "country": "AU",
        "fees_collector": "application",
        "losses_collector": "application",
        "note": "Placeholders only. No commission is calculated in this version.",
    }


def client():
    if not configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    import stripe

    return stripe.StripeClient(secret_key())


def seller_login_email(shop_id: str) -> Optional[str]:
    return _portal_email(role="seller", shop_id=shop_id)


def creator_login_email(creator_id: str) -> Optional[str]:
    return _portal_email(role="creator", creator_id=creator_id)


def shop_status(shop_id: str) -> dict:
    customer_id = _shop_customer_id(shop_id)
    return {
        "configured": configured(),
        "stripe_customer_id": customer_id,
        "billing_status": "customer" if customer_id else ("not_configured" if not configured() else "not_started"),
        "charges_enabled": False,
        "fees": fee_placeholders(),
    }


def creator_status(creator_id: str) -> dict:
    row = _creator_stripe(creator_id)
    return {
        "configured": configured(),
        "stripe_account_id": row.get("stripe_account_id"),
        "payouts_enabled": bool(row.get("payouts_enabled")),
        "details_submitted": bool(row.get("details_submitted")),
        "stripe_requirements_due": list(row.get("stripe_requirements_due") or []),
        "status": "not_configured" if not configured() else ("connected" if row.get("stripe_account_id") else "not_started"),
    }


def ensure_customer(shop_id: str, *, email: str, name: str) -> str:
    existing = _shop_customer_id(shop_id)
    if existing:
        return existing
    created = client().customers.create(
        {
            "email": email,
            "name": name,
            "metadata": {"shop_id": shop_id},
        }
    )
    customer_id = _id_of(created)
    _set_shop_customer(shop_id, customer_id)
    return customer_id


def ensure_connected_account(creator_id: str, *, email: str, display_name: str) -> str:
    existing = _creator_stripe(creator_id).get("stripe_account_id")
    if existing:
        return existing
    created = client().v2.core.accounts.create(
        {
            "contact_email": email,
            "display_name": display_name,
            "dashboard": "express",
            "identity": {"country": "AU"},
            "configuration": {
                "recipient": {
                    "capabilities": {
                        "stripe_balance": {"stripe_transfers": {"requested": True}}
                    }
                }
            },
            "defaults": {
                "currency": "aud",
                "responsibilities": {
                    "fees_collector": "application",
                    "losses_collector": "application",
                },
            },
            "metadata": {"creator_id": creator_id},
        }
    )
    account_id = _id_of(created)
    _save_creator_stripe(creator_id, {"stripe_account_id": account_id})
    return account_id


def account_link(account_id: str, *, return_url: str, refresh_url: str) -> str:
    link = client().v2.core.account_links.create(
        {
            "account": account_id,
            "use_case": {
                "type": "account_onboarding",
                "account_onboarding": {
                    "refresh_url": refresh_url,
                    "return_url": return_url,
                },
            },
        }
    )
    url = _attr(link, "url")
    if not url:
        raise HTTPException(status_code=502, detail="Stripe did not return an onboarding URL")
    return str(url)


def refresh_account(creator_id: str) -> dict:
    account_id = _creator_stripe(creator_id).get("stripe_account_id")
    if not account_id or not configured():
        return creator_status(creator_id)
    account = client().v2.core.accounts.retrieve(account_id)
    parsed = parse_account_status(account)
    parsed["stripe_account_id"] = account_id
    _save_creator_stripe(creator_id, parsed)
    return creator_status(creator_id)


def construct_event(payload: bytes, signature: str) -> dict:
    secret = webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    import stripe

    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature") from exc
    if hasattr(event, "to_dict"):
        return event.to_dict()
    return dict(event)


def record_event(event: dict) -> bool:
    """Store the event. Return False when this Stripe event id was already stored."""
    event_id = str(event.get("id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="Stripe event id is missing")
    event_type = str(event.get("type") or "")
    if db.configured():
        try:
            with db.session_scope() as session:
                session.add(
                    StripeEvent(
                        id=str(uuid.uuid4()),
                        stripe_event_id=event_id,
                        event_type=event_type,
                        payload=event,
                        processed=False,
                        created_at=utcnow(),
                    )
                )
                session.flush()
        except IntegrityError:
            return False
        return True
    if event_id in db.memory_stripe_events:
        return False
    db.memory_stripe_events[event_id] = {
        "id": str(uuid.uuid4()),
        "stripe_event_id": event_id,
        "event_type": event_type,
        "payload": event,
        "processed": False,
    }
    return True


def apply_event(event: dict) -> None:
    event_type = str(event.get("type") or "")
    data_object = ((event.get("data") or {}).get("object") or {})
    if _is_account_update(event_type):
        _apply_account_update(data_object)
    _mark_processed(str(event.get("id") or ""))


def parse_account_status(account: Any) -> dict:
    """Read payout flags from an Accounts v2 object or a v1 account.updated payload."""
    if _attr(account, "payouts_enabled") is not None or _attr(account, "details_submitted") is not None:
        requirements = _attr(account, "requirements") or {}
        due = _attr(requirements, "currently_due") or []
        return {
            "payouts_enabled": bool(_attr(account, "payouts_enabled")),
            "details_submitted": bool(_attr(account, "details_submitted")),
            "stripe_requirements_due": list(due),
        }
    transfers = _dig(
        account,
        "configuration",
        "recipient",
        "capabilities",
        "stripe_balance",
        "stripe_transfers",
    )
    status = _attr(transfers, "status")
    due = _requirements_due(account)
    return {
        "payouts_enabled": status == "active",
        "details_submitted": not due,
        "stripe_requirements_due": due,
    }


def _is_account_update(event_type: str) -> bool:
    return event_type in {"account.updated", "v2.core.account.updated"} or event_type.startswith(
        "v2.core.account"
    )


def _apply_account_update(account: Any) -> None:
    account_id = str(_attr(account, "id") or "")
    if not account_id:
        return
    creator_id = _creator_id_for_account(account_id)
    if creator_id is None:
        logger.warning("Stripe account %s is not linked to a creator", account_id)
        return
    parsed = parse_account_status(account)
    parsed["stripe_account_id"] = account_id
    _save_creator_stripe(creator_id, parsed)


def _requirements_due(account: Any) -> list:
    entries = _dig(account, "requirements", "entries") or []
    due = []
    for entry in entries:
        if _attr(entry, "status") in {None, "currently_due"}:
            description = _attr(entry, "description") or _attr(entry, "awaiting_action_from")
            if description:
                due.append(str(description))
    return due


def _portal_email(*, role: str, shop_id: Optional[str] = None, creator_id: Optional[str] = None) -> Optional[str]:
    if db.configured():
        with db.session_scope() as session:
            query = select(PortalAccount).where(PortalAccount.role == role)
            if shop_id:
                query = query.where(PortalAccount.shop_id == shop_id)
            if creator_id:
                query = query.where(PortalAccount.creator_id == creator_id)
            row = session.scalars(query).first()
            return row.email if row is not None else None
    for row in db.memory_portal_accounts:
        if row.role != role:
            continue
        if shop_id and row.shop_id == shop_id:
            return row.email
        if creator_id and row.creator_id == creator_id:
            return row.email
    return None


def _shop_customer_id(shop_id: str) -> Optional[str]:
    if db.configured():
        with db.session_scope() as session:
            shop = session.get(Shop, shop_id)
            return shop.stripe_customer_id if shop is not None else None
    shop = db.memory_shops.get(shop_id)
    return getattr(shop, "stripe_customer_id", None) if shop is not None else None


def _set_shop_customer(shop_id: str, customer_id: str) -> None:
    if db.configured():
        with db.session_scope() as session:
            shop = session.get(Shop, shop_id)
            if shop is None:
                raise HTTPException(status_code=404, detail="Shop not found")
            shop.stripe_customer_id = customer_id
        return
    shop = db.memory_shops.get(shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    shop.stripe_customer_id = customer_id


def _creator_stripe(creator_id: str) -> dict:
    if db.configured():
        with db.session_scope() as session:
            creator = session.get(Creator, creator_id)
            if creator is None:
                return {}
            return {
                "stripe_account_id": creator.stripe_account_id,
                "payouts_enabled": creator.payouts_enabled,
                "details_submitted": creator.details_submitted,
                "stripe_requirements_due": list(creator.stripe_requirements_due or []),
            }
    return dict(db.memory_creator_stripe.get(creator_id) or {})


def _save_creator_stripe(creator_id: str, values: dict) -> None:
    if db.configured():
        with db.session_scope() as session:
            creator = session.get(Creator, creator_id)
            if creator is None:
                raise HTTPException(status_code=404, detail="Creator not found")
            if "stripe_account_id" in values:
                creator.stripe_account_id = values["stripe_account_id"]
            if "payouts_enabled" in values:
                creator.payouts_enabled = bool(values["payouts_enabled"])
            if "details_submitted" in values:
                creator.details_submitted = bool(values["details_submitted"])
            if "stripe_requirements_due" in values:
                creator.stripe_requirements_due = list(values["stripe_requirements_due"])
        return
    current = dict(db.memory_creator_stripe.get(creator_id) or {})
    current.update(values)
    db.memory_creator_stripe[creator_id] = current


def _creator_id_for_account(account_id: str) -> Optional[str]:
    if db.configured():
        with db.session_scope() as session:
            creator = session.scalar(select(Creator).where(Creator.stripe_account_id == account_id))
            return creator.id if creator is not None else None
    for creator_id, row in db.memory_creator_stripe.items():
        if row.get("stripe_account_id") == account_id:
            return creator_id
    return None


def _mark_processed(event_id: str) -> None:
    if not event_id:
        return
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(StripeEvent).where(StripeEvent.stripe_event_id == event_id))
            if row is not None:
                row.processed = True
        return
    stored = db.memory_stripe_events.get(event_id)
    if stored is not None:
        stored["processed"] = True


def _id_of(obj: Any) -> str:
    value = _attr(obj, "id")
    if not value:
        raise HTTPException(status_code=502, detail="Stripe did not return an id")
    return str(value)


def _attr(obj: Any, name: str):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _dig(obj: Any, *names: str):
    current = obj
    for name in names:
        current = _attr(current, name)
    return current
