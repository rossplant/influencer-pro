"""First-touch and last-touch attribution.

The first visit is stored for 90 days and is never overwritten. The cookie is
written only after the visitor allows marketing cookies.
Last-touch updates on later visits. Click ids are kept with the touch.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote

COOKIE = "plantr_attr"
MAX_AGE = 90 * 24 * 60 * 60

UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
CLICK_FIELDS = ("ttclid", "ttp", "fbclid", "fbc", "fbp", "gclid", "gbraid", "wbraid", "epik", "li_fat_id")
CODE_FIELDS = ("invite_code", "promo_code", "campaign", "channel")


def observe(scope: dict) -> dict:
    """Read the cookie and the query string. Return the touch and a cookie to set."""
    query = _query(scope)
    incoming = {**_platform_cookies(scope), **_from_query(query)}
    stored = _read_cookie(scope)
    first = dict(stored.get("first") or {})
    last = dict(stored.get("last") or {})
    if incoming:
        if not first:
            first = dict(incoming)
        last = {**last, **incoming}
    touch = {"first": first, "last": last}
    cookie = None
    if incoming or not stored:
        cookie = _cookie(touch, secure=scope.get("scheme") == "https")
    return {"touch": touch, "cookie": cookie}


def consented_cookie(observed: dict, *, secure: bool) -> Optional[str]:
    """The attribution cookie for a visitor who has just allowed marketing cookies."""
    touch = (observed or {}).get("touch") or {}
    if not touch.get("first") and not touch.get("last"):
        return None
    return _cookie(touch, secure=secure)


def first_of(touch: Optional[dict]) -> dict:
    if not touch:
        return {}
    return dict((touch.get("touch") or touch).get("first") or {})


def last_of(touch: Optional[dict]) -> dict:
    if not touch:
        return {}
    return dict((touch.get("touch") or touch).get("last") or {})


def apply_first_touch(record: Any, touch: Optional[dict]) -> None:
    """Store first-touch once. Later calls do not replace it."""
    current = getattr(record, "attribution_first", None)
    if current:
        return
    first = first_of(touch)
    if first:
        record.attribution_first = first
    last = last_of(touch)
    if last:
        record.attribution_last = last


def fbc_from_fbclid(fbclid: str, now_ms: int) -> str:
    return f"fb.1.{now_ms}.{fbclid}"


def _from_query(query: dict) -> dict:
    found = {}
    aliases = {
        "utm_source": "utm_source",
        "utm_medium": "utm_medium",
        "utm_campaign": "utm_campaign",
        "utm_content": "utm_content",
        "utm_term": "utm_term",
        "ttclid": "ttclid",
        "_ttp": "ttp",
        "ttp": "ttp",
        "fbclid": "fbclid",
        "_fbc": "fbc",
        "fbc": "fbc",
        "_fbp": "fbp",
        "fbp": "fbp",
        "gclid": "gclid",
        "gbraid": "gbraid",
        "wbraid": "wbraid",
        "epik": "epik",
        "li_fat_id": "li_fat_id",
        "invite": "invite_code",
        "invite_code": "invite_code",
        "promo": "promo_code",
        "promo_code": "promo_code",
        "campaign": "campaign",
        "channel": "channel",
    }
    for key, target in aliases.items():
        value = (query.get(key) or [""])[0].strip()
        if value:
            found[target] = value[:255]
    if found.get("fbclid") and not found.get("fbc"):
        found["fbc"] = fbc_from_fbclid(found["fbclid"], _now_ms())
    return found


def _platform_cookies(scope: dict) -> dict:
    """Read Meta and TikTok cookies. Query-string values override these."""
    header = _cookie_header(scope)
    names = {"_fbp": "fbp", "_fbc": "fbc", "_ttp": "ttp"}
    found = {}
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        target = names.get(key.strip())
        if target and value.strip():
            found[target] = unquote(value.strip())[:255]
    return found


def _cookie_header(scope: dict) -> str:
    for name, value in scope.get("headers") or []:
        if name.lower() == b"cookie":
            return value.decode("utf-8", "ignore")
    return ""


def _query(scope: dict) -> dict:
    raw = scope.get("query_string") or b""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    return parse_qs(raw, keep_blank_values=False)


def _read_cookie(scope: dict) -> dict:
    header = _cookie_header(scope)
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == COOKIE:
            try:
                parsed = json.loads(unquote(value.strip().strip('"')))
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
    return {}


def _cookie(touch: dict, *, secure: bool) -> str:
    payload = quote(json.dumps({"first": touch["first"], "last": touch["last"]}, separators=(",", ":")))
    suffix = "; Secure" if secure else ""
    return f"{COOKIE}={payload}; Path=/; Max-Age={MAX_AGE}; HttpOnly; SameSite=Lax{suffix}"


def _now_ms() -> int:
    from datetime import datetime, timezone

    return int(datetime.now(timezone.utc).timestamp() * 1000)
