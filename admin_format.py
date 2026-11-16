"""Plain-language labels, money, and Sydney dates for the operator pages.

Internal codes stay out of the page. A value with no label is turned into
words (underscores become spaces). Raw ids are not formatted here.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import billing_config
import taxonomy

SYDNEY = ZoneInfo("Australia/Sydney")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

STATUS_LABELS = {
    "pending": "Waiting for review",
    "approved": "Approved",
    "rejected": "Declined",
    "waitlisted": "Waitlisted",
    "active": "Active",
    "trialing": "Trial",
    "trial": "Trial",
    "past_due": "Payment overdue",
    "canceled": "Cancelled",
    "cancelled": "Cancelled",
    "incomplete": "Incomplete",
    "incomplete_expired": "Payment not finished",
    "unpaid": "Unpaid",
    "paused": "Paused",
    "free": "Free",
    "unsubscribed": "Not subscribed",
    "invited": "Invited",
    "inactive": "Not active",
    "open": "Open",
    "marked_paid": "Marked paid",
    "confirmed": "Payment confirmed",
    "disputed": "Disputed",
    "resolved": "Resolved",
    "pending_creator": "Waiting on the Creatr",
    "agreed": "Agreed",
    "deemed_agreed": "Treated as agreed",
    "disagreed": "Creator disagreed",
    "admin_corrected": "Corrected by Plantr",
    "admin_kept": "Kept as recorded",
    "offered": "Offered",
    "withdrawn": "Withdrawn",
    "ended": "Ended",
    "complete": "Complete",
    "draft": "Draft",
    "connected": "Connected",
    "not_connected": "Not connected",
    "pending_connection": "Waiting to connect",
    "uninstalled": "Uninstalled",
    "expired": "Expired",
    "revoked": "Revoked",
    "unknown": "Not known",
    "no_longer_true": "Claim removed",
    "accepted": "Accepted",
    "declined": "Declined",
    "waiting": "Waiting",
    "sent": "Sent",
    "not_sent": "Not sent",
    "overdue": "Overdue",
    "app": "Shopify app",
    "store_webhooks": "Store webhooks",
    "manual": "Added by hand",
    "csv": "CSV import",
    "api": "Added by the API",
    "shopify": "Shopify",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "other": "Other",
    "link": "Tracked link",
    "code": "Code",
    "ad": "Ad",
    "plantr": "Plantr",
    "balanced": "Balanced",
    "reach": "Reach",
    "sales": "Sales",
    "revenue": "Revenue",
    "grok": "Drafted with help",
    "fallback": "Drafted from the template",
    "system": "Plantr",
    "operator": "Operator",
    "seller": "Seller",
    "creator": "Creator",
    "admin": "Operator",
    "month": "Monthly",
    "year": "Yearly",
    "rules-v1": "Matching rules, version 1",
    "starter": "Starter",
    "growth": "Growth",
    "scale": "Scale",
    "free_tier": "Starter",
    "member": "Membr",
    "curator": "Curatr",
    "elite": "Trendsettr",
    "foundr": "Foundr",
    "founding_curators": "Foundrs",
    "seller_pilot": "Seller pilot",
}

EVENT_LABELS = {
    "shop_signed_up": "The seller joined",
    "creator_onboarded": "The creator joined",
    "profile_completed": "The profile was completed",
    "product_enriched": "A product was added or updated",
    "match_run": "A match was run",
    "match_accepted": "A match was accepted",
    "match_declined": "A match was declined",
    "brief_sent": "A brief was sent",
    "usage_limit_hit": "A plan limit was reached",
    "view_as_started": "An operator opened a read-only view",
    "view_as_ended": "An operator left the read-only view",
    "login_created": "A sign-in was created",
    "sign_in_reset": "An operator set a new password",
    "reset_link_issued": "A one-time reset link was created",
    "reset_link_used": "The password was set from a reset link",
    "owed_calculated": "A statement was calculated",
    "owed_marked_paid": "The seller marked a statement paid",
    "owed_confirmed": "The creator confirmed a payment",
    "owed_disputed": "The creator disputed a statement",
    "owed_dispute_resolved": "A dispute was closed",
    "owed_dispute_kept": "A dispute was kept open",
    "owed_overdue": "A statement became overdue",
    "correction_requested": "The seller asked to correct a payment record",
    "correction_agreed": "The creator agreed to a correction",
    "correction_deemed_agreed": "A correction was treated as agreed",
    "correction_disagreed": "The creator disagreed with a correction",
    "correction_corrected": "Plantr corrected a payment record",
    "correction_kept": "Plantr kept the payment record",
    "apply_submit": "An application was submitted",
    "catalogue_fee_threshold": "The catalogue passed the fee warning",
}

TONE = {
    "past_due": "bad",
    "overdue": "bad",
    "disputed": "bad",
    "rejected": "bad",
    "declined": "bad",
    "cancelled": "bad",
    "canceled": "bad",
    "unpaid": "bad",
    "incomplete": "warn",
    "incomplete_expired": "warn",
    "pending": "warn",
    "waitlisted": "warn",
    "trialing": "warn",
    "trial": "warn",
    "disagreed": "warn",
    "pending_creator": "warn",
    "active": "good",
    "approved": "good",
    "confirmed": "good",
    "complete": "good",
    "accepted": "good",
    "connected": "good",
    "paid": "good",
    "marked_paid": "good",
    "resolved": "neutral",
    "open": "neutral",
}


def is_uuid(value: Any) -> bool:
    return bool(UUID_RE.match(str(value or "").strip()))


def humanize(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Not set"
    key = text.lower()
    if key in STATUS_LABELS:
        return STATUS_LABELS[key]
    if is_uuid(text):
        return "Record"
    words = text.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Not set"


def status_label(value: Any) -> str:
    return humanize(value)


def badge_tone(value: Any) -> str:
    return TONE.get(str(value or "").strip().lower(), "neutral")


def plan_name(audience: str, tier: Any) -> str:
    label = billing_config.tier_label(audience, str(tier or ""))
    if label:
        mapped = STATUS_LABELS.get(label.strip().lower())
        if mapped:
            return mapped
        if label.strip().lower() in STATUS_LABELS:
            return STATUS_LABELS[label.strip().lower()]
        # tier_label already returns Starter / Membr when it knows the tier.
        if label != str(tier or ""):
            return label
    return humanize(tier)


def category_name(slug: Any) -> str:
    text = str(slug or "").strip().lower()
    if not text:
        return "Not set"
    for key, label in taxonomy.TOP_LEVEL_CATEGORIES:
        if key == text:
            return label
    return humanize(text)


def event_sentence(name: Any) -> str:
    key = str(name or "").strip()
    if key in EVENT_LABELS:
        return EVENT_LABELS[key]
    return humanize(key)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day, tzinfo=SYDNEY)
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def format_when(value: Any) -> str:
    """'16 Nov 2026, 8:21 pm AEDT'. A date with no clock stays a date."""
    if value is None or value == "":
        return "—"
    if isinstance(value, date) and not isinstance(value, datetime):
        return f"{value.day} {value.strftime('%b %Y')}, Australia/Sydney"
    moment = _as_datetime(value)
    if moment is None:
        return "—"
    local = moment.astimezone(SYDNEY)
    hour = int(local.strftime("%I"))
    minute = local.strftime("%M")
    ampm = local.strftime("%p").lower()
    zone = local.strftime("%Z") or "AEST"
    return f"{local.day} {local.strftime('%b %Y')}, {hour}:{minute} {ampm} {zone}"


def relative_age(value: Any, *, now: Optional[datetime] = None) -> str:
    moment = _as_datetime(value)
    if moment is None:
        return ""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = int((current - moment).total_seconds())
    future = seconds < 0
    span = abs(seconds)
    if span < 3600:
        text = "less than an hour"
    elif span < 86400:
        hours = max(1, span // 3600)
        text = "1 hour" if hours == 1 else f"{hours} hours"
    else:
        days = max(1, span // 86400)
        text = "1 day" if days == 1 else f"{days} days"
    if future:
        return f"in {text}"
    if text == "less than an hour":
        return "less than an hour ago"
    return f"{text} ago"


def waiting_phrase(value: Any, *, now: Optional[datetime] = None) -> str:
    phrase = relative_age(value, now=now)
    if not phrase:
        return "waiting"
    if phrase.endswith(" ago"):
        return "waiting " + phrase[: -len(" ago")]
    if phrase == "less than an hour ago":
        return "waiting less than an hour"
    return phrase


def due_phrase(value: Any, *, now: Optional[datetime] = None) -> str:
    moment = _as_datetime(value)
    if moment is None:
        return ""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = int((current - moment).total_seconds())
    if abs(seconds) < 3600:
        return "due now" if seconds >= 0 else "due in less than an hour"
    days = max(1, abs(seconds) // 86400)
    unit = "day" if days == 1 else "days"
    if seconds >= 0:
        return f"{days} {unit} overdue"
    return f"due in {days} {unit}"


def money(amount: Any, currency: Any = "AUD", *, kind: str = "") -> str:
    if amount is None or amount == "":
        return "—"
    try:
        number = float(amount)
    except (TypeError, ValueError):
        return "—"
    code = str(currency or "AUD").strip().upper() or "AUD"
    formatted = f"{number:,.2f}"
    body = f"A${formatted}" if code == "AUD" else f"{code} {formatted}"
    note = kind.strip()
    if note:
        return f"{body} {note}"
    return body


def deployment_name() -> str:
    import os

    env = os.getenv("ENV", "").strip().lower()
    railway = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip().lower()
    if env == "production" or railway == "production":
        return "production"
    if env == "staging" or railway == "staging":
        return "staging"
    if env:
        return env
    if railway:
        return railway
    return "local"


def show_staging_badge() -> bool:
    return deployment_name() != "production"


def show_test_tools() -> bool:
    return deployment_name() == "staging"


def stripe_mode() -> str:
    """test, live, missing, or unrecognised. The key itself is never returned."""
    import os

    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        return "missing"
    if key.startswith("sk_live") or key.startswith("rk_live"):
        return "live"
    if key.startswith("sk_test") or key.startswith("rk_test"):
        return "test"
    return "unrecognised"
