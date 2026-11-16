"""Stripe Billing subscriptions for sellers and creators.

Connect, transfers, and payouts are not used. The app starts when Stripe is
unset. Live secret keys are refused. The sandbox setup script reads
STRIPE_SECRET_KEY_TEST and will not use a live key.
"""

from __future__ import annotations

import json
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
LIVE_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due"}
ALREADY_SUBSCRIBED = "You already have a subscription. Use Manage to change plan, update payment, or cancel."
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


def pilot_trial_end(role: str, subject_id: str) -> Optional[int]:
    """Free until the shared pilot end. Stripe needs the trial to end at least 48 hours ahead."""
    from datetime import timedelta

    account = _account(role, subject_id) or {}
    slug = str(account.get("signup_campaign") or "")
    if slug not in billing_config.PILOT_COHORTS:
        return None
    import pilots

    cohort = pilots._cohort(slug)
    end = None if cohort is None else getattr(cohort, "ends_at", None)
    if end is None:
        return None
    if end.tzinfo is None:
        from datetime import timezone

        end = end.replace(tzinfo=timezone.utc)
    if end <= utcnow() + timedelta(hours=48):
        return None
    return int(end.timestamp())


SUBSCRIBE_PROMPT = (
    "Subscribe to add products, create campaigns, run matches, or accept offers. "
    "Pilot suppliers and Foundrs keep that access during the pilot, 20 November 2026 to 20 January 2027."
)


def commerce_open(role: str, subject_id: str) -> bool:
    return commerce_open_for(role, _account(role, subject_id))


def commerce_open_for(role: str, row: Optional[dict]) -> bool:
    """A live subscription or the current pilot. Everyone else is unsubscribed."""
    row = row or {}
    status = str(row.get("subscription_status") or "").strip().lower()
    if status in LIVE_SUBSCRIPTION_STATUSES:
        return True
    now = utcnow()
    if now < billing_config.pilot_start() or now > billing_config.pilot_end():
        return False
    if role == "seller":
        return bool(row.get("is_pilot")) or row.get("signup_campaign") == "seller_pilot"
    return row.get("signup_campaign") == "founding_curators" or row.get("founding_number") not in (None, "")


def subscription_in_force(role: str, subject_id: str) -> bool:
    """True when another Checkout would create a second subscription."""
    status = str((_account(role, subject_id) or {}).get("subscription_status") or "").strip().lower()
    return status in LIVE_SUBSCRIPTION_STATUSES


def shop_status(shop_id: str) -> dict:
    row = _account("seller", shop_id)
    return _status("seller", "starter", None if row is None else {**row, "_subject_id": shop_id})


def creator_status(creator_id: str) -> dict:
    row = _account("creator", creator_id)
    return _status("creator", "member", None if row is None else {**row, "_subject_id": creator_id})


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
    """Create a Checkout Session.

    Eligible pilot suppliers and Foundrs get PILOTGROWTH or FOUNDR9 without typing
    it. During the pilot the code is attached by a subscription schedule after
    the free period ends, so none of its months are used up by the trial. After
    the pilot it is attached at Checkout. Customers cannot type codes on the
    Stripe page, because Stripe cannot limit a code to one interval.
    """
    if subscription_in_force(role, subject_id):
        raise HTTPException(status_code=409, detail=ALREADY_SUBSCRIBED)
    email = _portal_email(role, subject_id)
    if not email:
        raise HTTPException(status_code=422, detail="A login email is required before checkout")
    lookup = billing_config.lookup_key(role, tier, interval)
    if not lookup:
        raise HTTPException(status_code=422, detail="That plan does not have a Stripe price")
    account = _account(role, subject_id) or {}
    offer = billing_config.pilot_offer_code(role, account, tier, interval)
    typed = promo_code.strip().upper()
    if typed and typed != offer:
        if typed in billing_config.RETIRED_PROMOTION_CODES:
            raise HTTPException(status_code=422, detail="That promotion code is no longer active.")
        if typed in billing_config.PROMOTION_CODES:
            if not billing_config.promo_allows(typed, role=role, tier=tier, interval=interval):
                raise HTTPException(status_code=422, detail=_restriction_message(typed))
            raise HTTPException(status_code=422, detail="That promotion code does not apply to this account.")
    code = offer or typed
    promotion_id = _resolve_promotion_code(code, role) if code else ""
    customer_id = ensure_customer(role, subject_id, email=email, name=email)
    price_id = _price_id(lookup)
    known = billing_config.promo_spec(code)
    metadata = {
        "role": role,
        "subject_id": subject_id,
        "tier": tier,
        "interval": interval,
        "promo_code": code,
        "invite_code": invite_code,
        "campaign": campaign or known.get("campaign", ""),
        "channel": channel or known.get("channel", ""),
    }
    subscription_data = {"metadata": dict(metadata)}
    if trial_end:
        subscription_data["trial_end"] = trial_end
        subscription_data["trial_settings"] = {"end_behavior": {"missing_payment_method": "cancel"}}
        if promotion_id:
            # Attached at the end of the free period by ensure_pilot_schedule.
            subscription_data["metadata"]["pilot_offer"] = code
            subscription_data["metadata"]["pilot_promotion_code"] = promotion_id
    payload = {
        "mode": "subscription",
        "customer": customer_id,
        "client_reference_id": subject_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items": [{"price": price_id, "quantity": 1}],
        "metadata": metadata,
        "subscription_data": subscription_data,
        "custom_text": {"submit": {"message": checkout_disclosure(role, tier, interval, offer=code, trial_end=trial_end)[:1200]}},
    }
    if promotion_id and not trial_end:
        payload["discounts"] = [{"promotion_code": promotion_id}]
    session = client().checkout.sessions.create(payload)
    url = _attr(session, "url")
    if not url:
        raise HTTPException(status_code=502, detail="Stripe did not return a Checkout URL")
    return str(url)


def _restriction_message(code: str) -> str:
    spec = billing_config.promo_spec(code)
    label = billing_config.tier_label(spec.get("role", ""), spec.get("tier", ""))
    period = "monthly" if spec.get("interval") == "month" else "annual"
    return f"{code} applies only to {period} {label}."


def checkout_disclosure(role: str, tier: str, interval: str, *, offer: str = "", trial_end: Optional[int] = None) -> str:
    """Wording from 09_Signup_Acceptance_Wording section 10, filled in for this plan."""
    from datetime import datetime, timezone

    spec = billing_config.tier_spec(role, tier)
    if not spec:
        return ""
    unit = "year" if interval == "year" else "month"
    price_cents = spec["amount_year_cents"] if unit == "year" else spec["amount_month_cents"]
    price = billing_config.amount_label(price_cents)
    offer_spec = billing_config.promo_spec(offer) if offer else {}
    if trial_end:
        first = datetime.fromtimestamp(int(trial_end), tz=timezone.utc)
        first_label = billing_config.sydney_date_label(first)
        if offer_spec:
            discounted = billing_config.amount_label(offer_spec["discounted_month_cents"])
            months = int(offer_spec["months"])
            full_from = billing_config.sydney_date_label(billing_config.add_months(first, months))
            applied = f" Code {offer} applied." if offer == "FOUNDR9" else ""
            return (
                f"No charge today.{applied} Your first charge of {discounted} (ex GST) will be on {first_label}. "
                f"You will pay {discounted} a month for {months} months, then {price} a month from {full_from}, "
                f"renewing automatically until you cancel. Cancel before {first_label} and you will not be charged."
            )
        return (
            f"No charge today. Your first charge of {price} (ex GST) will be on {first_label}. "
            f"You will then pay {price} per {unit}, renewing automatically until you cancel. "
            f"Cancel before {first_label} and you will not be charged."
        )
    standard = (
        f"You are subscribing to {spec['label']} at {price} per {unit}, excluding GST. "
        "No GST is charged while we are not registered for GST. If we register, we will give you 30 days' notice before adding GST. "
        f"Your plan renews automatically and we will charge your payment method every {unit} until you cancel. "
        "Cancel any time in Manage billing; cancellation takes effect at the end of your current period. "
        "No refunds for change of mind on part periods, but your rights under the Australian Consumer Law are not affected."
    )
    if offer_spec:
        discounted = billing_config.amount_label(offer_spec["discounted_month_cents"])
        months = int(offer_spec["months"])
        full_from = billing_config.sydney_date_label(billing_config.add_months(utcnow(), months))
        standard += f" {discounted} per month for {months} months, then {price} per month from {full_from}."
    return standard


def disclosures(role: str, subject_id: str) -> dict:
    """The statement shown next to Subscribe for every plan, plus the main one for this account."""
    account = _account(role, subject_id) or {}
    trial_end = pilot_trial_end(role, subject_id) if subject_id else None
    table = billing_config.SELLER_TIERS if role == "seller" else billing_config.CREATOR_TIERS
    rows = []
    main = ""
    for tier, spec in table.items():
        for interval in ("month", "year"):
            offer = billing_config.pilot_offer_code(role, account, tier, interval)
            text = checkout_disclosure(role, tier, interval, offer=offer, trial_end=trial_end)
            rows.append({"tier": tier, "interval": interval, "label": spec["label"], "text": text, "offer": offer})
            if offer and not main:
                main = text
    if not main and trial_end:
        from datetime import datetime, timezone

        day = billing_config.sydney_date_label(datetime.fromtimestamp(int(trial_end), tz=timezone.utc))
        main = f"No charge today. Your first charge will be on {day}. Cancel before {day} and you will not be charged."
    return {"main": main, "plans": rows, "terms": "Seller Terms" if role == "seller" else "Creator and Foundr Terms"}


def record_checkout_acceptance(role: str, subject_id: str, *, tier: str, interval: str, trial_end, ip=None, user_agent=None) -> None:
    import legal

    account = _account(role, subject_id) or {}
    offer = billing_config.pilot_offer_code(role, account, tier, interval)
    text = checkout_disclosure(role, tier, interval, offer=offer, trial_end=trial_end)
    terms = "Seller Terms" if role == "seller" else "Creator and Foundr Terms"
    authorise = legal.CHECKOUT_AUTHORISE.format(terms=terms)
    legal.record(
        subject_type="shop" if role == "seller" else "creator",
        subject_id=subject_id,
        context=f"checkout:{tier}:{interval}",
        documents={"seller_terms" if role == "seller" else "creator_and_foundr_terms": legal.TERMS_VERSION},
        texts=[text, authorise],
        price_shown=text,
        confirmations={"authorise_recurring": True, "offer": offer, "trial_end": trial_end},
        ip=ip,
        user_agent=user_agent,
    )


def ensure_pilot_schedule(subscription: Any) -> dict:
    """Attach the post-pilot code from the end of the free period.

    Phases: the free period as it is, then the plan with PILOTGROWTH or FOUNDR9
    for its 3 or 12 monthly invoices, then the plan at full price. The schedule
    is released after the last phase. Safe to call again.
    """
    metadata = _attr(subscription, "metadata") or {}
    code = str(_attr(metadata, "pilot_offer") or "")
    subscription_id = str(_attr(subscription, "id") or "")
    if not code or not subscription_id:
        return {"scheduled": False, "reason": "no pilot offer"}
    spec = billing_config.promo_spec(code)
    if not spec:
        return {"scheduled": False, "reason": "unknown code"}
    status = str(_attr(subscription, "status") or "")
    trial_end = _attr(subscription, "trial_end")
    if status != "trialing" or not trial_end:
        return {"scheduled": False, "reason": "not in the free period"}
    existing = _attr(subscription, "schedule")
    if existing:
        return {"scheduled": True, "schedule": str(_attr(existing, "id") or existing), "existing": True}
    promotion_id = str(_attr(metadata, "pilot_promotion_code") or "") or _resolve_promotion_code(code, spec["role"])
    stripe_client = client()
    schedule = stripe_client.subscription_schedules.create({"from_subscription": subscription_id})
    schedule_id = _id_of(schedule)
    phases = _list_data(_attr(schedule, "phases")) or list(_attr(schedule, "phases") or [])
    current = phases[0] if phases else {}
    items = []
    for item in _list_data(_attr(current, "items")) or list(_attr(current, "items") or []):
        price = _attr(item, "price")
        items.append({"price": str(_attr(price, "id") or price), "quantity": int(_attr(item, "quantity") or 1)})
    if not items:
        price_id = _price_id(billing_config.lookup_key(spec["role"], spec["tier"], spec["interval"]))
        items = [{"price": price_id, "quantity": 1}]
    phase_meta = {str(k): str(v) for k, v in dict(metadata).items()}
    stripe_client.subscription_schedules.update(
        schedule_id,
        {
            "end_behavior": "release",
            "proration_behavior": "none",
            "phases": [
                {
                    "items": items,
                    "start_date": int(_attr(current, "start_date") or _attr(subscription, "start_date") or utcnow().timestamp()),
                    "end_date": int(trial_end),
                    "trial_end": int(trial_end),
                    "metadata": phase_meta,
                },
                {
                    "items": items,
                    "duration": {"interval": "month", "interval_count": int(spec["months"])},
                    "discounts": [{"promotion_code": promotion_id}],
                    "metadata": phase_meta,
                },
                {"items": items, "metadata": phase_meta},
            ],
        },
    )
    from datetime import datetime, timezone

    trial_moment = datetime.fromtimestamp(int(trial_end), tz=timezone.utc)
    discount_end = billing_config.add_months(trial_moment, int(spec["months"]))
    found = _find_by_customer(_customer_id(subscription))
    role = str(_attr(metadata, "role") or (found[0] if found else ""))
    subject_id = str(_attr(metadata, "subject_id") or (found[1] if found else ""))
    if role and subject_id:
        _upsert_billing_row(
            role,
            subject_id,
            {
                "stripe_customer_id": _customer_id(subscription) or None,
                "stripe_subscription_id": subscription_id,
                "subscription_status": status,
                "plan_tier": str(_attr(metadata, "tier") or spec["tier"]),
                "billing_interval": spec["interval"],
                "trial_end": trial_moment,
                "discount_end": discount_end,
            },
            {**dict(metadata), "promo_code": code},
        )
    logger.info("pilot schedule %s attached to %s with %s", schedule_id, subscription_id, code)
    return {"scheduled": True, "schedule": schedule_id, "code": code, "discount_end": discount_end.isoformat()}


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
        # for_json turns Decimal fields such as unit_amount_decimal into strings.
        # A plain to_dict() cannot be stored in the JSON payload column.
        try:
            data = event.to_dict(for_json=True)
        except TypeError:
            data = event.to_dict()
    else:
        data = dict(event)
    return _json_safe(data)


def record_event(event: dict) -> bool:
    """Store the event and report whether the caller should apply it.

    A duplicate that was stored but not applied is applied again. A duplicate
    that was already applied is skipped.
    """
    event_id = str(event.get("id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="Stripe event id is missing")
    event = _json_safe(event)
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
            with db.session_scope() as session:
                row = session.scalar(select(StripeEvent).where(StripeEvent.stripe_event_id == event_id))
                return row is not None and not row.processed
        return True
    stored = db.memory_stripe_events.get(event_id)
    if stored is not None:
        return not stored.get("processed")
    db.memory_stripe_events[event_id] = {"processed": False, "event_type": event.get("type")}
    return True


def apply_event(event: dict) -> None:
    """Apply a webhook. An unexpected payload is logged and skipped, not raised."""
    try:
        event_type = str(event.get("type") or "")
        data_object = ((event.get("data") or {}).get("object") or {})
        if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            _apply_subscription(data_object, deleted=event_type.endswith("deleted"))
            if event_type != "customer.subscription.deleted" and configured():
                try:
                    ensure_pilot_schedule(data_object)
                except Exception:
                    logger.exception("pilot schedule could not be attached; the reminder job retries")
        elif event_type == "checkout.session.completed":
            _apply_checkout(data_object)
        elif event_type == "invoice.paid":
            _apply_invoice_paid(data_object)
        elif event_type == "invoice.payment_failed":
            logger.info(
                "invoice payment failed invoice=%s subscription=%s",
                _attr(data_object, "id"),
                _subscription_id_from_invoice(data_object),
            )
        elif event_type == "customer.subscription.trial_will_end":
            _apply_trial_ending(data_object)
        _mark_processed(str(event.get("id") or ""))
    except Exception:
        logger.exception("stripe event %s (%s) could not be applied", event.get("id"), event.get("type"))


def apply_sale_waiver(creator_id: str) -> dict:
    """Manual trigger. Curatr only. A month with 5 or more attributed sales waives the next invoice.

    The waiver is a customer balance credit equal to the next invoice, so it
    stacks with FOUNDR9 instead of replacing it. A Foundr's next A$9 invoice is
    covered and the FOUNDR9 discount stays on the subscription.
    """
    account = _account("creator", creator_id) or {}
    tier = str(account.get("plan_tier") or "").strip().lower()
    status = str(account.get("subscription_status") or "").strip().lower()
    if tier not in billing_config.WAIVER_TIERS:
        raise HTTPException(status_code=422, detail="The 5-sale waiver applies to Curatr only")
    if status not in {"active", "past_due"}:
        raise HTTPException(status_code=422, detail="The waiver needs a paid Curatr subscription")
    sales = _sales_this_month(creator_id)
    if sales < billing_config.WAIVER_SALES:
        raise HTTPException(status_code=422, detail=f"Need {billing_config.WAIVER_SALES} attributed sales this month")
    if not configured():
        return {"applied": False, "reason": NOT_CONFIGURED, "sales": sales}
    customer_id = account.get("stripe_customer_id")
    subscription_id = account.get("stripe_subscription_id")
    if not customer_id or not subscription_id:
        raise HTTPException(status_code=422, detail="Creator has no Stripe subscription")
    from datetime import datetime, timezone

    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    stripe_client = client()
    preview = stripe_client.invoices.create_preview({"customer": customer_id, "subscription": subscription_id})
    due = _attr(preview, "amount_due")
    amount = int(due if due is not None else (_attr(preview, "total") or 0))
    if amount <= 0:
        return {"applied": False, "reason": "next invoice is already A$0", "sales": sales}
    stripe_client.customers.balance_transactions.create(
        customer_id,
        {
            "amount": -amount,
            "currency": billing_config.CURRENCY,
            "description": f"Curatr 5-sale waiver for {month_key}",
            "metadata": {"waiver": "curatr_5_sales", "month": month_key, "creator_id": creator_id},
        },
        {"idempotency_key": f"curatr-waiver-{creator_id}-{month_key}"},
    )
    return {"applied": True, "sales": sales, "credit_cents": amount, "month": month_key, "stacks_with_discount": True}


def grant_elite(creator_id: str) -> dict:
    """Admin recognition: Trendsettr at no charge for 3 months."""
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
        payment_status = str(_attr(session_obj, "payment_status") or "")
        paid = payment_status in {"paid", "no_payment_required"}
        _save_account(
            role,
            subject_id,
            {
                "stripe_customer_id": customer_id or None,
                "stripe_subscription_id": subscription_id or None,
                "plan_tier": str(_attr(metadata, "tier") or ""),
                "billing_interval": str(_attr(metadata, "interval") or "month"),
                "subscription_status": "active" if paid else "trialing",
                "plan_status": "active" if paid else "trial",
            },
        )


def _apply_subscription(subscription: Any, *, deleted: bool) -> None:
    metadata = _attr(subscription, "metadata") or {}
    role = str(_attr(metadata, "role") or "")
    subject_id = str(_attr(metadata, "subject_id") or "")
    if not role or not subject_id:
        found = _find_by_customer(_customer_id(subscription))
        if found is None:
            return
        role, subject_id = found
    status = "canceled" if deleted else str(_attr(subscription, "status") or "")
    tier = str(_attr(metadata, "tier") or "")
    if status in {"canceled", "incomplete_expired", "unpaid"}:
        tier = "starter" if role == "seller" else "member"
        plan_status = "unsubscribed"
    elif status == "trialing":
        plan_status = "trial"
    else:
        plan_status = "active" if status == "active" else (status or "active")
    _period_start, period_end = _subscription_period(subscription)
    values = {
        "stripe_customer_id": _customer_id(subscription) or None,
        "stripe_subscription_id": str(_attr(subscription, "id") or "") or None,
        "subscription_status": status,
        "plan_status": plan_status,
        "billing_interval": _subscription_interval(subscription, metadata),
        "current_period_end": period_end,
    }
    if tier:
        values["plan_tier"] = tier
    _save_account(role, subject_id, values)
    trial_end = _epoch(_attr(subscription, "trial_end"))
    billing_values = {**values, "trial_end": trial_end}
    code = str(_attr(metadata, "promo_code") or "")
    spec = billing_config.promo_spec(code)
    if spec and not deleted:
        # A pilot schedule starts the discount at the end of the free period.
        # A direct Checkout discount starts with the subscription.
        begins = trial_end if _attr(metadata, "pilot_offer") and trial_end else _epoch(_attr(subscription, "start_date"))
        if begins is not None:
            billing_values["discount_end"] = billing_config.add_months(begins, int(spec["months"]))
    _upsert_billing_row(role, subject_id, billing_values, metadata)


def set_marketing_consent(role: str, subject_id: str, allowed: bool) -> None:
    _save_account(role, subject_id, {"marketing_consent": bool(allowed)})


def account_context(role: str, subject_id: str) -> dict:
    row = _account(role, subject_id) or {}
    return {
        "signup_code": row.get("signup_code") or "",
        "signup_campaign": row.get("signup_campaign") or "",
        "marketing_consent": bool(row.get("marketing_consent")),
        "attribution_first": row.get("attribution_first") or {},
        "attribution_last": row.get("attribution_last") or {},
    }


def _resolve_promotion_code(code: str, role: str) -> str:
    """Turn a code such as FOUNDR9 into the promo_ id Stripe requires."""
    cleaned = code.strip()
    try:
        listed = client().promotion_codes.list({"code": cleaned, "active": True, "limit": 1})
    except Exception as exc:
        logger.info("promotion code lookup failed")
        raise HTTPException(status_code=422, detail="That promotion code could not be checked. Try again.") from exc
    rows = _promo_rows(listed)
    if not rows:
        raise HTTPException(status_code=422, detail="That promotion code is not recognised or is no longer active.")
    promo = rows[0]
    expected = _promo_role(cleaned, promo)
    if expected and expected != role:
        raise HTTPException(status_code=422, detail="That promotion code does not apply to this account.")
    promo_id = str(_attr(promo, "id") or "")
    if not promo_id:
        raise HTTPException(status_code=422, detail="That promotion code could not be used.")
    return promo_id


def _promo_rows(result) -> list:
    if result is None:
        return []
    if hasattr(result, "data") and result.data is not None:
        return list(result.data)
    if isinstance(result, dict):
        return list(result.get("data") or [])
    return []


def _promo_role(code: str, promo) -> str:
    spec = billing_config.promo_spec(code)
    if spec.get("role"):
        return spec["role"]
    campaign = ""
    if spec:
        campaign = spec.get("campaign") or ""
    metadata = _attr(promo, "metadata") or {}
    campaign = campaign or str(_attr(metadata, "campaign") or "")
    if campaign == "founding_curators":
        return "creator"
    if campaign == "seller_pilot":
        return "seller"
    coupon = str(_attr(promo, "coupon") or "")
    if "founding_curator" in coupon:
        return "creator"
    if "seller_growth" in coupon:
        return "seller"
    return ""


def _period_label(interval: str) -> str:
    cleaned = (interval or "").strip().lower()
    if cleaned == "month":
        return "monthly"
    if cleaned == "year":
        return "annual"
    return cleaned


def _apply_trial_ending(subscription: Any) -> None:
    customer_id = _customer_id(subscription)
    email = _email_for_customer(customer_id) if customer_id else None
    if not email:
        logger.info("trial ending for customer=%s with no portal email", customer_id)
        return
    # Reminders are scheduled by reminders.py. trial_will_end only fires about
    # 3 days ahead, which is later than the terms promise.
    logger.info("trial_will_end for customer=%s; reminders.py already scheduled the notices", customer_id)


def _apply_invoice_paid(invoice: Any) -> None:
    amount = _attr(invoice, "amount_paid")
    if not amount:
        return
    subscription_id = _subscription_id_from_invoice(invoice)
    invoice_id = str(_attr(invoice, "id") or "")
    currency = str(_attr(invoice, "currency") or "")
    metadata = _invoice_metadata(invoice)
    role = str(_attr(metadata, "role") or "")
    subject_id = str(_attr(metadata, "subject_id") or "")
    tier = str(_attr(metadata, "tier") or "")
    interval = str(_attr(metadata, "interval") or "")
    found = _find_by_customer(_customer_id(invoice))
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


def _json_safe(value: Any) -> Any:
    """Return a structure json.dumps can encode. Stripe leaves some amounts as Decimal."""
    return json.loads(json.dumps(value, default=str))


def _customer_id(obj: Any) -> str:
    customer = _attr(obj, "customer")
    if isinstance(customer, str):
        return customer
    return str(_attr(customer, "id") or "")


def _subscription_id_from_invoice(invoice: Any) -> str:
    """Old invoices use invoice.subscription. Basil and later use parent.subscription_details."""
    direct = _attr(invoice, "subscription")
    if isinstance(direct, str) and direct:
        return direct
    nested = str(_attr(direct, "id") or "")
    if nested:
        return nested
    parent = _attr(invoice, "parent") or {}
    details = _attr(parent, "subscription_details") or {}
    subscription = _attr(details, "subscription")
    if isinstance(subscription, str) and subscription:
        return subscription
    return str(_attr(subscription, "id") or "")


def _subscription_period(subscription: Any):
    """Period timestamps moved onto subscription items in the basil API."""
    start = _epoch(_attr(subscription, "current_period_start"))
    end = _epoch(_attr(subscription, "current_period_end"))
    if start or end:
        return start, end
    for item in _list_data(_attr(subscription, "items")):
        start = _epoch(_attr(item, "current_period_start"))
        end = _epoch(_attr(item, "current_period_end"))
        if start or end:
            return start, end
    return None, None


def _subscription_interval(subscription: Any, metadata: Any) -> str:
    explicit = str(_attr(metadata, "interval") or "")
    if explicit:
        return explicit
    for item in _list_data(_attr(subscription, "items")):
        price = _attr(item, "price") or {}
        recurring = _attr(price, "recurring") or {}
        interval = _attr(recurring, "interval") or _attr(item, "interval")
        if interval:
            return str(interval)
        plan = _attr(item, "plan") or {}
        if _attr(plan, "interval"):
            return str(_attr(plan, "interval"))
    return "month"


def _invoice_metadata(invoice: Any):
    """Metadata lives on the invoice, the first line, or parent.subscription_details."""
    parent = _attr(invoice, "parent") or {}
    details = _attr(parent, "subscription_details") or {}
    parent_meta = _attr(details, "metadata")
    if parent_meta:
        return parent_meta
    for line in _list_data(_attr(invoice, "lines")):
        line_meta = _attr(line, "metadata")
        if line_meta:
            return line_meta
    return _attr(invoice, "metadata") or {}


def _line_price_id(line: Any) -> str:
    """Old lines use price.id. Current lines use pricing.price_details.price."""
    price = _attr(line, "price")
    if isinstance(price, str) and price:
        return price
    nested = str(_attr(price, "id") or "")
    if nested:
        return nested
    pricing = _attr(line, "pricing") or {}
    details = _attr(pricing, "price_details") or {}
    return str(_attr(details, "price") or "")


def _list_data(container: Any) -> list:
    if container is None:
        return []
    data = _attr(container, "data")
    if isinstance(data, list):
        return data
    if isinstance(container, list):
        return container
    return []


def _epoch(value: Any):
    from datetime import datetime, timezone

    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


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
        "plan_status": _public_plan_status(row.get("plan_status")),
        "billing_interval": row.get("billing_interval"),
        "plans": plans(role),
        "publishable_key": publishable_key(),
        "commerce_open": commerce_open_for(role, row),
        "disclosures": disclosures(role, row.get("_subject_id") or ""),
    }


def _public_plan_status(status: Optional[str]) -> str:
    cleaned = (status or "unsubscribed").strip().lower()
    if cleaned in {"free", "invited"}:
        return "unsubscribed"
    return cleaned or "unsubscribed"


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
        "is_pilot": bool(getattr(row, "is_pilot", False)),
        "founding_number": getattr(row, "founding_number", None),
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
            if values.get("current_period_end") is not None:
                row.current_period_end = values["current_period_end"]
            if values.get("billing_interval"):
                row.interval = values["billing_interval"]
            if values.get("trial_end") is not None:
                row.trial_end = values["trial_end"]
            if values.get("discount_end") is not None:
                row.discount_end = values["discount_end"]
        return
    merged = {"role": role, "subject_id": subject_id, **{k: v for k, v in values.items() if v is not None}}
    merged["promo_code"] = str(_attr(metadata, "promo_code") or "") or None
    for stored in db.memory_billing:
        if subscription_id and stored.get("stripe_subscription_id") == subscription_id:
            stored.update({k: v for k, v in merged.items() if v is not None})
            return
    db.memory_billing.append(merged)


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
        if getattr(order, "creator_id", None) != creator_id or not getattr(order, "attributed", True):
            continue
        occurred = getattr(order, "occurred_at", None)
        if occurred is not None and occurred >= start:
            count += 1
    if db.configured():
        from models import AttributedOrder

        with db.session_scope() as session:
            rows = session.scalars(select(AttributedOrder).where(AttributedOrder.creator_id == creator_id)).all()
            count = sum(1 for row in rows if row.attributed and row.occurred_at >= start)
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
