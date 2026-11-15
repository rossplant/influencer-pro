"""Stripe Billing subscriptions for sellers and creators.

Connect, transfers, and payouts are not used. The app starts when Stripe is
unset. Live secret keys are refused. The sandbox setup script reads
STRIPE_SECRET_KEY_TEST and will not use a live key.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import billing_config
import db
import tracking
from models import BillingSubscription, Creator, PortalAccount, Shop, StripeEvent, utcnow

logger = logging.getLogger("plantr.stripe")

NOT_CONFIGURED = "Stripe is not configured"
HANDLED_EVENT_TYPES = (
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.trial_will_end",
    "invoice.paid",
    "invoice.payment_failed",
)


def configured() -> bool:
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    return bool(key) and not _live(key)


def secret_key() -> str:
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if _live(key):
        raise HTTPException(status_code=503, detail="Live Stripe keys are not allowed")
    return key


def webhook_secret() -> str:
    return os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()


def publishable_key() -> str:
    return os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()


def client():
    key = secret_key()
    if not key:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    import stripe

    return stripe.StripeClient(key)


def shop_status(shop_id: str) -> dict:
    row = _account("seller", shop_id)
    return _status("seller", "starter", row)


def creator_status(creator_id: str) -> dict:
    row = _account("creator", creator_id)
    return _status("creator", "member", row)


def plans(audience: str) -> list:
    table = billing_config.SELLER_TIERS if audience == "seller" else billing_config.CREATOR_TIERS
    listed = []
    for key, spec in table.items():
        listed.append(
            {
                "tier": key,
                "label": spec["label"],
                "monthly": billing_config.price_label(spec["amount_month_cents"]),
                "annual": billing_config.price_label(spec["amount_year_cents"]),
                "tax_display": billing_config.TAX_DISPLAY,
                "stripe_tax": billing_config.STRIPE_TAX_ENABLED,
                "coming_soon": list(spec.get("coming_soon") or []),
            }
        )
    return listed


def checkout(
    *,
    role: str,
    subject_id: str,
    tier: str,
    interval: str,
    success_url: str,
    cancel_url: str,
    promo_code: str = "",
    invite_code: str = "",
    trial_end: Optional[int] = None,
    campaign: str = "",
    channel: str = "",
) -> str:
    email = _portal_email(role, subject_id)
    if not email:
        raise HTTPException(status_code=422, detail="A login email is required before checkout")
    lookup = billing_config.lookup_key(role, tier, interval)
    if not lookup:
        raise HTTPException(status_code=422, detail="That plan does not have a Stripe price")
    customer_id = ensure_customer(role, subject_id, email=email, name=email)
    price_id = _price_id(lookup)
    metadata = {
        "role": role,
        "subject_id": subject_id,
        "tier": tier,
        "interval": interval,
        "promo_code": promo_code,
        "invite_code": invite_code,
        "campaign": campaign or billing_config.PROMOTION_CODES.get(promo_code, {}).get("campaign", ""),
        "channel": channel or billing_config.PROMOTION_CODES.get(promo_code, {}).get("channel", ""),
    }
    subscription_data = {"metadata": metadata}
    if trial_end:
        subscription_data["trial_end"] = trial_end
        subscription_data["trial_settings"] = {"end_behavior": {"missing_payment_method": "cancel"}}
    payload = {
        "mode": "subscription",
        "customer": customer_id,
        "client_reference_id": subject_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items": [{"price": price_id, "quantity": 1}],
        "metadata": metadata,
        "subscription_data": subscription_data,
    }
    if promo_code:
        payload["discounts"] = [{"promotion_code": promo_code}]
    else:
        payload["allow_promotion_codes"] = True
    session = client().checkout.sessions.create(payload)
    url = _attr(session, "url")
    if not url:
        raise HTTPException(status_code=502, detail="Stripe did not return a Checkout URL")
    return str(url)


def portal_session(*, role: str, subject_id: str, return_url: str) -> str:
    customer_id = (_account(role, subject_id) or {}).get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=422, detail="No Stripe customer yet")
    session = client().billing_portal.sessions.create({"customer": customer_id, "return_url": return_url})
    url = _attr(session, "url")
    if not url:
        raise HTTPException(status_code=502, detail="Stripe did not return a portal URL")
    return str(url)


def ensure_customer(role: str, subject_id: str, *, email: str, name: str) -> str:
    current = (_account(role, subject_id) or {}).get("stripe_customer_id")
    if current:
        return current
    created = client().customers.create({"email": email, "name": name, "metadata": {"role": role, "subject_id": subject_id}})
    customer_id = _id_of(created)
    _save_account(role, subject_id, {"stripe_customer_id": customer_id})
    return customer_id


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
    event_id = str(event.get("id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="Stripe event id is missing")
    if db.configured():
        try:
            with db.session_scope() as session:
                session.add(
                    StripeEvent(
                        id=str(uuid.uuid4()),
                        stripe_event_id=event_id,
                        event_type=str(event.get("type") or ""),
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
    db.memory_stripe_events[event_id] = {"processed": False, "event_type": event.get("type")}
    return True


def apply_event(event: dict) -> None:
    event_type = str(event.get("type") or "")
    data_object = ((event.get("data") or {}).get("object") or {})
    if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        _apply_subscription(data_object, deleted=event_type.endswith("deleted"))
    elif event_type == "checkout.session.completed":
        _apply_checkout(data_object)
    elif event_type == "invoice.paid":
        _apply_invoice_paid(data_object)
    elif event_type == "invoice.payment_failed":
        logger.info("invoice payment failed invoice=%s", _attr(data_object, "id"))
    elif event_type == "customer.subscription.trial_will_end":
        email = _email_for_customer(str(_attr(data_object, "customer") or ""))
        if email:
            import emailer

            emailer.send("pilot_ending_7_days", email, ends="the trial end date")
    _mark_processed(str(event.get("id") or ""))


def apply_sale_waiver(creator_id: str) -> dict:
    """Manual trigger. A month with 5 or more attributed sales credits the next invoice."""
    sales = _sales_this_month(creator_id)
    if sales < billing_config.WAIVER_SALES:
        raise HTTPException(status_code=422, detail=f"Need {billing_config.WAIVER_SALES} attributed sales this month")
    if not configured():
        return {"applied": False, "reason": NOT_CONFIGURED, "sales": sales}
    customer_id = (_account("creator", creator_id) or {}).get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=422, detail="Creator has no Stripe customer")
    coupon = billing_config.COUPONS["curator_sale_waiver"]
    # The coupon id in Stripe matches the config key created by the setup script.
    subscription_id = (_account("creator", creator_id) or {}).get("stripe_subscription_id")
    if subscription_id:
        client().subscriptions.update(subscription_id, {"discounts": [{"coupon": "curator_sale_waiver"}]})
    return {"applied": True, "sales": sales, "coupon": "curator_sale_waiver", "percent_off": coupon["percent_off"]}


def grant_elite(creator_id: str) -> dict:
    """Admin recognition: Elite at no charge for 3 months."""
    _save_account("creator", creator_id, {"plan_tier": "elite", "plan_status": "trial", "billing_interval": "month"})
    if configured():
        subscription_id = (_account("creator", creator_id) or {}).get("stripe_subscription_id")
        if subscription_id:
            price = _price_id(billing_config.lookup_key("creator", "elite", "month"))
            client().subscriptions.update(
                subscription_id,
                {"items": [{"price": price}], "discounts": [{"coupon": "elite_grant_3mo"}]},
            )
    return {"creator_id": creator_id, "plan_tier": "elite", "coupon": "elite_grant_3mo"}


def _apply_checkout(session_obj: dict) -> None:
    metadata = _attr(session_obj, "metadata") or {}
    role = str(_attr(metadata, "role") or "")
    subject_id = str(_attr(metadata, "subject_id") or _attr(session_obj, "client_reference_id") or "")
    customer_id = str(_attr(session_obj, "customer") or "")
    subscription_id = str(_attr(session_obj, "subscription") or "")
    if role and subject_id:
        _save_account(
            role,
            subject_id,
            {
                "stripe_customer_id": customer_id or None,
                "stripe_subscription_id": subscription_id or None,
                "plan_tier": str(_attr(metadata, "tier") or ""),
                "billing_interval": str(_attr(metadata, "interval") or "month"),
                "subscription_status": "trialing" if _attr(metadata, "tier") else "active",
            },
        )


def _apply_subscription(subscription: Any, *, deleted: bool) -> None:
    metadata = _attr(subscription, "metadata") or {}
    role = str(_attr(metadata, "role") or "")
    subject_id = str(_attr(metadata, "subject_id") or "")
    if not role or not subject_id:
        found = _find_by_customer(str(_attr(subscription, "customer") or ""))
        if found is None:
            return
        role, subject_id = found
    status = "canceled" if deleted else str(_attr(subscription, "status") or "")
    tier = str(_attr(metadata, "tier") or "")
    if status in {"canceled", "incomplete_expired", "unpaid"}:
        tier = "starter" if role == "seller" else "member"
        plan_status = "free" if role == "seller" else "invited"
    elif status == "trialing":
        plan_status = "trial"
    else:
        plan_status = "active" if status == "active" else status
    values = {
        "stripe_customer_id": str(_attr(subscription, "customer") or "") or None,
        "stripe_subscription_id": str(_attr(subscription, "id") or "") or None,
        "subscription_status": status,
        "plan_status": plan_status,
        "billing_interval": str(_attr(metadata, "interval") or "month"),
    }
    if tier:
        values["plan_tier"] = tier
    _save_account(role, subject_id, values)
    _upsert_billing_row(role, subject_id, values, metadata)


def account_context(role: str, subject_id: str) -> dict:
    row = _account(role, subject_id) or {}
    return {
        "signup_code": row.get("signup_code") or "",
        "signup_campaign": row.get("signup_campaign") or "",
        "marketing_consent": bool(row.get("marketing_consent")),
        "attribution_first": row.get("attribution_first") or {},
        "attribution_last": row.get("attribution_last") or {},
    }


def _period_label(interval: str) -> str:
    cleaned = (interval or "").strip().lower()
    if cleaned == "month":
        return "monthly"
    if cleaned == "year":
        return "annual"
    return cleaned


def _apply_invoice_paid(invoice: Any) -> None:
    amount = _attr(invoice, "amount_paid")
    if not amount:
        return
    subscription_id = str(_attr(invoice, "subscription") or "")
    invoice_id = str(_attr(invoice, "id") or "")
    currency = str(_attr(invoice, "currency") or "")
    metadata = _invoice_metadata(invoice)
    role = str(_attr(metadata, "role") or "")
    subject_id = str(_attr(metadata, "subject_id") or "")
    tier = str(_attr(metadata, "tier") or "")
    interval = str(_attr(metadata, "interval") or "")
    found = _find_by_customer(str(_attr(invoice, "customer") or ""))
    if found and not role:
        role, subject_id = found
    account = _account(role, subject_id) if role and subject_id else {}
    created_at = (account or {}).get("created_at")
    days = 0
    if created_at is not None:
        days = max(0, (utcnow() - created_at).days)
    invite_code = _attr(metadata, "invite_code") or (account or {}).get("signup_code")
    campaign = _attr(metadata, "campaign") or (account or {}).get("signup_campaign")
    value = float(amount) / 100.0
    common = {
        "tier": tier,
        "period": _period_label(interval),
        "value": value,
        "currency": currency,
        "invite_code": invite_code,
        "promo_code": _attr(metadata, "promo_code"),
        "campaign": campaign,
    }
    account_touch = {
        "first": (account or {}).get("attribution_first") or {},
        "last": (account or {}).get("attribution_last") or {},
    }
    consent = bool((account or {}).get("marketing_consent"))
    subscribed = tracking.track(
        "subscribe",
        event_id=f"sub:{invoice_id}",
        subject_key=f"sub-first:{subscription_id}" if subscription_id else None,
        role=role or None,
        entity_type="shop" if role == "seller" else "creator",
        entity_id=subject_id or invoice_id,
        shop_id=subject_id if role == "seller" else None,
        actor="system",
        consent=consent,
        touch=account_touch,
        **common,
    )
    if subscribed.get("duplicate"):
        return
    if invite_code or campaign:
        tracking.track(
            "founding_free_to_paid",
            event_id=f"founding:{invoice_id}",
            subject_key=f"founding-first:{subscription_id}" if subscription_id else None,
            role=role or None,
            entity_type="shop" if role == "seller" else "creator",
            entity_id=subject_id or invoice_id,
            shop_id=subject_id if role == "seller" else None,
            actor="system",
            consent=consent,
            touch=account_touch,
            days_since_signup=days,
            **common,
        )
        _mark_converted(role, subject_id)


def _invoice_metadata(invoice: Any):
    lines = _attr(invoice, "lines") or {}
    data = _attr(lines, "data") or []
    if data:
        parent = _attr(data[0], "metadata")
        if parent:
            return parent
    return _attr(invoice, "metadata") or {}


def _price_id(lookup: str) -> str:
    prices = client().prices.list({"lookup_keys": [lookup], "limit": 1})
    data = _attr(prices, "data") or []
    if not data:
        raise HTTPException(status_code=503, detail=f"Stripe price {lookup} is not set up")
    return _id_of(data[0])


def _status(role: str, default_tier: str, row: Optional[dict]) -> dict:
    row = row or {}
    tier = row.get("plan_tier") or default_tier
    return {
        "configured": configured(),
        "tax_display": billing_config.TAX_DISPLAY,
        "stripe_tax": billing_config.STRIPE_TAX_ENABLED,
        "stripe_customer_id": row.get("stripe_customer_id"),
        "stripe_subscription_id": row.get("stripe_subscription_id"),
        "subscription_status": row.get("subscription_status"),
        "plan_tier": tier,
        "plan_status": row.get("plan_status"),
        "billing_interval": row.get("billing_interval"),
        "plans": plans(role),
        "publishable_key": publishable_key(),
    }


def _account(role: str, subject_id: str) -> Optional[dict]:
    if not subject_id:
        return None
    if db.configured():
        with db.session_scope() as session:
            row = session.get(Shop if role == "seller" else Creator, subject_id)
            if row is None:
                return None
            return _account_dict(row)
    if role == "seller":
        row = db.memory_shops.get(subject_id)
    else:
        row = db.memory_creator_objects.get(subject_id)
    return None if row is None else _account_dict(row)


def _account_dict(row) -> dict:
    return {
        "stripe_customer_id": getattr(row, "stripe_customer_id", None),
        "stripe_subscription_id": getattr(row, "stripe_subscription_id", None),
        "subscription_status": getattr(row, "subscription_status", None),
        "plan_tier": getattr(row, "plan_tier", None),
        "plan_status": getattr(row, "plan_status", None),
        "billing_interval": getattr(row, "billing_interval", None),
        "created_at": getattr(row, "created_at", None),
        "marketing_consent": bool(getattr(row, "marketing_consent", False)),
        "attribution_first": getattr(row, "attribution_first", None) or {},
        "attribution_last": getattr(row, "attribution_last", None) or {},
        "signup_code": getattr(row, "signup_code", None),
        "signup_campaign": getattr(row, "signup_campaign", None),
    }


def _save_account(role: str, subject_id: str, values: dict) -> None:
    if db.configured():
        with db.session_scope() as session:
            row = session.get(Shop if role == "seller" else Creator, subject_id)
            if row is None:
                return
            for key, value in values.items():
                if value is not None and hasattr(row, key):
                    setattr(row, key, value)
        return
    row = db.memory_shops.get(subject_id) if role == "seller" else db.memory_creator_objects.get(subject_id)
    if row is None:
        return
    for key, value in values.items():
        if value is not None:
            setattr(row, key, value)


def _upsert_billing_row(role, subject_id, values, metadata) -> None:
    subscription_id = values.get("stripe_subscription_id")
    if db.configured():
        with db.session_scope() as session:
            row = None
            if subscription_id:
                row = session.scalar(
                    select(BillingSubscription).where(BillingSubscription.stripe_subscription_id == subscription_id)
                )
            if row is None:
                row = BillingSubscription(
                    id=str(uuid.uuid4()),
                    role=role,
                    shop_id=subject_id if role == "seller" else None,
                    creator_id=subject_id if role == "creator" else None,
                    plan_tier=values.get("plan_tier") or "starter",
                    status=values.get("subscription_status") or "inactive",
                    interval=values.get("billing_interval") or "month",
                    created_at=utcnow(),
                )
                session.add(row)
            row.stripe_customer_id = values.get("stripe_customer_id")
            row.stripe_subscription_id = subscription_id
            row.status = values.get("subscription_status") or row.status
            row.plan_tier = values.get("plan_tier") or row.plan_tier
            row.promo_code = str(_attr(metadata, "promo_code") or "") or row.promo_code
            row.invite_code = str(_attr(metadata, "invite_code") or "") or row.invite_code
            row.campaign = str(_attr(metadata, "campaign") or "") or row.campaign
            row.channel = str(_attr(metadata, "channel") or "") or row.channel
        return
    db.memory_billing.append({"role": role, "subject_id": subject_id, **values})


def _find_by_customer(customer_id: str):
    if not customer_id:
        return None
    if db.configured():
        with db.session_scope() as session:
            shop = session.scalar(select(Shop).where(Shop.stripe_customer_id == customer_id))
            if shop is not None:
                return "seller", shop.id
            creator = session.scalar(select(Creator).where(Creator.stripe_customer_id == customer_id))
            if creator is not None:
                return "creator", creator.id
        return None
    for shop_id, shop in db.memory_shops.items():
        if getattr(shop, "stripe_customer_id", None) == customer_id:
            return "seller", shop_id
    for creator_id, creator in db.memory_creator_objects.items():
        if getattr(creator, "stripe_customer_id", None) == customer_id:
            return "creator", creator_id
    return None


def _portal_email(role: str, subject_id: str) -> Optional[str]:
    if db.configured():
        with db.session_scope() as session:
            query = select(PortalAccount).where(PortalAccount.role == role)
            if role == "seller":
                query = query.where(PortalAccount.shop_id == subject_id)
            else:
                query = query.where(PortalAccount.creator_id == subject_id)
            row = session.scalars(query).first()
            return None if row is None else row.email
    for row in db.memory_portal_accounts:
        if row.role != role:
            continue
        if role == "seller" and row.shop_id == subject_id:
            return row.email
        if role == "creator" and row.creator_id == subject_id:
            return row.email
    return None


def _email_for_customer(customer_id: str) -> Optional[str]:
    found = _find_by_customer(customer_id)
    if found is None:
        return None
    return _portal_email(*found)


def _sales_this_month(creator_id: str) -> int:
    from datetime import datetime, timezone

    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = 0
    for order in db.memory_attributed_orders:
        if getattr(order, "creator_id", None) != creator_id:
            continue
        occurred = getattr(order, "occurred_at", None)
        if occurred is not None and occurred >= start:
            count += 1
    if db.configured():
        from models import AttributedOrder

        with db.session_scope() as session:
            rows = session.scalars(select(AttributedOrder).where(AttributedOrder.creator_id == creator_id)).all()
            count = sum(1 for row in rows if row.occurred_at >= start)
    return count


def _mark_converted(role: str, subject_id: str) -> None:
    if not role or not subject_id:
        return
    import pilots

    for membership in pilots._memberships():
        matches = pilots._get(membership, "shop_id") == subject_id if role == "seller" else pilots._get(membership, "creator_id") == subject_id
        if matches and pilots._get(membership, "converted_at") is None:
            pilots._set(membership, "converted_at", utcnow())
            pilots._set(membership, "stage", "converted")
            email = pilots._get(membership, "email")
            if email:
                import emailer

                emailer.send("converted", email, tier=role)


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


def _live(key: str) -> bool:
    return key.startswith("sk_live") or key.startswith("rk_live")


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
