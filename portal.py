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
import db
import events
import scoring
import services
from events import PlanLimitReached
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


def require_seller(request: Request) -> dict:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    session = seller_context(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Seller session required")
    return session


def require_creator(request: Request) -> dict:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    session = creator_context(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Creator session required")
    return session


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


def save_product(shop_id: str, *, product_id: str, title: str, category: str, price: str, url: str, description: str, commission: str = "") -> str:
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    category = (category or "").strip().lower()
    if category:
        services.validate_category_lists([category])
    amount = None
    if str(price or "").strip():
        try:
            amount = float(price)
        except ValueError:
            raise HTTPException(status_code=422, detail="price must be a number") from None
    destination = (url or "").strip()
    if destination and attribution.safe_destination(destination) is None:
        raise HTTPException(status_code=422, detail="URL must be an http(s) address")
    rate = None
    if str(commission or "").strip():
        try:
            rate = float(commission)
        except ValueError:
            raise HTTPException(status_code=422, detail="commission must be a number") from None
    if db.configured():
        with db.session_scope() as session:
            product = session.get(Product, product_id) if product_id else None
            if product is None:
                shop = session.get(Shop, shop_id)
                if shop is not None:
                    try:
                        events.guard_plan_limit(shop, "products", session)
                    except PlanLimitReached as exc:
                        raise HTTPException(status_code=402, detail=str(exc)) from exc
                product = Product(
                    id=str(uuid.uuid4()),
                    shop_id=shop_id,
                    title=title,
                    description_short=(description or "").strip() or None,
                    description_long=None,
                    category=category or None,
                    images=[],
                    price=amount,
                    currency="AUD",
                    tags=[],
                    attributes={"url": destination} if destination else {},
                    claims=[],
                    source="manual",
                    created_at=utcnow(),
                )
                session.add(product)
            else:
                if product.shop_id != shop_id:
                    raise HTTPException(status_code=404, detail="Product not found")
                product.title = title
                product.category = category or None
                product.price = amount
                product.description_short = (description or "").strip() or None
                attributes = dict(product.attributes or {})
                if destination:
                    attributes["url"] = destination
                elif "url" in attributes:
                    attributes.pop("url")
                product.attributes = attributes
                product.source = product.source or "manual"
            if rate is not None:
                product.commission_rate = rate
            session.flush()
            return product.id
    if product_id and product_id in db.memory_products:
        product = db.memory_products[product_id]
        if product.shop_id != shop_id:
            raise HTTPException(status_code=404, detail="Product not found")
    else:
        shop = db.memory_shops.get(shop_id)
        if shop is not None:
            try:
                events.guard_plan_limit(shop, "products", None)
            except PlanLimitReached as exc:
                raise HTTPException(status_code=402, detail=str(exc)) from exc
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
            source="manual",
            created_at=utcnow(),
        )
        db.memory_products[product.id] = product
    product.title = title
    product.category = category or None
    product.price = amount
    product.description_short = (description or "").strip() or None
    attributes = dict(getattr(product, "attributes", None) or {})
    if destination:
        attributes["url"] = destination
    product.attributes = attributes
    product.source = getattr(product, "source", None) or "manual"
    if rate is not None:
        product.commission_rate = rate
    return product.id


def run_match(shop_id: str, product_id: str) -> dict:
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
        offers.append(item)
    return offers


def creator_links(creator_id: str) -> list[dict]:
    return attribution.list_links(shop_id=None, creator_id=creator_id)


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
        return HTMLResponse(_layout("Seller", body, None, None))
    shop = shop_profile(session["subject_id"])
    csrf = html.escape(session["csrf"])
    banner = ""
    if session.get("impersonator"):
        banner = "<p class='banner'>Viewing as this shop.</p>"
    nav = """
    <nav>
      <a href="/seller/profile">Profile</a>
      <a href="/seller/products">Products</a>
      <a href="/seller/find">Find creators</a>
      <a href="/seller/accepted">Accepted</a>
      <a href="/seller/campaigns">Campaigns</a>
      <a href="/seller/billing">Billing</a>
    </nav>
    """
    content = banner + nav + _seller_body(page, shop, csrf, base_url=base_url, **data)
    return HTMLResponse(_layout(shop["name"], content, "/seller/logout", session["csrf"]))


def render_creator(session: Optional[dict], page: str, *, base_url: str = "", **data) -> HTMLResponse:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    if session is None:
        token = auth.issue_form_token("creator-login")
        body = f"""
        <h1>Creator sign in</h1>
        <form method="post" action="/creator/login">
          <input type="hidden" name="csrf_token" value="{html.escape(token)}">
          <label for="email">Email</label>
          <input id="email" name="email" type="email" autocomplete="username" required>
          <label for="password">Password</label>
          <input id="password" name="password" type="password" autocomplete="current-password" required>
          <button type="submit">Sign in</button>
        </form>
        """
        return HTMLResponse(_layout("Creator", body, None, None))
    profile = creator_profile(session["subject_id"])
    csrf = html.escape(session["csrf"])
    banner = "<p class='banner'>Viewing as this creator.</p>" if session.get("impersonator") else ""
    nav = """
    <nav>
      <a href="/creator/profile">Profile</a>
      <a href="/creator/offers">Offers</a>
      <a href="/creator/links">Links</a>
      <a href="/creator/billing">Subscription</a>
    </nav>
    """
    content = banner + nav + _creator_body(page, profile, csrf, base_url=base_url, **data)
    return HTMLResponse(_layout(profile.get("handle") or "Creator", content, "/creator/logout", session["csrf"]))


def _seller_body(page: str, shop: dict, csrf: str, *, base_url: str = "", **data) -> str:
    if page == "profile":
        return f"<h1>{html.escape(shop['name'])}</h1><pre>{html.escape(json.dumps({k: shop[k] for k in ('id','name','country','market','currency','primary_category','plan_tier','is_pilot','is_underserved')}, default=str, indent=2))}</pre>"
    if page == "products":
        rows = "".join(
            f"<li>{html.escape(row['title'])} · {html.escape(row.get('category') or '')} · {row.get('price') if row.get('price') is not None else ''} · {html.escape(row.get('url') or '')}</li>"
            for row in data.get("products") or []
        )
        edit = data.get("edit") or {}
        return f"""
        <h1>Products</h1>
        <ul>{rows or '<li>No products yet.</li>'}</ul>
        <h2>{'Edit' if edit else 'Add'} product</h2>
        <form method="post" action="/seller/products">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="product_id" value="{html.escape(edit.get('id') or '')}">
          <label>Title <input name="title" value="{html.escape(edit.get('title') or '')}" required></label>
          <label>Category <input name="category" value="{html.escape(edit.get('category') or '')}"></label>
          <label>Price <input name="price" value="{html.escape(str(edit.get('price') or ''))}"></label>
          <label>URL <input name="url" type="url" value="{html.escape(edit.get('url') or '')}"></label>
          <label>Commission percent <input name="commission" value="{html.escape(str(edit.get('commission_rate') or ''))}"></label>
          <label>Description <textarea name="description">{html.escape(edit.get('description') or '')}</textarea></label>
          <button type="submit">Save product</button>
        </form>
        """
    if page == "find":
        options = "".join(
            f"<option value=\"{html.escape(row['id'])}\">{html.escape(row['title'])}</option>"
            for row in data.get("products") or []
        )
        cards = []
        for match in data.get("matches") or []:
            creator = match.get("creator") or {}
            handle = html.escape(str(creator.get("handle") or creator.get("influencer_id") or ""))
            reasons = "".join(f"<li>{html.escape(str(reason))}</li>" for reason in match.get("reasons") or [])
            estimates = html.escape(json.dumps(match.get("estimates") or {}, default=str))
            item_id = html.escape(str(match.get("shortlist_item_id") or ""))
            actions = ""
            if item_id:
                actions = f"""
                <form method="post" action="/seller/shortlist-items/{item_id}/accept"><input type="hidden" name="csrf_token" value="{csrf}"><button type="submit">Accept</button></form>
                <form method="post" action="/seller/shortlist-items/{item_id}/decline"><input type="hidden" name="csrf_token" value="{csrf}"><button type="submit">Decline</button></form>
                """
            cards.append(f"<article><h3>{handle}</h3><p>Score {html.escape(str(match.get('score')))}</p><ul>{reasons}</ul><pre>{estimates}</pre>{actions}</article>")
        return f"""
        <h1>Find creators</h1>
        <form method="post" action="/seller/find">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Product <select name="product_id">{options}</select></label>
          <button type="submit">Run match</button>
        </form>
        {''.join(cards) or '<p>Run a match to see ranked creators.</p>'}
        """
    rows = []
    for item in data.get("accepted") or []:
        rows.append(
            "<li>"
            f"{html.escape(str(item.get('handle') or item.get('creator_id')))} · "
            f"{html.escape(str(item.get('product_title') or ''))}"
            f"{_tracking_block(item.get('discount_code'), item.get('tracking_path'), base_url)}"
            "</li>"
        )
    if page == "campaigns":
        rows = "".join(f"<li>{html.escape(row['name'])}</li>" for row in data.get("campaigns") or [])
        return f"""
        <h1>Campaigns</h1>
        <ul>{rows or '<li>No campaigns yet.</li>'}</ul>
        <form method="post" action="/seller/campaigns">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Name <input name="name" required></label>
          <button type="submit">Create campaign</button>
        </form>
        """
    if page == "billing":
        return _billing_body("seller", data.get("billing") or {}, csrf, "/seller/billing")
    return f"<h1>Accepted creators</h1><ul>{''.join(rows) or '<li>None yet.</li>'}</ul>"


def _creator_body(page: str, profile: dict, csrf: str, *, base_url: str = "", **data) -> str:
    if page == "profile":
        active = ", ".join(profile.get("active_categories") or [])
        desired = ", ".join(profile.get("desired_categories") or [])
        number = profile.get("founding_number")
        badge = f"<p>Founding Curator #{html.escape(str(number))}</p>" if number else ""
        return badge + f"""
        <h1>{html.escape(profile.get('handle') or '')}</h1>
        <form method="post" action="/creator/profile">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Active categories <input name="active_categories" value="{html.escape(active)}"></label>
          <label>Desired categories <input name="desired_categories" value="{html.escape(desired)}"></label>
          <button type="submit">Save profile</button>
        </form>
        """
    if page == "offers":
        blocks = []
        for offer in data.get("offers") or []:
            points = "".join(f"<li>{html.escape(str(point))}</li>" for point in offer.get("talking_points") or [])
            rate = offer.get("commission_rate")
            rate_text = (
                f"{rate}% of tracked sales, set and paid by the supplier."
                if rate is not None
                else "The supplier has not set a commission rate yet."
            )
            accept = ""
            if not offer.get("accepted_at"):
                accept = (
                    f"<form method='post' action='/creator/offers/{html.escape(str(offer.get('id')))}/accept'>"
                    f"<input type='hidden' name='csrf_token' value='{csrf}'>"
                    "<button type='submit'>Accept offer</button></form>"
                )
            blocks.append(
                f"<article><h2>{html.escape(str(offer.get('product_title') or 'Product'))}</h2>"
                f"<p>{html.escape(str(offer.get('shop_name') or ''))}</p>"
                f"<p>Commission: {html.escape(rate_text)} Plantr does not pay this.</p>"
                f"<ul>{points or '<li>No talking points yet.</li>'}</ul>"
                f"{accept}"
                f"{_tracking_block(offer.get('discount_code'), offer.get('tracking_path'), base_url)}</article>"
            )
        return f"<h1>Offers</h1>{''.join(blocks) or '<p>No accepted offers yet.</p>'}"
    items = "".join(
        "<li>"
        f"{html.escape(str(row.get('link_url') or ''))}"
        f"{_tracking_block(row.get('discount_code'), row.get('tracking_path'), base_url)}"
        "</li>"
        for row in data.get("links") or []
    )
    if page == "billing":
        number = profile.get("founding_number")
        badge = f"<p>Founding Curator #{html.escape(str(number))}</p>" if number else ""
        return badge + _billing_body("creator", data.get("billing") or {}, csrf, "/creator/billing")
    return f"<h1>Links and codes</h1><ul>{items or '<li>No links yet.</li>'}</ul>"


def _billing_body(role: str, billing: dict, csrf: str, action: str) -> str:
    notice = "" if billing.get("configured") else "<p>Stripe is not configured.</p>"
    tier = billing.get("plan_tier") or ("starter" if role == "seller" else "member")
    label = "Invited" if tier == "member" else tier
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
        checkout = f"""
        <form method="post" action="{action}/checkout">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Plan <input name="tier" value="{html.escape(tier)}"></label>
          <label>Interval
            <select name="interval"><option value="month">month</option><option value="year">year</option></select>
          </label>
          <label>Promotion code <input name="promo_code"></label>
          <button type="submit">Continue to checkout</button>
        </form>
        {manage}
        """
    return f"""
    <h1>Subscription</h1>
    {notice}
    <p>Current plan: {html.escape(str(label))}. Status: {html.escape(str(billing.get('plan_status') or 'not started'))}.</p>
    <p>Prices are ex GST. Stripe Tax is not enabled.</p>
    <ul>{''.join(plans)}</ul>
    {checkout}
    """


def _tracking_block(code: Optional[str], path: Optional[str], base_url: str) -> str:
    if not code or not path:
        return "<p class='tracking'>No tracking code yet.</p>"
    full = f"{base_url.rstrip('/')}{path}"
    safe_code = html.escape(str(code))
    safe_url = html.escape(full)
    return (
        "<div class='tracking'>"
        f"<p><span class='code'>{safe_code}</span> "
        f"<button type='button' class='copy' data-copy='{safe_code}'>Copy code</button></p>"
        f"<p><a href='{safe_url}'>{safe_url}</a> "
        f"<button type='button' class='copy' data-copy='{safe_url}'>Copy link</button></p>"
        "</div>"
    )


def _layout(title: str, body: str, logout: Optional[str], csrf: Optional[str]) -> str:
    logout_form = ""
    if logout and csrf:
        logout_form = (
            f"<form method='post' action='{html.escape(logout)}'>"
            f"<input type='hidden' name='csrf_token' value='{html.escape(csrf)}'>"
            "<button type='submit'>Sign out</button></form>"
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; background: #f3f6f4; color: #17211c; }}
header, main {{ max-width: 880px; margin: 0 auto; padding: 16px; }}
header {{ display: flex; justify-content: space-between; align-items: center; }}
nav a {{ margin-right: 12px; }}
form, article {{ background: white; padding: 16px; border-radius: 12px; margin: 12px 0; }}
label {{ display: block; margin: 8px 0; }}
input, textarea, select {{ font: inherit; padding: 8px; width: 100%; box-sizing: border-box; }}
button {{ background: #1f6b4a; color: white; border: 0; border-radius: 8px; padding: 8px 12px; }}
button.copy {{ background: #e7eeea; color: #16352b; margin-left: 8px; }}
.tracking {{ margin-top: 8px; }}
.banner {{ background: #fff4d6; padding: 8px 12px; border-radius: 8px; }}
</style></head><body>
<header><strong>Plantr Collab</strong>{logout_form}</header>
<main>{body}</main>
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
    attributes = getattr(row, "attributes", None) or {}
    price = getattr(row, "price", None)
    return {
        "id": row.id,
        "title": row.title,
        "category": getattr(row, "category", None),
        "price": float(price) if price is not None else None,
        "url": attributes.get("url") if isinstance(attributes, dict) else None,
        "description": getattr(row, "description_short", None) or "",
        "source": getattr(row, "source", None),
    }


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
