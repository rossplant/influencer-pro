"""Internal events first, then ad-platform fan-out.

Platform sends are off unless tracking is explicitly enabled, the environment
is not production, and the visitor has marketing consent. Internal rows are
always written. No raw email, phone, IP, or user agent is stored on the event.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import time
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
pending_sends: list = []


def reset() -> None:
    global transport
    transport = None
    outbound.clear()
    pending_sends.clear()


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
    _copy_touch(clean, touch)
    target = entity if entity is not None else _detached(entity_type, entity_id, shop_id)
    events.emit(name, target, actor, **clean)
    if spec.get("internal") or not spec.get("server"):
        return {"event_id": event_id, "event_name": name, "duplicate": False, "sent": False}
    if consent and enabled() and not production():
        pending_sends.append(
            _platform_payload(
                key,
                name,
                event_id,
                clean,
                email=email,
                phone=phone,
                ip=ip,
                user_agent=user_agent,
            )
        )
    return {"event_id": event_id, "event_name": name, "duplicate": False, "sent": False}


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


def _copy_touch(clean: dict, touch: Optional[dict]) -> None:
    first, last = _touch_maps(touch)
    utm = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
    clicks = ("fbc", "fbp", "ttclid", "ttp", "gclid", "gbraid", "wbraid", "epik", "li_fat_id")
    clean["utm_first"] = {field: first[field] for field in utm if first.get(field)}
    clean["utm_last"] = {field: last[field] for field in utm if last.get(field)}
    for click in clicks:
        value = last.get(click) or first.get(click)
        if value and click not in clean:
            clean[click] = value
    if not clean.get("invite_code"):
        code = last.get("invite_code") or first.get("invite_code")
        if code:
            clean["invite_code"] = code
    if not clean.get("promo_code"):
        code = last.get("promo_code") or first.get("promo_code")
        if code:
            clean["promo_code"] = code


def _touch_maps(touch: Optional[dict]) -> tuple[dict, dict]:
    if not touch:
        return {}, {}
    body = touch.get("touch") or touch
    return dict(body.get("first") or {}), dict(body.get("last") or {})


def _platform_payload(key, name, event_id, clean, *, email, phone, ip, user_agent) -> dict:
    spec = billing_config.PLATFORM_EVENTS[key]
    tiktok = spec.get("tiktok")
    if isinstance(tiktok, str):
        tiktok = [tiktok]
    payload = {
        "event_name": name,
        "event_id": event_id,
        "meta_event": spec.get("meta"),
        "tiktok_event": list(tiktok or []),
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


def flush_pending() -> None:
    """Send queued platform events. A failure is logged and queued, never raised."""
    batch = list(pending_sends)
    pending_sends.clear()
    for payload in batch:
        _dispatch(payload)


def _dispatch(payload: dict) -> None:
    """Tests set transport. Otherwise send through CAPI when flag, id, and token exist."""
    try:
        if transport is not None:
            transport(payload)
            outbound.append(payload)
            return
        if not (capi_ready("meta") or capi_ready("tiktok")):
            logger.info("tracking platform send skipped event=%s", payload.get("event_name"))
            return
        _send_capi(payload)
        outbound.append(payload)
    except Exception as exc:
        logger.exception("platform send failed event_id=%s", payload.get("event_id"))
        _enqueue_retry(payload, exc)


def capi_ready(platform: str) -> bool:
    if not enabled() or production():
        return False
    if platform == "meta":
        return bool(os.getenv("META_PIXEL_ID", "").strip() and os.getenv("META_CAPI_TOKEN", "").strip())
    if platform == "tiktok":
        return bool(os.getenv("TIKTOK_PIXEL_ID", "").strip() and os.getenv("TIKTOK_EVENTS_API_TOKEN", "").strip())
    return False


def _enqueue_retry(payload: dict, exc: Exception) -> None:
    row = {
        "id": str(__import__("uuid").uuid4()),
        "event_id": str(payload.get("event_id") or ""),
        "payload": payload,
        "error": str(exc)[:2000],
        "created_at": utcnow(),
    }
    db.memory_tracking_retries.append(row)
    if not db.configured():
        return
    try:
        from models import TrackingRetry

        with db.session_scope() as session:
            session.add(
                TrackingRetry(
                    id=row["id"],
                    event_id=row["event_id"],
                    payload=payload,
                    error=row["error"],
                    created_at=row["created_at"],
                )
            )
    except Exception:
        logger.exception("could not persist tracking retry event_id=%s", row["event_id"])


def _send_capi(payload: dict) -> None:
    import httpx

    if capi_ready("meta") and payload.get("meta_event"):
        user_data = {}
        if payload.get("hashed_email"):
            user_data["em"] = [payload["hashed_email"]]
        if payload.get("hashed_phone"):
            user_data["ph"] = [payload["hashed_phone"]]
        props = payload.get("properties") or {}
        if props.get("fbc"):
            user_data["fbc"] = props["fbc"]
        if props.get("fbp"):
            user_data["fbp"] = props["fbp"]
        if payload.get("ip"):
            user_data["client_ip_address"] = payload["ip"]
        if payload.get("user_agent"):
            user_data["client_user_agent"] = payload["user_agent"]
        body = {
            "data": [
                {
                    "event_name": payload["meta_event"],
                    "event_time": int(time.time()),
                    "event_id": payload["event_id"],
                    "action_source": "website",
                    "user_data": user_data,
                    "custom_data": props,
                }
            ]
        }
        if payload.get("meta_test_event_code"):
            body["test_event_code"] = payload["meta_test_event_code"]
        pixel = os.getenv("META_PIXEL_ID", "").strip()
        token = os.getenv("META_CAPI_TOKEN", "").strip()
        try:
            httpx.post(
                f"https://graph.facebook.com/v21.0/{pixel}/events",
                params={"access_token": token},
                json=body,
                timeout=5.0,
            )
        except Exception:
            logger.exception("Meta CAPI failed event_id=%s", payload.get("event_id"))
            raise
    if capi_ready("tiktok"):
        pixel = os.getenv("TIKTOK_PIXEL_ID", "").strip()
        token = os.getenv("TIKTOK_EVENTS_API_TOKEN", "").strip()
        props = payload.get("properties") or {}
        for event_name in payload.get("tiktok_event") or []:
            event = {
                "event": event_name,
                "event_time": int(time.time()),
                "event_id": payload["event_id"],
                "user": {
                    "email": payload.get("hashed_email"),
                    "phone": payload.get("hashed_phone"),
                    "ttclid": props.get("ttclid"),
                    "ttp": props.get("ttp"),
                    "ip": payload.get("ip"),
                    "user_agent": payload.get("user_agent"),
                },
                "properties": props,
            }
            if payload.get("tiktok_test_event_code"):
                event["test_event_code"] = payload["tiktok_test_event_code"]
            try:
                httpx.post(
                    "https://business-api.tiktok.com/open_api/v1.3/event/track/",
                    headers={"Access-Token": token, "Content-Type": "application/json"},
                    json={"event_source": "web", "event_source_id": pixel, "data": [event]},
                    timeout=5.0,
                )
            except Exception:
                logger.exception("TikTok Events API failed event_id=%s", payload.get("event_id"))
                raise


def pixel_snippet(consent: bool, fires: Optional[list] = None) -> str:
    """Real Meta and TikTok browser tags. They load only with an id and consent."""
    if not consent or production():
        return ""
    meta_id = os.getenv("META_PIXEL_ID", "").strip()
    tiktok_id = os.getenv("TIKTOK_PIXEL_ID", "").strip()
    if not meta_id and not tiktok_id:
        return ""
    calls = []
    for fire in fires or []:
        spec = billing_config.PLATFORM_EVENTS.get(fire["key"], {})
        event_id = json.dumps(fire["event_id"])
        params = json.dumps(fire.get("params") or {})
        meta_event = spec.get("meta")
        if meta_id and meta_event:
            calls.append(f"fbq('track', {json.dumps(meta_event)}, {params}, {{eventID: {event_id}}});")
        tiktok_events = spec.get("tiktok")
        if isinstance(tiktok_events, str):
            tiktok_events = [tiktok_events]
        if tiktok_id:
            for name in tiktok_events or []:
                calls.append(f"ttq.track({json.dumps(name)}, Object.assign({params}, {{event_id: {event_id}}}));")
    script = []
    if meta_id:
        safe_id = html.escape(meta_id, quote=True)
        script.append(
            "<script>"
            "!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?"
            "n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;"
            "n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;"
            "t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}"
            "(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');"
            f"fbq('init', '{safe_id}');"
            + "".join(call for call in calls if call.startswith("fbq"))
            + "</script>"
        )
    if tiktok_id:
        safe_id = html.escape(tiktok_id, quote=True)
        script.append(
            "<script>"
            "!function(w,d,t){w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];"
            "ttq.methods=['page','track','identify','instances','debug','on','off','once','ready','alias','group','enableCookie','disableCookie'];"
            "ttq.setAndDefer=function(t,e){t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};"
            "for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);"
            "ttq.load=function(e,n){var r='https://analytics.tiktok.com/i18n/pixel/events.js';"
            "ttq._i=ttq._i||{};ttq._i[e]=[];ttq._i[e]._u=r;ttq._t=ttq._t||{};ttq._t[e]=+new Date;"
            "ttq._o=ttq._o||{};ttq._o[e]=n||{};var o=document.createElement('script');"
            "o.type='text/javascript';o.async=!0;o.src=r+'?sdkid='+e+'&lib='+t;"
            "var a=document.getElementsByTagName('script')[0];a.parentNode.insertBefore(o,a)};"
            f"ttq.load('{safe_id}');"
            + "".join(call for call in calls if call.startswith("ttq"))
            + "}(window,document,'ttq');</script>"
        )
    return "".join(script)
