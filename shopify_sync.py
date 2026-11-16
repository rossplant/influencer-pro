"""Shopify order sync: store connection, webhooks, attribution, refunds, and backfill.

Two connection modes:
- store_webhooks (pilot bridge): the seller creates Order creation, Order update and
  Refund create webhooks in Settings > Notifications > Webhooks and gives Plantr the
  store signing key. No Shopify app review. No backfill.
- app: the unlisted public Plantr Collab app. OAuth (authorization code grant) with
  expiring offline tokens and the read_orders scope. Webhooks are declared in
  shopify/shopify.app.toml. Backfill uses the GraphQL Admin API.

Only orders that carry a Plantr link reference or a Plantr/seller code for a creator
are stored. No customer names, emails, phones or addresses are stored. Secrets are
Fernet-encrypted with SHOPIFY_TOKEN_KEY and never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse, parse_qsl

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import db
from models import (
    AttributedOrder,
    CommissionAuditLog,
    CreatorProductLink,
    Shop,
    ShopifyConnection,
    ShopifyWebhookReceipt,
    utcnow,
)

logger = logging.getLogger("plantr.shopify")

SHOP_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
ORDER_TOPICS = {"orders/create", "orders/updated", "orders/paid", "orders/cancelled", "orders/edited"}
COMPLIANCE_TOPICS = {"customers/data_request", "customers/redact", "shop/redact"}
SETTLED_STATUSES = {"paid", "partially_refunded", "refunded"}
REF_PARAM = "pc_ref"
MAX_WEBHOOK_BYTES = 1_000_000
STORE_WEBHOOK_EVENTS = ["Order creation", "Order update", "Refund create"]
CENT = Decimal("0.01")


# ---------------------------------------------------------------- settings

def api_key() -> str:
    return os.getenv("SHOPIFY_API_KEY", "").strip()


def api_secret() -> str:
    return os.getenv("SHOPIFY_API_SECRET", "").strip()


def app_configured() -> bool:
    return bool(api_key() and api_secret())


def scopes() -> str:
    return os.getenv("SHOPIFY_SCOPES", "read_orders").strip() or "read_orders"


def api_version() -> str:
    return os.getenv("SHOPIFY_API_VERSION", "2026-10").strip() or "2026-10"


def attribution_window_days() -> int:
    return _int_env("ATTRIBUTION_WINDOW_DAYS", 7)


def backfill_days_default() -> int:
    return min(_int_env("SHOPIFY_BACKFILL_DAYS", 60), 60)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def require_db() -> None:
    if not db.configured():
        raise HTTPException(status_code=503, detail="Shopify sync needs the database")


# ---------------------------------------------------------------- secrets

def _fernet():
    key = os.getenv("SHOPIFY_TOKEN_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Shopify secret storage isn't configured (SHOPIFY_TOKEN_KEY)")
    from cryptography.fernet import Fernet

    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=503, detail="SHOPIFY_TOKEN_KEY is not a valid key") from None


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("Could not decrypt a stored Shopify secret; the key may have changed")
        return None


# ---------------------------------------------------------------- HMAC

def webhook_hmac(body: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode("ascii")


def verify_webhook_hmac(body: bytes, header: Optional[str], secret: Optional[str]) -> bool:
    if not header or not secret:
        return False
    return hmac.compare_digest(webhook_hmac(body, secret).encode("ascii"), header.strip().encode("ascii", "ignore"))


def oauth_query_hmac(params: dict, secret: str) -> str:
    message = "&".join(f"{key}={params[key]}" for key in sorted(params) if key not in {"hmac", "signature"})
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_oauth_query(params: dict, secret: str) -> bool:
    provided = str(params.get("hmac") or "")
    if not provided or not secret:
        return False
    return hmac.compare_digest(oauth_query_hmac(params, secret), provided)


def normalise_domain(raw: Optional[str]) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"^https?://", "", text).split("/", 1)[0]
    if text and "." not in text:
        text = f"{text}.myshopify.com"
    if not SHOP_DOMAIN_RE.fullmatch(text):
        raise HTTPException(status_code=422, detail="Enter your store's myshopify.com domain, for example yourstore.myshopify.com")
    return text


# ---------------------------------------------------------------- connection

def connection_status(shop_id: str) -> dict:
    require_db()
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.shop_id == shop_id))
        return _connection_dict(conn) if conn is not None else {"connected": False, "app_available": app_configured()}


def _connection_dict(conn: ShopifyConnection, base_url: str = "") -> dict:
    out = {
        "connected": conn.status == "active",
        "mode": conn.mode,
        "status": conn.status,
        "shop_domain": conn.shop_domain,
        "installed_at": conn.installed_at,
        "last_webhook_at": conn.last_webhook_at,
        "last_backfill_at": conn.last_backfill_at,
        "app_available": app_configured(),
    }
    if conn.mode == "store_webhooks" and conn.webhook_path_token:
        out["webhook_path"] = f"/shopify/webhooks/store/{conn.webhook_path_token}"
        if base_url:
            out["webhook_url"] = base_url.rstrip("/") + out["webhook_path"]
        out["webhook_events"] = STORE_WEBHOOK_EVENTS
    return out


def _claim_domain(session, shop_id: str, domain: str) -> ShopifyConnection:
    shop = session.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    other = session.scalar(select(Shop).where(Shop.shopify_domain == domain, Shop.id != shop_id))
    taken = session.scalar(
        select(ShopifyConnection).where(ShopifyConnection.shop_domain == domain, ShopifyConnection.shop_id != shop_id)
    )
    if other is not None or taken is not None:
        raise HTTPException(status_code=409, detail="That Shopify store is already connected to another Plantr account")
    conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.shop_id == shop_id))
    if conn is None:
        conn = ShopifyConnection(id=str(uuid.uuid4()), shop_id=shop_id, shop_domain=domain, mode="store_webhooks", created_at=utcnow())
        session.add(conn)
    conn.shop_domain = domain
    shop.shopify_domain = domain
    return conn


def connect_store_webhooks(shop_id: str, shop_domain: str, signing_secret: str, *, actor: str, base_url: str = "") -> dict:
    """Pilot bridge. Stores the store signing key encrypted and returns the URL to paste into Shopify."""
    require_db()
    domain = normalise_domain(shop_domain)
    secret = (signing_secret or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{16,128}", secret):
        raise HTTPException(status_code=422, detail="Paste the signing key shown under Settings > Notifications > Webhooks")
    encrypted = encrypt(secret)
    with db.session_scope() as session:
        conn = _claim_domain(session, shop_id, domain)
        if conn.mode == "app" and conn.status == "active":
            raise HTTPException(status_code=409, detail="This store is already connected through the Plantr Collab app")
        conn.mode = "store_webhooks"
        conn.status = "active"
        conn.webhook_secret_enc = encrypted
        conn.webhook_path_token = conn.webhook_path_token or secrets.token_urlsafe(24)
        conn.installed_at = conn.installed_at or utcnow()
        conn.uninstalled_at = None
        shop = session.get(Shop, shop_id)
        shop.shopify_connected_at = shop.shopify_connected_at or utcnow()
        _audit(session, shop_id, "seller", actor, "shopify_connected", details={"mode": "store_webhooks", "shop_domain": domain})
        session.flush()
        result = _connection_dict(conn, base_url)
        installed = conn.installed_at
    _track("shopify_connected", f"shopify_connected:{shop_id}:{installed.isoformat()}", shop_id, mode="store_webhooks")
    return result


def begin_oauth(shop_id: str, shop_domain: str, *, redirect_uri: str) -> str:
    require_db()
    if not app_configured():
        raise HTTPException(status_code=503, detail="The Plantr Collab Shopify app isn't available yet. Use the webhook setup instead.")
    domain = normalise_domain(shop_domain)
    state = secrets.token_urlsafe(24)
    with db.session_scope() as session:
        conn = _claim_domain(session, shop_id, domain)
        if conn.status != "active":
            conn.mode = "app"
            conn.status = "pending"
        conn.oauth_state = state
        conn.oauth_state_expires_at = utcnow() + timedelta(minutes=10)
    query = urlencode({"client_id": api_key(), "scope": scopes(), "redirect_uri": redirect_uri, "state": state})
    return f"https://{domain}/admin/oauth/authorize?{query}"


def _http_post(url: str, *, json_body: dict, headers: Optional[dict] = None) -> dict:
    import httpx

    response = httpx.post(url, json=json_body, headers=headers or {}, timeout=20)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Shopify returned {response.status_code}")
    return response.json()


def complete_oauth(params: dict, *, http_post: Optional[Callable] = None) -> str:
    """Verify the callback, exchange the code for an expiring offline token, and store it. Returns shop_id."""
    require_db()
    if not app_configured():
        raise HTTPException(status_code=503, detail="Shopify app is not configured")
    if not verify_oauth_query(params, api_secret()):
        raise HTTPException(status_code=401, detail="Invalid Shopify signature")
    domain = normalise_domain(params.get("shop"))
    state = str(params.get("state") or "")
    code = str(params.get("code") or "")
    if not state or not code:
        raise HTTPException(status_code=422, detail="Missing code or state")
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.oauth_state == state))
        if conn is None or conn.shop_domain != domain or not conn.oauth_state_expires_at or conn.oauth_state_expires_at < utcnow():
            raise HTTPException(status_code=401, detail="This Shopify sign-in link has expired. Start again from Plantr.")
        shop_id = conn.shop_id
    post = http_post or _http_post
    token = post(
        f"https://{domain}/admin/oauth/access_token",
        json_body={"client_id": api_key(), "client_secret": api_secret(), "code": code, "expiring": 1},
    )
    access = str(token.get("access_token") or "")
    if not access:
        raise HTTPException(status_code=502, detail="Shopify did not return an access token")
    now = utcnow()
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.shop_id == shop_id))
        _store_tokens(conn, token, now)
        conn.mode = "app"
        conn.status = "active"
        conn.scopes = str(token.get("scope") or scopes())[:255]
        conn.oauth_state = None
        conn.oauth_state_expires_at = None
        conn.installed_at = now
        conn.uninstalled_at = None
        conn.webhook_secret_enc = None
        shop = session.get(Shop, shop_id)
        shop.shopify_connected_at = shop.shopify_connected_at or now
        _audit(session, shop_id, "seller", domain, "shopify_connected", details={"mode": "app", "scopes": conn.scopes})
    _track("shopify_connected", f"shopify_connected:{shop_id}:{now.isoformat()}", shop_id, mode="app")
    return shop_id


def _store_tokens(conn: ShopifyConnection, token: dict, now: datetime) -> None:
    conn.access_token_enc = encrypt(str(token["access_token"]))
    expires_in = token.get("expires_in")
    conn.access_token_expires_at = now + timedelta(seconds=int(expires_in)) if expires_in else None
    if token.get("refresh_token"):
        conn.refresh_token_enc = encrypt(str(token["refresh_token"]))
        refresh_in = token.get("refresh_token_expires_in")
        conn.refresh_token_expires_at = now + timedelta(seconds=int(refresh_in)) if refresh_in else None


def access_token(shop_id: str, *, http_post: Optional[Callable] = None) -> tuple[str, str]:
    """Return (domain, token), refreshing an expiring offline token when it is within 5 minutes of expiry."""
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.shop_id == shop_id))
        if conn is None or conn.mode != "app" or conn.status != "active":
            raise HTTPException(status_code=409, detail="Backfill needs the Plantr Collab Shopify app connection")
        domain = conn.shop_domain
        expires = conn.access_token_expires_at
        token = decrypt(conn.access_token_enc)
        refresh = decrypt(conn.refresh_token_enc)
        if token and (expires is None or expires > utcnow() + timedelta(minutes=5)):
            return domain, token
        if not refresh:
            raise HTTPException(status_code=409, detail="Shopify access expired. Ask the seller to reconnect.")
    post = http_post or _http_post
    fresh = post(
        f"https://{domain}/admin/oauth/access_token",
        json_body={"client_id": api_key(), "client_secret": api_secret(), "grant_type": "refresh_token", "refresh_token": refresh},
    )
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.shop_id == shop_id))
        _store_tokens(conn, fresh, utcnow())
    return domain, str(fresh["access_token"])


def disconnect(shop_id: str, *, actor: str, role: str = "seller") -> dict:
    require_db()
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.shop_id == shop_id))
        if conn is None:
            return {"connected": False}
        _wipe(conn, "disconnected")
        _audit(session, shop_id, role, actor, "shopify_disconnected")
    _track("shopify_disconnected", f"shopify_disconnected:{shop_id}:{utcnow().isoformat()}", shop_id)
    return {"connected": False}


def _wipe(conn: ShopifyConnection, status: str) -> None:
    conn.status = status
    conn.access_token_enc = None
    conn.refresh_token_enc = None
    conn.access_token_expires_at = None
    conn.refresh_token_expires_at = None
    conn.webhook_secret_enc = None
    conn.oauth_state = None
    conn.uninstalled_at = utcnow()


def shop_connected(shop_id: Optional[str]) -> bool:
    if not shop_id or not db.configured():
        return False
    with db.session_scope() as session:
        return session.scalar(
            select(ShopifyConnection.id).where(ShopifyConnection.shop_id == shop_id, ShopifyConnection.status == "active")
        ) is not None


def with_ref(destination: str, code: str) -> str:
    """Add pc_ref=<code> to the product URL so Shopify keeps it in the order's landing_site."""
    parts = urlparse(destination)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != REF_PARAM]
    query.append((REF_PARAM, code))
    return urlunparse(parts._replace(query=urlencode(query)))


def set_store_code(shop_id: str, link_id: str, code: Optional[str], *, actor: str) -> dict:
    """The seller's own Shopify discount code for this creator (for example JESS10)."""
    require_db()
    clean = (code or "").strip().upper() or None
    if clean is not None and not re.fullmatch(r"[A-Z0-9_\-]{3,64}", clean):
        raise HTTPException(status_code=422, detail="Codes use letters, numbers, - and _ only")
    with db.session_scope() as session:
        link = session.get(CreatorProductLink, link_id)
        if link is None or link.shop_id != shop_id:
            raise HTTPException(status_code=404, detail="Link not found")
        if clean is not None:
            clash = session.scalar(
                select(CreatorProductLink.id).where(
                    CreatorProductLink.shop_id == shop_id,
                    CreatorProductLink.id != link_id,
                    (func.upper(CreatorProductLink.store_discount_code) == clean) | (func.upper(CreatorProductLink.discount_code) == clean),
                )
            )
            if clash is not None:
                raise HTTPException(status_code=409, detail="That code is already linked to another creator")
        link.store_discount_code = clean
        _audit(session, shop_id, "seller", actor, "store_code_set", details={"link_id": link_id, "code": clean})
        return {"id": link.id, "discount_code": link.discount_code, "store_discount_code": link.store_discount_code}


# ---------------------------------------------------------------- webhooks

def handle_webhook(body: bytes, headers: dict, *, path_token: Optional[str] = None) -> tuple[int, dict]:
    """Verify and process one webhook. Returns (status_code, payload). Bad HMAC is 401."""
    require_db()
    if len(body) > MAX_WEBHOOK_BYTES:
        return 413, {"detail": "Body too large"}
    lower = {str(k).lower(): v for k, v in headers.items()}
    topic = str(lower.get("x-shopify-topic") or "").strip().lower()
    domain_header = str(lower.get("x-shopify-shop-domain") or "").strip().lower()
    signature = lower.get("x-shopify-hmac-sha256")
    conn_id = None
    if path_token:
        with db.session_scope() as session:
            conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.webhook_path_token == path_token))
            secret = decrypt(conn.webhook_secret_enc) if conn is not None and conn.status == "active" else None
            expected_domain = conn.shop_domain if conn is not None else None
            conn_id = conn.id if conn is not None else None
        if not verify_webhook_hmac(body, signature, secret):
            return 401, {"detail": "Invalid signature"}
        if domain_header and domain_header != expected_domain:
            return 401, {"detail": "Store does not match"}
        domain = expected_domain
    else:
        if not verify_webhook_hmac(body, signature, api_secret() or None):
            return 401, {"detail": "Invalid signature"}
        domain = domain_header
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"detail": "Body is not JSON"}
    webhook_id = str(lower.get("x-shopify-webhook-id") or "").strip()
    key = webhook_id[:128] if webhook_id else "sha256:" + hashlib.sha256(topic.encode() + b"|" + (domain or "").encode() + b"|" + body).hexdigest()
    with db.session_scope() as session:
        if session.scalar(select(ShopifyWebhookReceipt.id).where(ShopifyWebhookReceipt.delivery_key == key)) is not None:
            return 200, {"status": "duplicate"}
    outcome = _dispatch(topic, domain, payload, conn_id=conn_id)
    try:
        with db.session_scope() as session:
            session.add(
                ShopifyWebhookReceipt(
                    id=str(uuid.uuid4()), delivery_key=key, topic=topic[:64] or "unknown", shop_domain=domain, outcome=outcome[:64], received_at=utcnow()
                )
            )
    except IntegrityError:
        return 200, {"status": "duplicate"}
    return 200, {"status": outcome}


def _dispatch(topic: str, domain: Optional[str], payload: dict, *, conn_id: Optional[str]) -> str:
    if topic in COMPLIANCE_TOPICS:
        return _compliance(topic, domain, payload)
    with db.session_scope() as session:
        query = select(ShopifyConnection).where(ShopifyConnection.id == conn_id) if conn_id else select(ShopifyConnection).where(ShopifyConnection.shop_domain == (domain or ""))
        conn = session.scalar(query)
        if conn is None or conn.status != "active":
            return "ignored_unknown_shop"
        conn.last_webhook_at = utcnow()
        shop_id = conn.shop_id
        if topic == "app/uninstalled":
            _wipe(conn, "uninstalled")
            _audit(session, shop_id, "system", domain or "shopify", "shopify_uninstalled")
            return "uninstalled"
    if topic in ORDER_TOPICS:
        return process_order(shop_id, payload)
    if topic == "refunds/create":
        return process_refund(shop_id, payload)
    return "ignored_topic"


def _compliance(topic: str, domain: Optional[str], payload: dict) -> str:
    """Plantr stores no customer personal data from Shopify, so redaction has nothing to remove."""
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.shop_domain == (domain or "")))
        shop_id = conn.shop_id if conn is not None else None
        if topic == "shop/redact" and conn is not None:
            session.delete(conn)
        _audit(
            session,
            shop_id,
            "system",
            domain or "shopify",
            topic.replace("/", "_"),
            details={
                "orders": [str(o) for o in (payload.get("orders_requested") or payload.get("orders_to_redact") or [])][:500],
                "note": "No customer personal data is stored; order amounts are kept for commission records.",
            },
        )
    return topic.replace("/", "_")


# ---------------------------------------------------------------- orders

def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else "0")).quantize(CENT, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def net_sales(order: dict) -> dict:
    """Net merchandise sales ex shipping and ex tax, before refunds. Gift cards are excluded."""
    taxes_included = bool(order.get("taxes_included"))
    gross = Decimal("0.00")
    tax_total = Decimal("0.00")
    for line in order.get("line_items") or []:
        if line.get("gift_card"):
            continue
        qty = int(line.get("quantity") or 0)
        price = _money(line.get("price")) * qty
        discount = sum((_money(a.get("amount")) for a in line.get("discount_allocations") or []), Decimal("0.00"))
        tax = sum((_money(t.get("price")) for t in line.get("tax_lines") or []), Decimal("0.00"))
        tax_total += tax
        gross += price - discount - (tax if taxes_included else Decimal("0.00"))
    shipping = sum((_money(s.get("price")) for s in order.get("shipping_lines") or []), Decimal("0.00"))
    return {"net": max(gross, Decimal("0.00")), "tax": tax_total, "shipping": shipping, "taxes_included": taxes_included}


def refund_net(refund: dict, *, taxes_included: bool) -> Decimal:
    total = Decimal("0.00")
    for item in refund.get("refund_line_items") or []:
        if (item.get("line_item") or {}).get("gift_card"):
            continue
        subtotal = _money(item.get("subtotal"))
        tax = _money(item.get("total_tax"))
        total += subtotal - (tax if taxes_included else Decimal("0.00"))
    return max(total, Decimal("0.00"))


def _ref_codes(order: dict) -> list[str]:
    codes = []
    for field in ("landing_site", "landing_site_ref"):
        value = str(order.get(field) or "")
        if not value:
            continue
        if field == "landing_site_ref":
            codes.append(value)
            continue
        query = urlparse(value if "://" in value else "https://x" + (value if value.startswith("/") else "/" + value)).query
        codes.extend(parse_qs(query).get(REF_PARAM, []))
    for attr in order.get("note_attributes") or []:
        if str(attr.get("name") or "").strip().lower() in {REF_PARAM, "_" + REF_PARAM}:
            codes.append(str(attr.get("value") or ""))
    return [c.strip().upper() for c in codes if c and c.strip()]


def _link_for_code(session, shop_id: str, code: str) -> Optional[CreatorProductLink]:
    code = code.strip().upper()
    if not code:
        return None
    return session.scalar(
        select(CreatorProductLink).where(
            CreatorProductLink.shop_id == shop_id,
            (func.upper(CreatorProductLink.discount_code) == code) | (func.upper(CreatorProductLink.store_discount_code) == code),
        )
    )


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def attribute(session, shop_id: str, order: dict, occurred: datetime) -> dict:
    """Pick at most one creator. Code beats link when they point at different creators."""
    import attribution

    ref_link = None
    for code in _ref_codes(order):
        ref_link = session.scalar(
            select(CreatorProductLink).where(CreatorProductLink.shop_id == shop_id, func.upper(CreatorProductLink.discount_code) == code)
        )
        if ref_link is not None:
            break
    code_link = None
    for entry in order.get("discount_codes") or []:
        code_link = _link_for_code(session, shop_id, str(entry.get("code") or ""))
        if code_link is not None:
            break
    if code_link is not None and ref_link is not None:
        if code_link.creator_id == ref_link.creator_id:
            return {"link": code_link, "method": "link+code", "attributed": True, "note": None}
        return {
            "link": code_link,
            "method": "code",
            "attributed": True,
            "note": f"code beat link from creator {ref_link.creator_id}",
            "superseded_creator_id": ref_link.creator_id,
        }
    if code_link is not None:
        return {"link": code_link, "method": "code", "attributed": True, "note": None}
    if ref_link is not None:
        link_dict = {"id": ref_link.id, "discount_code": ref_link.discount_code}
        clicked = attribution._clicked_within_window(link_dict, occurred.date())
        return {
            "link": ref_link,
            "method": "link",
            "attributed": clicked,
            "note": None if clicked else f"no click in the {attribution_window_days()} days before the order",
        }
    return {"link": None, "method": None, "attributed": False, "note": None}


def process_order(shop_id: str, order: dict) -> str:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return "ignored_no_id"
    occurred = _parse_time(order.get("created_at")) or utcnow()
    updated = _parse_time(order.get("updated_at"))
    amounts = net_sales(order)
    financial = str(order.get("financial_status") or "").lower() or None
    cancelled_at = _parse_time(order.get("cancelled_at"))
    refunds_present = "refunds" in order
    refund_map = {
        str(r.get("id")): str(refund_net(r, taxes_included=amounts["taxes_included"]))
        for r in order.get("refunds") or []
        if r.get("id") is not None
    }
    with db.session_scope() as session:
        row = session.scalar(
            select(AttributedOrder).where(AttributedOrder.platform == "shopify", AttributedOrder.external_order_id == order_id)
        )
        if row is not None and row.shop_id != shop_id:
            return "ignored_wrong_shop"
        if row is not None and updated and row.external_updated_at and updated < row.external_updated_at:
            return "ignored_stale"
        created = False
        if row is None:
            picked = attribute(session, shop_id, order, occurred)
            if picked["link"] is None:
                return "ignored_no_plantr_link"
            link = picked["link"]
            shop = session.get(Shop, shop_id)
            import attribution

            brief_id = attribution._brief_id({"shortlist_item_id": link.shortlist_item_id, "product_id": link.product_id, "creator_id": link.creator_id})
            row = AttributedOrder(
                id=str(uuid.uuid4()),
                shop_id=shop_id,
                platform="shopify",
                country=getattr(shop, "country", None),
                market=getattr(shop, "market", None),
                external_order_id=order_id[:128],
                gmv_gross=0,
                refunded_amount=0,
                cancelled_amount=0,
                net_gmv=0,
                currency=str(order.get("currency") or getattr(shop, "currency", None) or "AUD")[:8].upper(),
                creator_id=link.creator_id,
                product_id=link.product_id,
                shortlist_item_id=link.shortlist_item_id,
                brief_id=brief_id,
                attribution_date=occurred.date(),
                click_window_days=attribution_window_days(),
                click_window_source=picked["method"],
                settlement_status="shopify",
                fee_revenue=0,
                ingest_source="shopify",
                raw_payload={"refunds": {}},
                attributed=picked["attributed"],
                attribution_method=picked["method"],
                attribution_note=(picked.get("note") or None),
                occurred_at=occurred,
                created_at=utcnow(),
                statement_reported_net=0,
            )
            summary = {
                "name": str(order.get("name") or "")[:32],
                "source_name": str(order.get("source_name") or "")[:32],
                "taxes_included": amounts["taxes_included"],
                "refunds": {},
                "matched_code": link.discount_code,
            }
            if picked.get("superseded_creator_id"):
                summary["superseded_link_creator_id"] = picked["superseded_creator_id"]
            row.raw_payload = summary
            session.add(row)
            created = True
        summary = dict(row.raw_payload or {})
        if refunds_present:
            merged = dict(summary.get("refunds") or {})
            merged.update(refund_map)
            summary["refunds"] = merged
        summary["taxes_included"] = amounts["taxes_included"]
        row.raw_payload = summary
        row.gmv_gross = float(amounts["net"])
        row.tax_excluded = float(amounts["tax"])
        row.shipping_excluded = float(amounts["shipping"])
        row.financial_status = financial
        row.cancelled_at = cancelled_at
        row.external_updated_at = updated or row.external_updated_at
        _recompute(row)
        session.flush()
        sale = None
        if created and row.attributed:
            sale = {
                "order_id": row.external_order_id, "gross": float(row.gmv_gross or 0), "currency": row.currency,
                "creator_id": row.creator_id, "method": row.attribution_method,
            }
    if sale is not None:
        _emit_sale(shop_id, sale)
    return "created" if created else "updated"


def process_refund(shop_id: str, refund: dict) -> str:
    order_id = str(refund.get("order_id") or "").strip()
    refund_id = str(refund.get("id") or "").strip()
    if not order_id or not refund_id:
        return "ignored_no_id"
    with db.session_scope() as session:
        row = session.scalar(
            select(AttributedOrder).where(AttributedOrder.platform == "shopify", AttributedOrder.external_order_id == order_id)
        )
        if row is None or row.shop_id != shop_id:
            return "ignored_unknown_order"
        summary = dict(row.raw_payload or {})
        refunds = dict(summary.get("refunds") or {})
        if refund_id in refunds:
            return "refund_duplicate"
        refunds[refund_id] = str(refund_net(refund, taxes_included=bool(summary.get("taxes_included"))))
        summary["refunds"] = refunds
        row.raw_payload = summary
        _recompute(row)
        return "refund_applied"


def _recompute(row: AttributedOrder) -> None:
    gross = _money(row.gmv_gross)
    refunds = sum((_money(v) for v in ((row.raw_payload or {}).get("refunds") or {}).values()), Decimal("0.00"))
    refunded = min(refunds, gross)
    cancelled = gross - refunded if row.cancelled_at else Decimal("0.00")
    row.refunded_amount = float(refunded)
    row.cancelled_amount = float(cancelled)
    row.net_gmv = float(max(gross - refunded - cancelled, Decimal("0.00")))
    row.settled = (row.financial_status in SETTLED_STATUSES) and row.cancelled_at is None


def _emit_sale(shop_id: str, sale: dict) -> None:
    import tracking

    tracking.track(
        "attributed_sale",
        event_id=f"sale:{shop_id}:shopify:{sale['order_id']}",
        entity_type="shop",
        entity_id=shop_id,
        shop_id=shop_id,
        actor="system",
        source="shopify",
        order_id=sale["order_id"],
        gross=sale["gross"],
        currency=sale["currency"],
        creator_id=sale["creator_id"],
        method=sale["method"],
        refunded=False,
        cancelled=False,
    )


# ---------------------------------------------------------------- backfill

BACKFILL_QUERY = """
query PlantrOrders($cursor: String, $query: String) {
  orders(first: 50, after: $cursor, query: $query, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      legacyResourceId name createdAt updatedAt cancelledAt currencyCode taxesIncluded
      displayFinancialStatus sourceName discountCodes
      customAttributes { key value }
      customerJourneySummary { firstVisit { landingPage } }
      shippingLines(first: 10) { nodes { originalPriceSet { shopMoney { amount } } } }
      lineItems(first: 100) { nodes {
        quantity isGiftCard
        originalUnitPriceSet { shopMoney { amount } }
        discountAllocations { allocatedAmountSet { shopMoney { amount } } }
        taxLines { priceSet { shopMoney { amount } } }
      } }
      refunds { legacyResourceId refundLineItems(first: 100) { nodes {
        subtotalSet { shopMoney { amount } } totalTaxSet { shopMoney { amount } }
      } } }
    }
  }
}
"""


def _amount(node: Optional[dict], key: str) -> str:
    return str((((node or {}).get(key) or {}).get("shopMoney") or {}).get("amount") or "0")


def graphql_to_rest(node: dict) -> dict:
    """Map a GraphQL order node onto the webhook (REST) shape that process_order reads."""
    journey = ((node.get("customerJourneySummary") or {}).get("firstVisit") or {})
    return {
        "id": node.get("legacyResourceId"),
        "name": node.get("name"),
        "created_at": node.get("createdAt"),
        "updated_at": node.get("updatedAt"),
        "cancelled_at": node.get("cancelledAt"),
        "currency": node.get("currencyCode"),
        "taxes_included": node.get("taxesIncluded"),
        "financial_status": str(node.get("displayFinancialStatus") or "").lower(),
        "source_name": node.get("sourceName"),
        "landing_site": journey.get("landingPage"),
        "discount_codes": [{"code": c} for c in node.get("discountCodes") or []],
        "note_attributes": [{"name": a.get("key"), "value": a.get("value")} for a in node.get("customAttributes") or []],
        "shipping_lines": [{"price": _amount(s, "originalPriceSet")} for s in ((node.get("shippingLines") or {}).get("nodes") or [])],
        "line_items": [
            {
                "quantity": li.get("quantity"),
                "gift_card": li.get("isGiftCard"),
                "price": _amount(li, "originalUnitPriceSet"),
                "discount_allocations": [{"amount": _amount(d, "allocatedAmountSet")} for d in li.get("discountAllocations") or []],
                "tax_lines": [{"price": _amount(t, "priceSet")} for t in li.get("taxLines") or []],
            }
            for li in ((node.get("lineItems") or {}).get("nodes") or [])
        ],
        "refunds": [
            {
                "id": r.get("legacyResourceId"),
                "refund_line_items": [
                    {"subtotal": _amount(i, "subtotalSet"), "total_tax": _amount(i, "totalTaxSet")}
                    for i in ((r.get("refundLineItems") or {}).get("nodes") or [])
                ],
            }
            for r in node.get("refunds") or []
        ],
    }


def backfill(shop_id: str, *, days: Optional[int] = None, actor: str, http_post: Optional[Callable] = None) -> dict:
    require_db()
    days = max(1, min(int(days or backfill_days_default()), 60))
    domain, token = access_token(shop_id, http_post=http_post)
    post = http_post or _http_post
    since = (utcnow() - timedelta(days=days)).date().isoformat()
    cursor = None
    counts: dict[str, int] = {}
    for _page in range(200):
        data = post(
            f"https://{domain}/admin/api/{api_version()}/graphql.json",
            json_body={"query": BACKFILL_QUERY, "variables": {"cursor": cursor, "query": f"created_at:>={since}"}},
            headers={"X-Shopify-Access-Token": token},
        )
        orders = ((data.get("data") or {}).get("orders") or {})
        for node in orders.get("nodes") or []:
            outcome = process_order(shop_id, graphql_to_rest(node))
            counts[outcome] = counts.get(outcome, 0) + 1
        info = orders.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
    with db.session_scope() as session:
        conn = session.scalar(select(ShopifyConnection).where(ShopifyConnection.shop_id == shop_id))
        conn.last_backfill_at = utcnow()
        _audit(session, shop_id, "admin", actor, "shopify_backfill", details={"days": days, "counts": counts})
    return {"days": days, "counts": counts}


def list_connections() -> list[dict]:
    require_db()
    with db.session_scope() as session:
        rows = session.scalars(select(ShopifyConnection).order_by(ShopifyConnection.created_at.desc())).all()
        return [{"shop_id": r.shop_id, **_connection_dict(r)} for r in rows]


# ---------------------------------------------------------------- helpers

def _audit(session, shop_id, role: str, actor: str, action: str, *, statement_id=None, reason=None, details=None) -> None:
    session.add(
        CommissionAuditLog(
            id=str(uuid.uuid4()),
            shop_id=shop_id,
            statement_id=statement_id,
            actor_role=role,
            actor_id=str(actor or "unknown")[:255],
            action=action,
            reason=reason,
            details=details,
            created_at=utcnow(),
        )
    )


def _track(key: str, event_id: str, shop_id: Optional[str], **props) -> None:
    import tracking

    try:
        tracking.track(key, event_id=event_id, entity_type="shop", entity_id=shop_id or "unknown", shop_id=shop_id, **props)
    except Exception:  # analytics must never break order sync
        logger.exception("Could not record %s", key)
