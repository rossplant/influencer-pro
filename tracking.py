"""Internal events first, then ad-platform fan-out.

Platform sends are off unless tracking is explicitly enabled, the environment
is not production, and the visitor has marketing consent. Internal rows are
always written. No raw email, phone, IP, or user agent is stored on the event.
"""

from __future__ import annotations

import hashlib
import logging
import os
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

import billing_config
import db
import events
from models import Event, TrackedEvent, utcnow

logger = logging.getLogger("plantr.tracking")

PII_KEYS = {
    "email",
    "phone",
    "password",
    "ip",
    "user_agent",
    "raw_email",
    "raw_phone",
}

# Tests replace this. The default never calls the network.
transport = None
outbound: list = []


def reset() -> None:
    global transport
    transport = None
    outbound.clear()


def enabled() -> bool:
    flag = os.getenv("TRACKING_ENABLED", "").strip().lower()
    return flag in {"1", "true", "yes"}


def production() -> bool:
    name = (os.getenv("ENV") or os.getenv("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
    return name == "production"


def staging() -> bool:
    name = (os.getenv("ENV") or os.getenv("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
    return name == "staging"


def consent_from_scope(scope: dict) -> bool:
    for name, value in scope.get("headers") or []:
        if name.lower() != b"cookie":
            continue
        header = value.decode("utf-8", "ignore")
        return "plantr_consent=marketing" in header.replace(" ", "")
    return False


def hash_email(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def hash_phone(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    return hashlib.sha256(digits.encode()).hexdigest()


def track(
    key: str,
    *,
    event_id: str,
    role: Optional[str] = None,
    entity: Any = None,
    entity_type: str = "visitor",
    entity_id: str = "public",
    shop_id: Optional[str] = None,
    actor: str = "system",
    consent: bool = False,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    subject_key: Optional[str] = None,
    touch: Optional[dict] = None,
    **props: Any,
) -> dict:
    """Write one internal event, then fan out only when the spec and consent allow it."""
    name = billing_config.event_name(key)
    spec = billing_config.PLATFORM_EVENTS[key]
    if not _reserve(event_id, subject_key, name):
        return {"event_id": event_id, "event_name": name, "duplicate": True, "sent": False}
    clean = {key_name: value for key_name, value in props.items() if key_name not in PII_KEYS and value is not None}
    clean["event_id"] = event_id
    if role:
        clean["role"] = role
    if touch:
        first = (touch.get("touch") or touch).get("first") or {}
        last = (touch.get("touch") or touch).get("last") or {}
        if first:
            clean["utm_first"] = {field: first.get(field) for field in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term") if first.get(field)}
        if last:
            clean["utm_last"] = {field: last.get(field) for field in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term") if last.get(field)}
            for click in ("fbc", "fbp", "ttclid", "ttp", "gclid", "epik", "li_fat_id"):
                if last.get(click):
                    clean[click] = last[click]
    target = entity if entity is not None else _detached(entity_type, entity_id, shop_id)
    events.emit(name, target, actor, **clean)
    sent = False
    if spec.get("internal") or not spec.get("server"):
        return {"event_id": event_id, "event_name": name, "duplicate": False, "sent": False}
    if consent and enabled() and not production():
        payload = _platform_payload(
            key,
            name,
            event_id,
            clean,
            email=email,
            phone=phone,
            ip=ip,
            user_agent=user_agent,
        )
        _dispatch(payload)
        sent = True
    return {"event_id": event_id, "event_name": name, "duplicate": False, "sent": sent}


def _detached(entity_type: str, entity_id: str, shop_id: Optional[str]):
    if db.configured():
        return SimpleNamespace(emit_detached=True, id=entity_id, entity_type=entity_type, shop_id=shop_id)
    return SimpleNamespace(memory_kind="tracking", id=entity_id, entity_type=entity_type, shop_id=shop_id)


def _reserve(event_id: str, subject_key: Optional[str], name: str) -> bool:
    if not event_id:
        return False
    if db.configured():
        try:
            with db.session_scope() as session:
                session.add(TrackedEvent(event_id=event_id, subject_key=subject_key, event_name=name, created_at=utcnow()))
                session.flush()
        except IntegrityError:
            return False
        return True
    if event_id in db.memory_tracked_event_ids:
        return False
    if subject_key and subject_key in db.memory_tracked_subjects:
        return False
    db.memory_tracked_event_ids.add(event_id)
    if subject_key:
        db.memory_tracked_subjects.add(subject_key)
    return True


def _platform_payload(key, name, event_id, clean, *, email, phone, ip, user_agent) -> dict:
    spec = billing_config.PLATFORM_EVENTS[key]
    payload = {
        "event_name": name,
        "event_id": event_id,
        "meta_event": spec.get("meta"),
        "tiktok_event": spec.get("tiktok"),
        "custom": bool(spec.get("custom")),
        "google": False,
        "pinterest": False,
        "linkedin": False,
        "properties": {item: clean[item] for item in clean if item not in PII_KEYS},
    }
    if email:
        payload["hashed_email"] = hash_email(email)
    if phone:
        payload["hashed_phone"] = hash_phone(phone)
    if ip:
        payload["ip"] = ip
    if user_agent:
        payload["user_agent"] = user_agent
    if staging():
        payload["tiktok_test_event_code"] = os.getenv("TIKTOK_TEST_EVENT_CODE", "").strip()
        payload["meta_test_event_code"] = os.getenv("META_TEST_EVENT_CODE", "").strip()
    pixel = os.getenv("TIKTOK_PIXEL_ID", "").strip()
    meta = os.getenv("META_PIXEL_ID", "").strip()
    if pixel:
        payload["tiktok_pixel_id"] = pixel
    if meta:
        payload["meta_pixel_id"] = meta
    if os.getenv("GOOGLE_TAG_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
        payload["google"] = True
    if os.getenv("PINTEREST_CAPI_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
        payload["pinterest"] = True
    if os.getenv("LINKEDIN_CAPI_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
        payload["linkedin"] = True
    return payload


def _dispatch(payload: dict) -> None:
    """Hand the payload to a transport. The default transport is not installed."""
    if transport is None:
        logger.info("tracking platform send skipped event=%s", payload.get("event_name"))
        return
    transport(payload)
    outbound.append(payload)


def pixel_snippet(consent: bool) -> str:
    """Browser tags render only when tracking is on, consent is set, and an id exists."""
    if not consent or not enabled() or production():
        return ""
    parts = []
    tiktok = os.getenv("TIKTOK_PIXEL_ID", "").strip()
    meta = os.getenv("META_PIXEL_ID", "").strip()
    if tiktok:
        parts.append(f"<!-- tiktok {tiktok} -->")
    if meta:
        parts.append(f"<!-- meta {meta} -->")
    return "".join(parts)
