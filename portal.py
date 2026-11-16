"""Seller and creator pilot apps.

Logins are separate from the admin cookie. A seller session only opens that
shop, and a creator session only opens that creator. Forms carry a CSRF token.
"""

from __future__ import annotations

import html
import json
import uuid
from collections import defaultdict, deque
from threading import Lock
from types import SimpleNamespace
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

import attribution
import auth
import billing_config
import catalogue
import db
import events
import portal_copy
import scoring
import services
import stripe_billing
import taxonomy
from models import Brief, Creator, PortalAccount, Product, Shop, Shortlist, ShortlistItem, utcnow

MIN_PASSWORD = 12
_login_buckets: dict[str, deque] = defaultdict(deque)
_login_lock = Lock()


def reset_limits() -> None:
    with _login_lock:
        _login_buckets.clear()


def portal_enabled() -> bool:
    return bool(auth._secret())


def create_login(
    *,
    role: str,
    email: str,
    password: str,
    password_confirm: Optional[str],
    shop_id: Optional[str] = None,
    creator_id: Optional[str] = None,
    confirm: bool = True,
) -> dict:
    if role not in {"seller", "creator"}:
        raise HTTPException(status_code=422, detail="role must be seller or creator")
    cleaned = (email or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        raise HTTPException(status_code=422, detail="email is required")
    if confirm and not auth.passwords_match(password or "", password_confirm or ""):
        raise HTTPException(status_code=422, detail="Password confirmation does not match")
    if len(password or "") < MIN_PASSWORD:
        raise HTTPException(status_code=422, detail="Password must be at least 12 characters")
    if role == "seller" and not _shop_exists(shop_id):
        raise HTTPException(status_code=404, detail="Shop not found")
    if role == "creator" and not _creator_exists(creator_id):
        raise HTTPException(status_code=404, detail="Creator not found")
    existing = _find_email(cleaned)
    if existing is not None and (existing.role != role or _subject(existing) != (shop_id if role == "seller" else creator_id)):
        raise HTTPException(status_code=409, detail="That email is already used")
    password_hash = auth.hash_password(password)
    if existing is not None:
        existing.password_hash = password_hash
        _save_existing(existing)
        return {"ok": True, "email": cleaned, "role": role, "updated": True}
    account = _insert(role, cleaned, password_hash, shop_id, creator_id)
    return {"ok": True, "email": account.email, "role": role, "updated": False}


def authenticate(role: str, email: str, password: str, ip: str) -> Optional[object]:
    if not _login_allowed(ip, role):
        raise HTTPException(status_code=429, detail="Too many login attempts", headers={"Retry-After": "900"})
    account = _find_email((email or "").strip().lower())
    stored = account.password_hash if account is not None and account.role == role else "invalid"
    password_ok = auth.verify_password(password or "", stored)
    if account is None or account.role != role or not password_ok:
        return None
    if not auth.emails_match(email or "", account.email):
        return None
    _touch_login(account)
    return account


def seller_context(request: Request) -> Optional[dict]:
    return auth.read_role_session(request, auth.SELLER_COOKIE)


def creator_context(request: Request) -> Optional[dict]:
    return auth.read_role_session(request, auth.CREATOR_COOKIE)


class ReadOnlyView(Exception):
    """An operator is viewing an account and tried to change it."""


def require_seller(request: Request) -> dict:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    session = seller_context(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Seller session required")
    _reject_view_as_write(request, session)
    return session


def require_creator(request: Request) -> dict:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    session = creator_context(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Creator session required")
    _reject_view_as_write(request, session)
    return session


def _reject_view_as_write(request: Request, session: dict) -> None:
    if not session.get("impersonator"):
        return
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    path = request.url.path.rstrip("/") or "/"
    if path in {"/seller/logout", "/creator/logout"}:
        return
    raise ReadOnlyView()


def begin_impersonation(response, role: str, admin_email: str, subject_id: str, name: str, reason: str = ""):
    """Open a read-only view and record that it started. The password is not logged."""
    set_role_cookie(response, role, admin_email, subject_id, impersonator=admin_email)
    props = {
        "role": role,
        "subject_id": subject_id,
        "subject_name": name or "",
        "operator": admin_email,
    }
    text = " ".join((reason or "").split())
    if text:
        props["reason"] = text[:500]
    events.record_admin("view_as_started", **props)


def end_impersonation(session: dict) -> None:
    if not session.get("impersonator"):
        return
    events.record_admin(
        "view_as_ended",
        role=session.get("role") or "",
        subject_id=session.get("subject_id") or "",
        operator=session.get("impersonator") or "",
    )


def check_login_csrf(token: str, purpose: str) -> None:
    if not auth.form_token_ok(token or "", purpose):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def check_session_csrf(session: dict, token: str) -> None:
    if not auth.csrf_matches(session, token or ""):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def set_role_cookie(response, role: str, email: str, subject_id: str, impersonator: Optional[str] = None):
    token, _csrf = auth.issue_role_session(role, email, subject_id, impersonator)
    # Re-read csrf from the token so the page can embed the same value.
    payload = auth._read_signed(token)
    response.set_cookie(
        key=auth.SELLER_COOKIE if role == "seller" else auth.CREATOR_COOKIE,
        value=token,
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return payload["csrf"]


def clear_role_cookie(response, role: str):
    response.delete_cookie(
        auth.SELLER_COOKIE if role == "seller" else auth.CREATOR_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def shop_profile(shop_id: str) -> dict:
    shop = services.fetch_shop(shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


def seller_products(shop_id: str) -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Product).where(Product.shop_id == shop_id).order_by(Product.created_at.desc())).all()
            return [_product_row(row) for row in rows]
    return [_product_row(row) for row in db.memory_products.values() if row.shop_id == shop_id]


def save_product(
    shop_id: str,
    *,
    product_id: str,
    title: str,
    category: str,
    price: str,
    url: str,
    description: str,
    commission: str = "",
    why_chosen: str = "",
    value_to_creators: str = "",
    image_url: str = "",
    source: str = "manual",
    as_draft: bool = False,
) -> str:
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title is required")
    if len(title) > 500:
        raise HTTPException(status_code=422, detail="Title is too long")
    category = (category or "").strip().lower()
    if category:
        services.validate_category_lists([category])
    amount = catalogue.parse_price(price)
    destination = catalogue.http_url(url)
    if (url or "").strip() and destination is None:
        raise HTTPException(status_code=422, detail="URL must be an http or https address")
    image = catalogue.http_url(image_url)
    if (image_url or "").strip() and image is None:
        raise HTTPException(status_code=422, detail="Image URL must be an http or https address")
    why = (why_chosen or "").strip()
    value = (value_to_creators or "").strip()
    if as_draft:
        rate = catalogue.parse_commission(commission, required=False)
    else:
        rate = catalogue.require_curation(why, value, commission)
    if source not in {"manual", "csv", "api", "shopify"}:
        source = "manual"

    def _write(product, shop, *, creating: bool, session, before: Optional[int]) -> str:
        product.title = title
        product.category = category or None
        product.price = amount
        product.description_short = (description or "").strip() or None
        product.url = destination
        product.why_chosen = why or None
        product.value_to_creators = value or None
        product.image_url = image
        attributes = dict(getattr(product, "attributes", None) or {})
        if destination:
            attributes["url"] = destination
        elif "url" in attributes:
            attributes.pop("url", None)
        product.attributes = attributes
        if creating:
            product.source = source
        elif not getattr(product, "source", None):
            product.source = "manual"
        if rate is not None or not as_draft:
            product.commission_rate = rate
        product.updated_at = utcnow()
        if creating and shop is not None and before is not None:
            events.note_catalogue_threshold(shop, before, before + 1)
        return product.id

    if db.configured():
        with db.session_scope() as session:
            product = session.get(Product, product_id) if product_id else None
            creating = product is None
            shop = session.get(Shop, shop_id)
            before = events.catalogue_count(shop, session) if creating and shop is not None else None
            if creating:
                product = Product(
                    id=str(uuid.uuid4()),
                    shop_id=shop_id,
                    title=title,
                    description_short=None,
                    description_long=None,
                    category=None,
                    images=[],
                    price=None,
                    currency="AUD",
                    tags=[],
                    attributes={},
                    claims=[],
                    source=source,
                    created_at=utcnow(),
                )
                session.add(product)
            elif product.shop_id != shop_id:
                raise HTTPException(status_code=404, detail="Product not found")
            session.flush()
            return _write(product, shop, creating=creating, session=session, before=before)
    if product_id and product_id in db.memory_products:
        product = db.memory_products[product_id]
        if product.shop_id != shop_id:
            raise HTTPException(status_code=404, detail="Product not found")
        creating = False
        before = None
    else:
        before = events.catalogue_count(db.memory_shops.get(shop_id), None) if db.memory_shops.get(shop_id) else None
        product = SimpleNamespace(
            id=str(uuid.uuid4()),
            shop_id=shop_id,
            title=title,
            description_short=None,
            description_long=None,
            category=None,
            subcategory=None,
            images=[],
            price=None,
            currency="AUD",
            tags=[],
            attributes={},
            claims=[],
            commission_rate=None,
            source=source,
            url=None,
            why_chosen=None,
            value_to_creators=None,
            image_url=None,
            created_at=utcnow(),
            updated_at=None,
        )
        db.memory_products[product.id] = product
        creating = True
    shop = db.memory_shops.get(shop_id)
    return _write(product, shop, creating=creating, session=None, before=before)


def run_match(shop_id: str, product_id: str) -> dict:
    stored = _owned_product(shop_id, product_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if not catalogue.product_is_complete(stored):
        raise HTTPException(
            status_code=422,
            detail=(
                "Complete this product before it can be matched or used in a brief. "
                "Add what makes it special, what's in it for Creatrs, and the commission you'll pay."
            ),
        )
    loaded = services.load_match_inputs(
        product=None,
        product_id=product_id,
        creators=None,
        all_onboarded=True,
        shop_id=shop_id,
    )
    if loaded["shop_id"] != shop_id:
        raise HTTPException(status_code=404, detail="Product not found")
    outcome = scoring.rank_creators(
        loaded["product"],
        loaded["creators"],
        shop=loaded["shop"],
        optimise_for="balanced",
        limit=10,
    )
    shortlist_id, item_ids = services.persist_rules_match(
        shop_id=shop_id,
        product_id=loaded["product_id"],
        optimise_for="balanced",
        excluded=outcome["excluded"],
        matches=[
            {
                "creator_id": (row["creator"].get("influencer_id") or row["creator"].get("id")),
                "score": row["score"],
                "components": row["components"],
                "estimates": row["estimates"],
                "reasons": row["reasons"],
            }
            for row in outcome["matches"]
        ],
    )
    for row, item_id in zip(outcome["matches"], item_ids):
        row["shortlist_item_id"] = item_id
    outcome["shortlist_id"] = shortlist_id
    return outcome


def decide(shop_id: str, item_id: str, event_name: str, reason: Optional[str] = None) -> dict:
    if not _item_belongs_to_shop(item_id, shop_id):
        raise HTTPException(status_code=404, detail="Shortlist item not found")
    return services.decide_shortlist_item(item_id, event_name, reason)


def accepted_for_shop(shop_id: str) -> list[dict]:
    rows = []
    for item in _accepted_items(shop_id=shop_id):
        link = _link_for(item["creator_id"], item["product_id"])
        item["discount_code"] = link.get("discount_code") if link else None
        item["tracking_path"] = link.get("tracking_path") if link else None
        item["direct_url"] = link.get("direct_url") if link else None
        item["platform_links"] = link.get("platform_links") if link else None
        rows.append(item)
    return rows


def creator_profile(creator_id: str) -> dict:
    creator = services.fetch_creator(creator_id)
    if creator is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    profile = creator.get("influencer") or {}
    stored = db.memory_creator_objects.get(creator_id)
    number = getattr(stored, "founding_number", None)
    if db.configured():
        with db.session_scope() as session:
            row = session.get(Creator, creator_id)
            number = None if row is None else row.founding_number
    return {
        "id": creator_id,
        "handle": profile.get("handle"),
        "name": profile.get("name"),
        "active_categories": list(profile.get("active_categories") or []),
        "desired_categories": list(profile.get("desired_categories") or []),
        "founding_number": number,
    }


def update_creator_categories(creator_id: str, active: list[str], desired: list[str]) -> None:
    services.validate_category_lists(active, desired)
    if db.configured():
        with db.session_scope() as session:
            creator = session.get(Creator, creator_id)
            if creator is None:
                raise HTTPException(status_code=404, detail="Creator not found")
            creator.active_categories = active
            creator.desired_categories = desired
            profile = dict(creator.profile or {})
            profile["active_categories"] = active
            profile["desired_categories"] = desired
            creator.profile = profile
        return
    record = db.memory_creators.get(creator_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    record["active_categories"] = active
    record["desired_categories"] = desired
    stored = db.memory_creator_objects.get(creator_id)
    if stored is not None:
        stored.active_categories = active


def creator_offers(creator_id: str) -> list[dict]:
    offers = []
    items = _offer_items(creator_id, pending=True) + _accepted_items(creator_id=creator_id)
    for item in items:
        brief = _brief_for(item)
        points = []
        if brief and brief.get("content"):
            try:
                parsed = json.loads(brief["content"])
                points = list(parsed.get("talking_points") or [])
            except (TypeError, json.JSONDecodeError):
                points = []
        item["talking_points"] = points
        item["brief"] = brief.get("content") if brief else None
        link = _link_for(item.get("creator_id"), item.get("product_id"))
        item["discount_code"] = link.get("discount_code") if link else None
        item["tracking_path"] = link.get("tracking_path") if link else None
        item["direct_url"] = link.get("direct_url") if link else None
        item["platform_links"] = link.get("platform_links") if link else None
        offers.append(item)
    return offers


def creator_links(creator_id: str) -> list[dict]:
    return attribution.list_links(shop_id=None, creator_id=creator_id)


_ICONS = {
    "home": '<path d="M4 11 12 4l8 7v9a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1z"/>',
    "user": '<circle cx="12" cy="8" r="3"/><path d="M5 19c1.5-3 3.8-4.5 7-4.5S17.5 16 19 19"/>',
    "star": '<path d="m12 3 2.4 5.2L20 9.2l-4 3.9.9 5.7L12 16.8 7.1 18.8 8 13.1 4 9.2l5.6-.9z"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
    "check": '<path d="m5 12 5 5L20 7"/>',
    "flag": '<path d="M6 4v16M6 5h11l-2 4 2 4H6"/>',
    "card": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.1-.1l-2 2a5 5 0 0 0 7.1 7.1l1.1-1.1"/>',
}
_NAV_ICON = {
    "dashboard": "home",
    "profile": "user",
    "products": "star",
    "find": "spark",
    "offers": "spark",
    "accepted": "check",
    "campaigns": "flag",
    "billing": "card",
    "links": "link",
}


def _icon(name: str) -> str:
    return (
        '<svg class="nav-ico" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" '
        'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f"{_ICONS[name]}</svg>"
    )


def _logo(mark: str) -> str:
    return (
        f'<span class="logo"><svg width="34" height="24" viewBox="0 0 34 24" aria-hidden="true">'
        f'<circle cx="12" cy="12" r="10" fill="none" stroke="{mark}" stroke-width="3"/>'
        '<circle cx="22" cy="12" r="10" fill="none" stroke="#C2127F" stroke-width="3"/>'
        '<path d="M17 3.3a10 10 0 0 1 0 17.4 10 10 0 0 1 0-17.4z" fill="url(#plantrLens)"/></svg>'
        '<span>Plantr <span class="collab">Collab</span></span></span>'
    )


def _empty(heading: str, body: str, button: str = "", href: str = "") -> str:
    action = f'<a class="button" href="{html.escape(href)}">{html.escape(button)}</a>' if button and href else ""
    return (
        f'<div class="empty-art" id="first-product"><div class="orb" aria-hidden="true"></div>'
        f"<h2>{html.escape(heading)}</h2><p>{html.escape(body)}</p>{action}</div>"
    )


def _hero(heading: str, intro: str, *, badge: str = "", tag: str = "") -> str:
    flag = f'<span class="hero-badge">{html.escape(badge)}</span>' if badge else ""
    who = f'<span class="hero-tag">{html.escape(tag)}</span>' if tag else ""
    return (
        f'<section class="hero">{flag}{who}<h1>{html.escape(heading)}</h1>'
        f"<p>{html.escape(intro)}</p></section>"
    )


def _aside(items, current: str, csrf: str, logout: str, shop_or_none, *, seller: bool) -> str:
    links = []
    for key, href, label in items:
        on = ' class="on" aria-current="page"' if key == current else ""
        links.append(f'<a href="{href}"{on}>{_icon(_NAV_ICON.get(key, "home"))}{html.escape(label)}</a>')
    pill = ""
    if seller and shop_or_none:
        plan = billing_config.tier_label("seller", str(shop_or_none.get("plan_tier") or "starter"))
        title, detail = portal_copy.pilot_pill(plan, is_pilot=bool(shop_or_none.get("is_pilot")))
        pill = f'<div class="plan-pill"><b>{html.escape(title)}</b>{html.escape(detail)}</div>'
    sign_out = (
        f'<form method="post" action="{html.escape(logout)}">'
        f'<input type="hidden" name="csrf_token" value="{html.escape(csrf)}">'
        '<button class="secondary" type="submit">Sign out</button></form>'
    )
    mark = "#0A64E0" if seller else "#5B2EFF"
    return (
        "<aside><div class=\"logo-lock\">"
        '<svg width="0" height="0" aria-hidden="true"><defs><linearGradient id="plantrLens" x1="0" x2="1">'
        '<stop offset="0" stop-color="#5B2EFF"/><stop offset="1" stop-color="#C2127F"/></linearGradient></defs></svg>'
        f"{_logo(mark)}</div>"
        f'<p class="value-strip">{html.escape(portal_copy.VALUE_STRIP)}</p>'
        f"<nav>{''.join(links)}</nav>{pill}{sign_out}</aside>"
    )


def render_seller(session: Optional[dict], page: str, *, base_url: str = "", **data) -> HTMLResponse:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    if session is None:
        token = auth.issue_form_token("seller-login")
        body = f"""
        <h1>Seller sign in</h1>
        <form method="post" action="/seller/login">
          <input type="hidden" name="csrf_token" value="{html.escape(token)}">
          <label for="email">Email</label>
          <input id="email" name="email" type="email" autocomplete="username" required>
          <label for="password">Password</label>
          <input id="password" name="password" type="password" autocomplete="current-password" required>
          <button type="submit">Sign in</button>
        </form>
        """
        return HTMLResponse(_layout("Seller", body, None, None, theme="seller"))
    shop = shop_profile(session["subject_id"])
    csrf = html.escape(session["csrf"])
    data["locked"] = not stripe_billing.commerce_open("seller", session["subject_id"])
    banner = ""
    if session.get("impersonator"):
        label = (shop.get("business_name") or shop.get("name") or "this seller").strip() or "this seller"
        banner = (
            "<div class='view-as-bar' role='status'>"
            f"<p>Viewing as {html.escape(label)}, read-only.</p>"
            "<form method='post' action='/seller/logout'>"
            f"<input type='hidden' name='csrf_token' value='{csrf}'>"
            "<button type='submit'>Exit</button></form></div>"
        )
    if data["locked"] and page != "profile":
        banner += f"<p class='banner'>{html.escape(stripe_billing.SUBSCRIBE_PROMPT)}</p>"
    aside = _aside(portal_copy.SELLER_NAV, page, session["csrf"], "/seller/logout", shop, seller=True)
    content = banner + _seller_body(page, shop, csrf, base_url=base_url, **data)
    return HTMLResponse(_layout(shop["name"], content, "/seller/logout", session["csrf"], theme="seller", aside=aside))


def render_creator(session: Optional[dict], page: str, *, base_url: str = "", **data) -> HTMLResponse:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    if session is None:
        token = auth.issue_form_token("creator-login")
        body = f"""
        <h1>Creatr sign in</h1>
        <form method="post" action="/creator/login">
          <input type="hidden" name="csrf_token" value="{html.escape(token)}">
          <label for="email">Email</label>
          <input id="email" name="email" type="email" autocomplete="username" required>
          <label for="password">Password</label>
          <input id="password" name="password" type="password" autocomplete="current-password" required>
          <button type="submit">Sign in</button>
        </form>
        """
        return HTMLResponse(_layout("Creatr", body, None, None, theme="creatr"))
    profile = creator_profile(session["subject_id"])
    csrf = html.escape(session["csrf"])
    data["locked"] = not stripe_billing.commerce_open("creator", session["subject_id"])
    if session.get("impersonator"):
        label = (profile.get("handle") or profile.get("name") or "this Creatr").strip() or "this Creatr"
        banner = (
            "<div class='view-as-bar' role='status'>"
            f"<p>Viewing as {html.escape(label)}, read-only.</p>"
            "<form method='post' action='/creator/logout'>"
            f"<input type='hidden' name='csrf_token' value='{csrf}'>"
            "<button type='submit'>Exit</button></form></div>"
        )
    else:
        banner = ""
    if data["locked"] and page not in {"profile", "links"}:
        banner += f"<p class='banner'>{html.escape(stripe_billing.SUBSCRIBE_PROMPT)}</p>"
    aside = _aside(portal_copy.CREATR_NAV, page, session["csrf"], "/creator/logout", None, seller=False)
    content = banner + _creator_body(page, profile, csrf, base_url=base_url, **data)
    return HTMLResponse(_layout(profile.get("handle") or "Creatr", content, "/creator/logout", session["csrf"], theme="creatr", aside=aside))


def _profile_form(shop: dict, csrf: str, error: str) -> str:
    current = shop.get("primary_category") or ""
    options = ["<option value=''>Choose a category</option>"]
    for slug, label in taxonomy.TOP_LEVEL_CATEGORIES:
        selected = " selected" if slug == current else ""
        options.append(f"<option value='{html.escape(slug)}'{selected}>{html.escape(label)}</option>")
    banner = f"<p class='banner'>{html.escape(error)}</p>" if error else ""
    copy = portal_copy.SELLER_PROFILE
    name = (shop.get("business_name") or shop.get("name") or "").strip()
    intro = _hero(copy["heading"], copy["subhead"] + " The business name is the trading name.", tag=name)
    empty = ""
    if not (shop.get("business_name") or shop.get("store_url")):
        empty = _empty(portal_copy.SELLER_PROFILE_EMPTY["heading"], portal_copy.SELLER_PROFILE_EMPTY["body"])
    return f"""
    {intro}
    {empty}
    {banner}
    <form class="sheet" method="post" action="/seller/profile">
      <input type="hidden" name="csrf_token" value="{csrf}">
      <label>{html.escape(copy["business"])} <input name="business_name" value="{html.escape(shop.get('business_name') or '')}" autocomplete="organization"></label>
      <label>{html.escape(copy["abn"])} <input name="abn" inputmode="numeric" value="{html.escape(shop.get('abn') or '')}"></label>
      <label>{html.escape(copy["category"])} <select name="primary_category">{''.join(options)}</select></label>
      <label>{html.escape(copy["shop"])} <input name="store_url" type="url" value="{html.escape(shop.get('store_url') or '')}" placeholder="https://"></label>
      <label>{html.escape(copy["shopify"])} <input name="shopify_domain" value="{html.escape(shop.get('shopify_domain') or '')}" placeholder="the-store.myshopify.com"></label>
      <button type="submit">{html.escape(copy["button"])}</button>
    </form>
    """


def _missing_details(row: dict) -> int:
    missing = 0
    if not str(row.get("why_chosen") or "").strip():
        missing += 1
    if not str(row.get("value_to_creators") or "").strip():
        missing += 1
    if row.get("commission_rate") is None:
        missing += 1
    return missing


def _aud(amount) -> str:
    if amount is None or amount == "":
        return "—"
    return f"A${float(amount):,.2f}"


def _products_page(shop: dict, csrf: str, data: dict) -> str:
    copy = portal_copy.PRODUCTS
    products = data.get("products") or []
    count = len(products)
    cards = []
    for index, row in enumerate(products):
        missing = _missing_details(row)
        chip = ""
        if missing:
            label = copy["detail"] if missing == 1 else copy["details"].format(n=missing)
            chip = f'<span class="chip warn">{html.escape(label)}</span>'
        finish = ""
        if row.get("needs_completion") or missing:
            finish = f'<a href="/seller/products?edit={html.escape(row["id"])}">{html.escape(copy["finish"])}</a>'
        image = row.get("image_url") or ""
        if image.startswith("http://") or image.startswith("https://"):
            thumb = f'<img class="thumb" alt="" src="{html.escape(image)}">'
        else:
            thumb = f'<div class="thumb t{index % 4}" aria-hidden="true"></div>'
        cards.append(
            '<article class="product-card">'
            f"{thumb}<div><h3>{html.escape(row['title'])}</h3>"
            f'<span class="chip">{html.escape(taxonomy.TOP_LEVEL_CATEGORIES and next((label for slug, label in taxonomy.TOP_LEVEL_CATEGORIES if slug == (row.get("category") or "")), row.get("category") or "Category"))}</span>'
            f"{chip}</div><div class=\"price\">{html.escape(_aud(row.get('price')))}{('<small>' + finish + '</small>') if finish else ''}</div></article>"
        )
    warning = catalogue.fee_warning(count)
    warning_html = f"<p class='banner' id='catalogue-fee'>{html.escape(warning)}</p>" if warning else ""
    empty = ""
    if count == 0:
        empty = _empty(portal_copy.PRODUCTS_EMPTY["heading"], portal_copy.PRODUCTS_EMPTY["body"])
    edit = data.get("edit") or {}
    form = ""
    if not data.get("locked"):
        commission = edit.get("commission_rate")
        commission_value = "" if commission is None else str(commission)
        options = ['<option value="">Choose a category</option>']
        current = edit.get("category") or ""
        for slug, label in taxonomy.TOP_LEVEL_CATEGORIES:
            selected = " selected" if slug == current else ""
            options.append(f'<option value="{html.escape(slug)}"{selected}>{html.escape(label)}</option>')
        button = copy["save"] if edit else copy["button"]
        form = f"""
        <form class="sheet" method="post" action="/seller/products">
          <h2>{html.escape(copy["edit_form"] if edit else copy["form"])}</h2>
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="product_id" value="{html.escape(edit.get('id') or '')}">
          <label for="title">{html.escape(copy["name"])}</label>
          <input id="title" name="title" value="{html.escape(edit.get('title') or '')}" placeholder="{html.escape(copy["name_placeholder"])}" required>
          <div class="two">
            <div><label for="category">{html.escape(copy["category"])}</label>
            <select id="category" name="category">{''.join(options)}</select></div>
            <div><label for="price">{html.escape(copy["price"])}</label>
            <input id="price" name="price" value="{html.escape('' if edit.get('price') is None else str(edit.get('price')))}" placeholder="{html.escape(copy["price_placeholder"])}"></div>
          </div>
          <label for="url">{html.escape(copy["url"])}</label>
          <input id="url" name="url" type="text" value="{html.escape(edit.get('url') or '')}" placeholder="{html.escape(copy["url_placeholder"])}">
          <input type="hidden" name="image_url" value="{html.escape(edit.get('image_url') or '')}">
          <label for="why">{html.escape(copy["special"])}</label>
          <p class="help">{html.escape(copy["special_help"])}</p>
          <textarea id="why" name="why_chosen" required>{html.escape(edit.get('why_chosen') or '')}</textarea>
          <p class="help guard" id="claim-guard" hidden>{html.escape(copy["guard"])}</p>
          <label for="value">{html.escape(copy["creatr_value"])}</label>
          <p class="help">{html.escape(copy["creatr_help"])}</p>
          <textarea id="value" name="value_to_creators" required>{html.escape(edit.get('value_to_creators') or '')}</textarea>
          <label for="commission">{html.escape(copy["commission"])}</label>
          <p class="help">{html.escape(portal_copy.COMMISSION_LINE)}</p>
          <input id="commission" name="commission" value="{html.escape(commission_value)}" required>
          <input type="hidden" name="description" value="{html.escape(edit.get('description') or '')}">
          <button class="btn" type="submit">{html.escape(button)}</button>
        </form>
        <form class="sheet" method="post" action="/seller/products/import" enctype="multipart/form-data">
          <h2>Bring a list</h2>
          <p class="help">Tick the rows you want. Nothing starts ticked. A product is ready for matching when the story and the commission are in.</p>
          <p><a href="/seller/products/csv-template">Download the template</a></p>
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Products CSV <input type="file" name="file" accept=".csv,text/csv" required></label>
          <button type="submit">Preview CSV</button>
        </form>
        """
    badge = portal_copy.HERO_BADGE if shop.get("is_pilot") else ""
    tag = (shop.get("business_name") or shop.get("name") or "").strip()
    cards_html = "".join(cards) if cards else empty
    return f"""
    {_hero(copy["heading"], copy["intro"], badge=badge, tag=tag)}
    {warning_html}
    <div class="split">
      <section>
        <h2 id="product-count">{count} {"hero product" if count == 1 else "hero products"}</h2>
        <div class="cards">{cards_html}</div>
      </section>
      {form}
    </div>
    <script>
    (function(){{
      var box = document.getElementById("why");
      var note = document.getElementById("claim-guard");
      if (!box || !note) return;
      var flags = /\\b(best|greatest|guaranteed|exclusive|miracle|cure|clinically|before and after|customers rave|love it)\\b/i;
      box.addEventListener("input", function(){{ note.hidden = !flags.test(box.value); }});
    }})();
    </script>
    """


def _csv_preview(csrf: str, rows: list[dict]) -> str:
    body = []
    for index, row in enumerate(rows):
        if row.get("ok"):
            tick = f"<input type='checkbox' name='pick' value='{index}'>"
        else:
            tick = ""
        hidden = ""
        if row.get("ok"):
            hidden = (
                f'<input type="hidden" name="title_{index}" value="{html.escape(row.get("title") or "")}">'
                f'<input type="hidden" name="url_{index}" value="{html.escape(row.get("url") or "")}">'
                f'<input type="hidden" name="price_{index}" value="{html.escape(row.get("price_text") or "")}">'
                f'<input type="hidden" name="category_{index}" value="{html.escape(row.get("category") or "")}">'
                f'<input type="hidden" name="image_url_{index}" value="{html.escape(row.get("image_url") or "")}">'
                f'<input type="hidden" name="commission_{index}" value="{html.escape(row.get("commission_text") or "")}">'
            )
        body.append(
            "<tr>"
            f"<td>{tick}{hidden}</td>"
            f"<td>{html.escape(row.get('title') or '')}</td>"
            f"<td>{html.escape(row.get('url') or '')}</td>"
            f"<td>{html.escape(row.get('price_text') or '')}</td>"
            f"<td>{html.escape(row.get('category') or '')}</td>"
            f"<td>{html.escape(row.get('error') or '')}</td>"
            "</tr>"
        )
    return f"""
    <h1>Choose products</h1>
    <p>Tick the rows you want to add. Nothing is ticked to start with. You can save them as drafts, or add why you chose each one before they can be matched.</p>
    <form method="post" action="/seller/products/import/drafts">
      <input type="hidden" name="csrf_token" value="{csrf}">
      <input type="hidden" name="row_count" value="{len(rows)}">
      <table>
        <thead><tr><th>Add</th><th>Title</th><th>URL</th><th>Price</th><th>Category</th><th>Notes</th></tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table>
      <button type="submit">Save ticked rows as drafts</button>
      <button type="submit" formaction="/seller/products/import/details">Add why and value for ticked rows</button>
    </form>
    """


def _csv_details(csrf: str, rows: list[dict], error: str) -> str:
    banner = f"<p class='banner'>{html.escape(error)}</p>" if error else ""
    blocks = []
    for index, row in enumerate(rows):
        blocks.append(
            f"""
            <fieldset>
              <legend>{html.escape(row.get('title') or 'Product')}</legend>
              <input type="hidden" name="title_{index}" value="{html.escape(row.get('title') or '')}">
              <input type="hidden" name="url_{index}" value="{html.escape(row.get('url') or '')}">
              <input type="hidden" name="price_{index}" value="{html.escape(row.get('price_text') or '')}">
              <input type="hidden" name="category_{index}" value="{html.escape(row.get('category') or '')}">
              <input type="hidden" name="image_url_{index}" value="{html.escape(row.get('image_url') or '')}">
              <label>Why you chose it <textarea name="why_chosen_{index}" required>{html.escape(row.get('why_chosen') or '')}</textarea></label>
              <label>Value for Creatrs <textarea name="value_to_creators_{index}" required>{html.escape(row.get('value_to_creators') or '')}</textarea></label>
              <label>Commission percent <input name="commission_{index}" value="{html.escape(row.get('commission_text') or '')}" required></label>
            </fieldset>
            """
        )
    return f"""
    <h1>Finish each product</h1>
    <p>These products are not used for matching until each one has a reason, the value for Creatrs, and a commission.</p>
    {banner}
    <form method="post" action="/seller/products/import/save">
      <input type="hidden" name="csrf_token" value="{csrf}">
      <input type="hidden" name="finish" value="1">
      <input type="hidden" name="row_count" value="{len(rows)}">
      {''.join(blocks)}
      <button type="submit">Save products</button>
    </form>
    """


def _picked_rows(form, *, all_rows: bool = False) -> list[dict]:
    try:
        count = int(str(form.get("row_count") or "0"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The preview could not be read") from exc
    if count < 0 or count > catalogue.MAX_CSV_ROWS:
        raise HTTPException(status_code=422, detail="A CSV can list at most 500 products")
    if all_rows:
        indexes = {str(index) for index in range(count)}
    else:
        indexes = {str(value) for value in form.getlist("pick")}
    if not indexes:
        raise HTTPException(status_code=422, detail="Tick at least one product")
    rows = []
    for index in range(count):
        if str(index) not in indexes:
            continue
        raw = {
            "title": form.get(f"title_{index}"),
            "url": form.get(f"url_{index}"),
            "price": form.get(f"price_{index}"),
            "category": form.get(f"category_{index}"),
            "image_url": form.get(f"image_url_{index}"),
            "commission": form.get(f"commission_{index}"),
        }
        parsed = catalogue.normalise_import_row(raw)
        if not parsed["ok"]:
            raise HTTPException(status_code=422, detail=parsed["error"] or "That row cannot be imported")
        parsed["why_chosen"] = str(form.get(f"why_chosen_{index}") or "")
        parsed["value_to_creators"] = str(form.get(f"value_to_creators_{index}") or "")
        rows.append(parsed)
    if not rows:
        raise HTTPException(status_code=422, detail="Tick at least one product")
    return rows


def save_imported_products(shop_id: str, rows: list[dict], *, as_draft: bool) -> int:
    if not as_draft:
        for row in rows:
            catalogue.require_curation(row.get("why_chosen"), row.get("value_to_creators"), row.get("commission_text"))
    for row in rows:
        commission = row.get("commission_text") or ""
        why = row.get("why_chosen") or ""
        value = row.get("value_to_creators") or ""
        save_product(
            shop_id,
            product_id="",
            title=row.get("title") or "",
            category=row.get("category") or "",
            price=row.get("price_text") or "",
            url=row.get("url") or "",
            description="",
            commission=commission,
            why_chosen=why,
            value_to_creators=value,
            image_url=row.get("image_url") or "",
            source="csv",
            as_draft=as_draft,
        )
    return len(rows)


def _seller_body(page: str, shop: dict, csrf: str, *, base_url: str = "", **data) -> str:
    if page == "dashboard":
        return _seller_dashboard(shop)
    if page == "profile":
        return _profile_form(shop, csrf, data.get("profile_error") or "")
    if page == "products":
        return _products_page(shop, csrf, data)
    if page == "csv_preview":
        return _csv_preview(csrf, data.get("rows") or [])
    if page == "csv_details":
        return _csv_details(csrf, data.get("rows") or [], data.get("import_error") or "")
    if page == "find":
        ready = [row for row in data.get("products") or [] if row.get("ready")]
        options = "".join(
            f"<option value=\"{html.escape(row['id'])}\">{html.escape(row['title'])}</option>"
            for row in ready
        )
        cards = []
        for match in data.get("matches") or []:
            creator = match.get("creator") or {}
            handle = html.escape(str(creator.get("handle") or creator.get("influencer_id") or ""))
            reasons = "".join(f"<li>{html.escape(str(reason))}</li>" for reason in match.get("reasons") or [])
            estimates = html.escape(json.dumps(match.get("estimates") or {}, default=str))
            item_id = html.escape(str(match.get("shortlist_item_id") or ""))
            actions = ""
            if item_id and not data.get("locked"):
                actions = f"""
                <form method="post" action="/seller/shortlist-items/{item_id}/accept"><input type="hidden" name="csrf_token" value="{csrf}"><button type="submit">{html.escape(portal_copy.FIND["invite"])}</button></form>
                <form method="post" action="/seller/shortlist-items/{item_id}/decline"><input type="hidden" name="csrf_token" value="{csrf}"><button class="secondary" type="submit">{html.escape(portal_copy.FIND["pass"])}</button></form>
                """
            cards.append(f"<article><h3>{handle}</h3><p>Score {html.escape(str(match.get('score')))}</p><ul>{reasons}</ul><pre>{estimates}</pre>{actions}</article>")
        match_form = ""
        if not data.get("locked") and ready:
            match_form = f"""
        <form method="post" action="/seller/find">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Product <select name="product_id">{options}</select></label>
          <button type="submit">{html.escape(portal_copy.FIND["run"])}</button>
        </form>
        """
        waiting = ""
        if not ready:
            waiting = f"<p>{html.escape(portal_copy.FIND['need_product'])}</p>"
        body = "".join(cards) or _empty(portal_copy.FIND_EMPTY["heading"], portal_copy.FIND_EMPTY["body"], portal_copy.FIND_EMPTY["button"], "/seller/campaigns")
        return f"""
        {_hero(portal_copy.FIND["heading"], portal_copy.FIND["subhead"])}
        <p>{html.escape(portal_copy.FIND["reach"])}</p>
        {waiting}
        {match_form.replace("Run match", portal_copy.FIND["run"]) if match_form else ""}
        {body}
        <p class="help">{html.escape(portal_copy.FIND["footnote"])}</p>
        """
    rows = []
    for item in data.get("accepted") or []:
        rows.append(
            "<li>"
            f"{html.escape(str(item.get('handle') or item.get('creator_id')))} · "
            f"{html.escape(str(item.get('product_title') or ''))}"
            f"{_tracking_block(item.get('discount_code'), item.get('tracking_path'), base_url, item.get('platform_links'), item.get('direct_url'))}"
            f"{_abn_note(item)}"
            "</li>"
        )
    if page == "campaigns":
        rows = "".join(f"<li>{html.escape(row['name'])}</li>" for row in data.get("campaigns") or [])
        form = ""
        if not data.get("locked"):
            form = f"""
        <form method="post" action="/seller/campaigns">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>{html.escape(portal_copy.CAMPAIGNS["name"])} <input name="name" required></label>
          <button type="submit">{html.escape(portal_copy.CAMPAIGNS["button"])}</button>
        </form>
        """
        empty = _empty(portal_copy.CAMPAIGNS_EMPTY["heading"], portal_copy.CAMPAIGNS_EMPTY["body"]) if not rows else ""
        return f"""
        {_hero(portal_copy.CAMPAIGNS["heading"], portal_copy.CAMPAIGNS["subhead"])}
        {empty}
        <ul class="plain">{rows}</ul>
        {form}
        """
    if page == "billing":
        return _billing_body("seller", data.get("billing") or {}, csrf, "/seller/billing")
    listed = "".join(rows)
    empty = "" if rows else _empty(portal_copy.ACCEPTED_EMPTY["heading"], portal_copy.ACCEPTED_EMPTY["body"], portal_copy.ACCEPTED_EMPTY["button"], "/seller/find")
    return f"""
    {_hero(portal_copy.ACCEPTED["heading"], portal_copy.ACCEPTED["subhead"])}
    {empty}
    <ul class="plain">{listed}</ul>
    """


def _seller_dashboard(shop: dict) -> str:
    products = seller_products(shop["id"])
    live = sum(1 for row in products if row.get("ready"))
    need = len(products) - live
    accepted = len(accepted_for_shop(shop["id"]))
    campaigns = 0
    if db.configured():
        from sqlalchemy import func, select
        from models import Campaign

        with db.session_scope() as session:
            campaigns = int(session.scalar(select(func.count()).select_from(Campaign).where(Campaign.shop_id == shop["id"])) or 0)
    else:
        campaigns = sum(1 for row in db.memory_campaigns if getattr(row, "shop_id", None) == shop["id"])
    name = (shop.get("business_name") or shop.get("name") or "there").strip()
    copy = portal_copy.SELLER_DASHBOARD
    plan = billing_config.tier_label("seller", str(shop.get("plan_tier") or "starter"))
    if shop.get("is_pilot"):
        plan = f"{plan} · Pilot"
    empty = ""
    if not products:
        state = portal_copy.SELLER_DASHBOARD_EMPTY
        empty = _empty(state["heading"], state["body"], state["button"], "/seller/products")
    cards = f"""
    <div class="stat-grid">
      <a class="stat" href="/seller/products"><span>Hero products</span><strong>{html.escape(copy["products"].format(live=live, need=need))}</strong></a>
      <a class="stat" href="/seller/accepted"><span>Creatrs on board</span><strong>{html.escape(copy["crew"].format(n=accepted))}</strong></a>
      <a class="stat" href="/seller/campaigns"><span>Live campaigns</span><strong>{html.escape(copy["campaigns"].format(n=campaigns))}</strong></a>
      <div class="stat"><span>Your plan</span><strong>{html.escape(copy["plan"].format(plan=plan))}</strong></div>
    </div>
    <p>{html.escape(copy["next"])}</p>
    <p class="actions">
      <a class="button" href="/seller/find">{html.escape(copy["find"])}</a>
      <a class="button" href="/seller/products">{html.escape(copy["add"])}</a>
      <a class="button secondary" href="/seller/campaigns">{html.escape(copy["campaign"])}</a>
    </p>
    """
    return _hero(copy["heading"].format(name=name), copy["subhead"]) + empty + cards


def _creator_body(page: str, profile: dict, csrf: str, *, base_url: str = "", **data) -> str:
    if page == "dashboard":
        name = (profile.get("name") or profile.get("handle") or "there").strip()
        copy = portal_copy.CREATR_DASHBOARD
        offers = creator_offers(profile["id"]) if profile.get("id") else []
        empty = ""
        if not offers:
            state = portal_copy.CREATR_DASHBOARD_EMPTY
            empty = _empty(state["heading"], state["body"], state["button"], "/creator/profile")
        actions = (
            f'<p class="actions"><a class="button" href="/creator/offers">{html.escape(copy["matches"])}</a>'
            f'<a class="button secondary" href="/creator/links">{html.escape(copy["links"])}</a></p>'
        )
        return _hero(copy["heading"].format(name=name), copy["subhead"]) + empty + actions
    if page == "profile":
        active = ", ".join(profile.get("active_categories") or [])
        desired = ", ".join(profile.get("desired_categories") or [])
        number = profile.get("founding_number")
        copy = portal_copy.CREATR_PROFILE
        badge = f"<p class='chip'>{html.escape(copy['foundr'])} {html.escape(copy['perks_note'])}</p>" if number else ""
        name = (profile.get("name") or profile.get("handle") or "there").strip()
        empty = ""
        if not active:
            empty = _empty(portal_copy.CREATR_PROFILE_EMPTY["heading"], portal_copy.CREATR_PROFILE_EMPTY["body"])
        return badge + _hero(copy["heading"], copy["subhead"], tag=name) + empty + f"""
        <form class="sheet" method="post" action="/creator/profile">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>{html.escape(copy["active"])} <input name="active_categories" value="{html.escape(active)}"></label>
          <label>{html.escape(copy["desired"])} <input name="desired_categories" value="{html.escape(desired)}"></label>
          <button type="submit">{html.escape(copy["button"])}</button>
        </form>
        """
    if page == "offers":
        blocks = []
        for offer in data.get("offers") or []:
            points = "".join(f"<li>{html.escape(str(point))}</li>" for point in offer.get("talking_points") or [])
            rate = offer.get("commission_rate")
            rate_text = (
                portal_copy.MATCHES["commission"].format(rate=rate)
                if rate is not None
                else portal_copy.MATCHES["commission_missing"]
            )
            accept = ""
            if not offer.get("accepted_at") and not data.get("locked"):
                accept = (
                    f"<form method='post' action='/creator/offers/{html.escape(str(offer.get('id')))}/accept'>"
                    f"<input type='hidden' name='csrf_token' value='{csrf}'>"
                    f"<button type='submit'>{html.escape(portal_copy.MATCHES['in'])}</button></form>"
                )
            blocks.append(
                f"<article><h2>{html.escape(str(offer.get('product_title') or 'Product'))}</h2>"
                f"<p>{html.escape(str(offer.get('shop_name') or ''))}</p>"
                f"<p>{html.escape(rate_text)}</p><p class='help'>{html.escape(portal_copy.DISCLOSURE)}</p>"
                f"<ul>{points or '<li>No talking points yet.</li>'}</ul>"
                f"{accept}"
                f"{_tracking_block(offer.get('discount_code'), offer.get('tracking_path'), base_url, offer.get('platform_links'), offer.get('direct_url'))}</article>"
            )
        empty = _empty(portal_copy.MATCHES_EMPTY["heading"], portal_copy.MATCHES_EMPTY["body"], portal_copy.MATCHES_EMPTY["button"], "/creator/profile")
        return _hero(portal_copy.MATCHES["heading"], portal_copy.MATCHES["subhead"]) + ("".join(blocks) or empty)
    items = "".join(
        "<li>"
        f"{html.escape(str(row.get('link_url') or ''))}"
        f"{_tracking_block(row.get('discount_code'), row.get('tracking_path'), base_url, row.get('platform_links'), row.get('direct_url'))}"
        "</li>"
        for row in data.get("links") or []
    )
    if page == "billing":
        number = profile.get("founding_number")
        badge = f"<p>Foundr #{html.escape(str(number))}</p>" if number else ""
        return badge + _billing_body("creator", data.get("billing") or {}, csrf, "/creator/billing")
    empty = _empty(portal_copy.LINKS_EMPTY["heading"], portal_copy.LINKS_EMPTY["body"], portal_copy.LINKS_EMPTY["button"], "/creator/offers")
    return _hero(portal_copy.LINKS["heading"], portal_copy.LINKS["subhead"]) + f"<p>{html.escape(portal_copy.LINKS['disclose'])}</p>" + (f"<ul class='plain'>{items}</ul>" if items else empty)


def _billing_body(role: str, billing: dict, csrf: str, action: str) -> str:
    notice = "" if billing.get("configured") else "<p>Stripe is not configured.</p>"
    tier = billing.get("plan_tier") or ("starter" if role == "seller" else "member")
    label = billing_config.tier_label(role, str(tier))
    plans = []
    for plan in billing.get("plans") or []:
        soon = ", ".join(plan.get("coming_soon") or [])
        soon_text = f" Coming soon: {html.escape(soon)}." if soon else ""
        plans.append(
            f"<li><strong>{html.escape(plan['label'])}</strong> "
            f"{html.escape(plan['monthly'])} / month or {html.escape(plan['annual'])} / year, ex GST.{soon_text}</li>"
        )
    checkout = ""
    subscribed = str(billing.get("subscription_status") or "").strip().lower() in {"active", "trialing", "past_due"}
    manage = f"""
        <form method="post" action="{action}/portal">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <button type="submit">Manage or cancel</button>
        </form>
        """
    if billing.get("configured") and subscribed:
        checkout = (
            "<p>You already have a subscription. Use Manage to change plan, update payment, or cancel.</p>"
            + manage
        )
    elif billing.get("configured"):
        import legal

        found = billing.get("disclosures") or {}
        detail = "".join(
            f"<li><strong>{html.escape(row['label'])} ({'annual' if row['interval'] == 'year' else 'monthly'})</strong>: {html.escape(row['text'])}</li>"
            for row in found.get("plans") or []
        )
        main_text = found.get("main") or ""
        disclosure_html = (
            (f"<p class='disclosure'>{html.escape(main_text)}</p>" if main_text else "")
            + f"<details><summary>What you will pay on each plan</summary><ul>{detail}</ul></details>"
            + legal.checkbox(
                "authorise_recurring",
                legal.CHECKOUT_AUTHORISE.format(terms=found.get("terms") or "Terms"),
                required=True,
            )
        )
        options = []
        for plan in billing.get("plans") or []:
            if str(plan.get("monthly") or "").startswith("Included"):
                continue
            selected = " selected" if plan.get("tier") == tier else ""
            options.append(
                f"<option value=\"{html.escape(str(plan.get('tier') or ''))}\"{selected}>"
                f"{html.escape(str(plan.get('label') or ''))}</option>"
            )
        checkout = f"""
        <form method="post" action="{action}/checkout">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Plan <select name="tier">{''.join(options)}</select></label>
          <label>Interval
            <select name="interval"><option value="month">month</option><option value="year">year</option></select>
          </label>
          {disclosure_html}
          <button type="submit">Subscribe</button>
        </form>
        {manage}
        """
    prompt = ""
    if billing.get("commerce_open") is False:
        prompt = f"<p class='banner'>{html.escape(stripe_billing.SUBSCRIBE_PROMPT)}</p>"
    shown_status = billing.get("plan_status") or "unsubscribed"
    if role == "seller":
        plan = portal_copy.SELLER_PLAN
        billing_copy = portal_copy.SELLER_BILLING
        pilot = ""
        price = portal_copy.POST_PILOT_GROWTH_PRICE
        if "pilot" in str(label).lower() or shown_status:
            pilot = f"<p>{html.escape(plan['pilot_now'])} {html.escape(plan['pilot_after'].format(price=price))} {html.escape(plan['renewal'])}</p>"
        invoices = _empty(portal_copy.BILLING_EMPTY["heading"], portal_copy.BILLING_EMPTY["body"])
        heading, sub = plan["heading"], plan["subhead"]
        extra = f"<p class='help'>{html.escape(plan['features'])}</p>{pilot}<h2>{html.escape(billing_copy['heading'])}</h2><p>{html.escape(billing_copy['subhead'])}</p><p>{html.escape(billing_copy['pilot'].format(price=price, renewal=portal_copy.PILOT_RENEWAL))}</p>{invoices}"
    else:
        plan = portal_copy.CREATR_PLAN
        heading, sub = plan["heading"], plan["subhead"]
        extra = (
            f"<p class='help'>{html.escape(plan['features'])} {html.escape(plan['limits'])}</p>"
            f"<h2>{html.escape(portal_copy.CREATR_BILLING['heading'])}</h2>"
            f"<p>{html.escape(portal_copy.CREATR_BILLING['subhead'])}</p>"
            f"<p class='help'>{html.escape(plan['pilot'])}</p>"
            + _empty(portal_copy.CREATR_BILLING_EMPTY["heading"], portal_copy.CREATR_BILLING_EMPTY["body"])
        )
    return f"""
    {_hero(heading, sub)}
    {prompt}
    {notice}
    <p>Current plan: {html.escape(str(label))}. Status: {html.escape(str(shown_status))}.</p>
    <ul class="plan-list">{''.join(plans)}</ul>
    {extra}
    {checkout}
    """


def _abn_note(item: dict) -> str:
    if item.get("creator_has_abn") is not False:
        return ""
    import commission

    return f'<p class="note">{html.escape(commission.ABN_WARNING)}</p>'


def _tracking_block(
    code: Optional[str],
    path: Optional[str],
    base_url: str,
    platform_links: Optional[list] = None,
    direct_url: Optional[str] = None,
) -> str:
    if not code or not path:
        return "<p class='tracking'>No tracking code yet.</p>"
    full = f"{base_url.rstrip('/')}{path}"
    safe_code = html.escape(str(code))
    safe_url = html.escape(full)
    extra = ""
    if direct_url:
        safe_direct = html.escape(str(direct_url))
        extra += (
            f"<p>Direct <a href='{safe_direct}'>{safe_direct}</a> "
            f"<button type='button' class='copy' data-copy='{safe_direct}'>Copy link</button></p>"
        )
    for variant in platform_links or []:
        label = html.escape(str(variant.get("label") or variant.get("source_platform") or ""))
        variant_url = html.escape(f"{base_url.rstrip('/')}{variant.get('tracking_path') or ''}")
        extra += (
            f"<p>{label} <a href='{variant_url}'>{variant_url}</a> "
            f"<button type='button' class='copy' data-copy='{variant_url}'>Copy link</button></p>"
        )
        if variant.get("direct_url"):
            safe_variant = html.escape(str(variant["direct_url"]))
            extra += (
                f"<p>{label} direct <a href='{safe_variant}'>{safe_variant}</a> "
                f"<button type='button' class='copy' data-copy='{safe_variant}'>Copy link</button></p>"
            )
    return (
        "<div class='tracking'>"
        f"<p><span class='code'>{safe_code}</span> "
        f"<button type='button' class='copy' data-copy='{safe_code}'>Copy code</button></p>"
        f"<p><a href='{safe_url}'>{safe_url}</a> "
        f"<button type='button' class='copy' data-copy='{safe_url}'>Copy link</button></p>"
        f"{extra}"
        "</div>"
    )


def _layout(title: str, body: str, logout: Optional[str], csrf: Optional[str], theme: str = "seller", aside: str = "") -> str:
    logout_form = ""
    if logout and csrf:
        logout_form = (
            f'<form method="post" action="{html.escape(logout)}">'
            f'<input type="hidden" name="csrf_token" value="{html.escape(csrf)}">'
            "<button class='secondary' type='submit'>Sign out</button></form>"
        )
    mark = "#0A64E0" if theme == "seller" else "#C2127F"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="/assets/plantr.css">
</head><body class="app app-{html.escape(theme)}">
{('<div class="shell seller-shell">' + aside + '<main class="seller-main">' + body + '</main></div>') if aside else ('<header class="portal-bar"><a class="logo" href="/"><strong>Plantr</strong> <span class="collab">Collab</span></a>' + logout_form + '</header><main class="portal">' + body + '</main>')}
<script>
document.querySelectorAll("button.copy").forEach((button) => {{
  button.addEventListener("click", async () => {{
    const value = button.getAttribute("data-copy") || "";
    try {{
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied";
    }} catch (error) {{
      button.textContent = "Copy failed";
    }}
  }});
}});
</script>
</body></html>"""


def _product_row(row) -> dict:
    price = getattr(row, "price", None)
    rate = getattr(row, "commission_rate", None)
    return {
        "id": row.id,
        "title": row.title,
        "category": getattr(row, "category", None),
        "price": float(price) if price is not None else None,
        "url": catalogue.product_public_url(row),
        "image_url": getattr(row, "image_url", None) or "",
        "why_chosen": getattr(row, "why_chosen", None) or "",
        "value_to_creators": getattr(row, "value_to_creators", None) or "",
        "description": getattr(row, "description_short", None) or "",
        "commission_rate": float(rate) if rate is not None else None,
        "source": getattr(row, "source", None),
        "ready": catalogue.product_is_complete(row),
        "needs_completion": catalogue.needs_completion_prompt(row),
    }


def _owned_product(shop_id: str, product_id: str):
    if not product_id:
        return None
    if db.configured():
        with db.session_scope() as session:
            product = session.get(Product, product_id)
            if product is None or product.shop_id != shop_id:
                return None
            return SimpleNamespace(
                shop_id=product.shop_id,
                why_chosen=product.why_chosen,
                value_to_creators=product.value_to_creators,
                commission_rate=product.commission_rate,
            )
    product = db.memory_products.get(product_id)
    if product is None or product.shop_id != shop_id:
        return None
    return product


def _accepted_items(*, shop_id: Optional[str] = None, creator_id: Optional[str] = None) -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            query = (
                select(ShortlistItem, Shortlist, Product, Shop, Creator)
                .join(Shortlist, Shortlist.id == ShortlistItem.shortlist_id)
                .join(Product, Product.id == Shortlist.product_id, isouter=True)
                .join(Shop, Shop.id == Shortlist.shop_id, isouter=True)
                .join(Creator, Creator.id == ShortlistItem.creator_id, isouter=True)
                .where(ShortlistItem.accepted_at.is_not(None))
            )
            if shop_id:
                query = query.where(Shortlist.shop_id == shop_id)
            if creator_id:
                query = query.where(ShortlistItem.creator_id == creator_id)
            rows = []
            for item, shortlist, product, shop, creator in session.execute(query).all():
                rows.append(
                    {
                        "id": item.id,
                        "creator_id": item.creator_id,
                        "handle": creator.handle if creator is not None else None,
                        "product_id": shortlist.product_id,
                        "product_title": product.title if product is not None else None,
                        "shop_id": shortlist.shop_id,
                        "shop_name": shop.name if shop is not None else None,
                        "accepted_at": item.accepted_at,
                        "commission_rate": float(product.commission_rate) if product is not None and product.commission_rate is not None else None,
                        "creator_has_abn": bool(getattr(creator, "abn", None)) if creator is not None else None,
                    }
                )
            return rows
    rows = []
    for item in db.memory_shortlist_items:
        if not getattr(item, "accepted_at", None):
            continue
        shortlist = next((row for row in db.memory_shortlists if row.id == item.shortlist_id), None)
        if shortlist is None:
            continue
        if shop_id and shortlist.shop_id != shop_id:
            continue
        if creator_id and item.creator_id != creator_id:
            continue
        product = db.memory_products.get(shortlist.product_id)
        shop = db.memory_shops.get(shortlist.shop_id)
        creator = db.memory_creators.get(item.creator_id) or {}
        rows.append(
            {
                "id": item.id,
                "creator_id": item.creator_id,
                "handle": creator.get("handle"),
                "product_id": shortlist.product_id,
                "product_title": getattr(product, "title", None),
                "shop_id": shortlist.shop_id,
                "shop_name": getattr(shop, "name", None),
                "accepted_at": item.accepted_at,
                "commission_rate": float(product.commission_rate) if product is not None and getattr(product, "commission_rate", None) is not None else None,
            }
        )
    return rows


def _offer_items(creator_id: str, *, pending: bool) -> list[dict]:
    if not pending:
        return []
    if db.configured():
        with db.session_scope() as session:
            query = (
                select(ShortlistItem, Shortlist, Product, Shop, Creator)
                .join(Shortlist, Shortlist.id == ShortlistItem.shortlist_id)
                .join(Product, Product.id == Shortlist.product_id, isouter=True)
                .join(Shop, Shop.id == Shortlist.shop_id, isouter=True)
                .join(Creator, Creator.id == ShortlistItem.creator_id, isouter=True)
                .where(ShortlistItem.creator_id == creator_id)
                .where(ShortlistItem.accepted_at.is_(None))
                .where(ShortlistItem.declined_at.is_(None))
            )
            rows = []
            for item, shortlist, product, shop, creator in session.execute(query).all():
                rate = product.commission_rate if product is not None else None
                rows.append(
                    {
                        "id": item.id,
                        "creator_id": item.creator_id,
                        "handle": creator.handle if creator is not None else None,
                        "product_id": shortlist.product_id,
                        "product_title": product.title if product is not None else None,
                        "shop_id": shortlist.shop_id,
                        "shop_name": shop.name if shop is not None else None,
                        "accepted_at": None,
                        "commission_rate": float(rate) if rate is not None else None,
                    }
                )
            return rows
    rows = []
    for item in db.memory_shortlist_items:
        if getattr(item, "accepted_at", None) or getattr(item, "declined_at", None):
            continue
        if item.creator_id != creator_id:
            continue
        shortlist = next((row for row in db.memory_shortlists if row.id == item.shortlist_id), None)
        if shortlist is None:
            continue
        product = db.memory_products.get(shortlist.product_id)
        shop = db.memory_shops.get(shortlist.shop_id)
        rate = getattr(product, "commission_rate", None) if product is not None else None
        rows.append(
            {
                "id": item.id,
                "creator_id": item.creator_id,
                "product_id": shortlist.product_id,
                "product_title": getattr(product, "title", None),
                "shop_id": shortlist.shop_id,
                "shop_name": getattr(shop, "name", None),
                "accepted_at": None,
                "commission_rate": float(rate) if rate is not None else None,
            }
        )
    return rows


def _item_belongs_to_shop(item_id: str, shop_id: str) -> bool:
    if db.configured():
        with db.session_scope() as session:
            item = session.get(ShortlistItem, item_id)
            if item is None:
                return False
            shortlist = session.get(Shortlist, item.shortlist_id)
            return shortlist is not None and shortlist.shop_id == shop_id
    item = next((row for row in db.memory_shortlist_items if row.id == item_id), None)
    if item is None:
        return False
    shortlist = next((row for row in db.memory_shortlists if row.id == item.shortlist_id), None)
    return shortlist is not None and shortlist.shop_id == shop_id


def _link_for(creator_id: Optional[str], product_id: Optional[str]) -> Optional[dict]:
    if not creator_id or not product_id:
        return None
    for row in attribution.list_links(shop_id=None, creator_id=creator_id):
        if row["product_id"] == product_id:
            return row
    return None


def _brief_matches(brief, item: dict) -> bool:
    item_id = item.get("id")
    product_id = item.get("product_id")
    creator_id = item.get("creator_id")
    brief_item = getattr(brief, "shortlist_item_id", None)
    brief_product = getattr(brief, "product_id", None)
    brief_creator = getattr(brief, "creator_id", None)
    if item_id and product_id and brief_product == product_id and brief_item == item_id:
        return True
    if item_id and brief_item == item_id:
        return True
    return bool(creator_id and product_id and brief_product == product_id and brief_creator == creator_id)


def _brief_for(item: dict) -> Optional[dict]:
    if db.configured():
        with db.session_scope() as session:
            clauses = []
            item_id = item.get("id")
            product_id = item.get("product_id")
            creator_id = item.get("creator_id")
            if item_id and product_id:
                clauses.append((Brief.product_id == product_id) & (Brief.shortlist_item_id == item_id))
            if item_id:
                clauses.append(Brief.shortlist_item_id == item_id)
            if creator_id and product_id:
                clauses.append((Brief.product_id == product_id) & (Brief.creator_id == creator_id))
            if not clauses:
                return None
            brief = session.scalars(select(Brief).where(or_(*clauses)).order_by(Brief.created_at.desc())).first()
            if brief is None:
                return None
            return {"id": brief.id, "content": brief.content}
    for brief in reversed(db.memory_briefs):
        if _brief_matches(brief, item):
            return {"id": brief.id, "content": brief.content}
    return None


def _shop_exists(shop_id: Optional[str]) -> bool:
    if not shop_id:
        return False
    if db.configured():
        with db.session_scope() as session:
            return session.get(Shop, shop_id) is not None
    return shop_id in db.memory_shops


def _creator_exists(creator_id: Optional[str]) -> bool:
    if not creator_id:
        return False
    if db.configured():
        with db.session_scope() as session:
            return session.get(Creator, creator_id) is not None
    return creator_id in db.memory_creators


def _find_email(email: str):
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(PortalAccount).where(PortalAccount.email == email))
            if row is None:
                return None
            return SimpleNamespace(
                id=row.id,
                role=row.role,
                email=row.email,
                password_hash=row.password_hash,
                shop_id=row.shop_id,
                creator_id=row.creator_id,
            )
    return next((row for row in db.memory_portal_accounts if row.email == email), None)


def _subject(account) -> Optional[str]:
    return account.shop_id if account.role == "seller" else account.creator_id


def _save_existing(account) -> None:
    if db.configured():
        with db.session_scope() as session:
            row = session.get(PortalAccount, account.id)
            if row is not None:
                row.password_hash = account.password_hash
        return
    for row in db.memory_portal_accounts:
        if row.id == account.id:
            row.password_hash = account.password_hash


def _insert(role: str, email: str, password_hash: str, shop_id: Optional[str], creator_id: Optional[str]):
    now = utcnow()
    if db.configured():
        try:
            with db.session_scope() as session:
                row = PortalAccount(
                    id=str(uuid.uuid4()),
                    role=role,
                    email=email,
                    password_hash=password_hash,
                    shop_id=shop_id if role == "seller" else None,
                    creator_id=creator_id if role == "creator" else None,
                    created_at=now,
                )
                session.add(row)
                session.flush()
                return row
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="That email is already used") from exc
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        role=role,
        email=email,
        password_hash=password_hash,
        shop_id=shop_id if role == "seller" else None,
        creator_id=creator_id if role == "creator" else None,
        created_at=now,
        last_login_at=None,
    )
    db.memory_portal_accounts.append(row)
    return row


def _touch_login(account) -> None:
    now = utcnow()
    if db.configured():
        with db.session_scope() as session:
            row = session.get(PortalAccount, account.id)
            if row is not None:
                row.last_login_at = now
        return
    account.last_login_at = now


def _login_allowed(ip: str, role: str) -> bool:
    now = __import__("time").monotonic()
    key = f"{role}:{ip}"
    with _login_lock:
        bucket = _login_buckets[key]
        while bucket and now - bucket[0] >= auth.LOGIN_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= auth.LOGIN_LIMIT:
            return False
        bucket.append(now)
        return True
