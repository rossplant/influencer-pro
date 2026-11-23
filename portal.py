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
import brand_ui
import recognition
import supplier_copy
import db
import events
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
    promo_rate: str = "",
    promo_days: str = "",
    fact_what: str = "",
    fact_who: str = "",
    selling_fast: bool = False,
    samples_enabled: bool = False,
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
        if (fact_what or "").strip() or (fact_who or "").strip():
            attributes["key_facts"] = {"what": (fact_what or "").strip(), "who": (fact_who or "").strip()}
        attributes["selling_fast"] = bool(selling_fast)
        attributes["samples_enabled"] = bool(samples_enabled)
        if (promo_rate or "").strip():
            from datetime import date as _date

            try:
                days = int(promo_days or "0")
                ends = recognition.promo_end(_date.today(), days)
                attributes["promo_rate"] = float(promo_rate)
                attributes["promo_ends"] = ends.isoformat()
                attributes["promo_line"] = recognition.promo_line(float(promo_rate), ends)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
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
                "Add why you chose it, the value for creators, and a commission."
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


def update_creator_categories(creator_id: str, active: list[str], desired: list[str], *, leaderboard: bool = False) -> None:
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
            profile["leaderboard_opt_in"] = bool(leaderboard)
            creator.profile = profile
        return
    record = db.memory_creators.get(creator_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    record["active_categories"] = active
    record["desired_categories"] = desired
    record["leaderboard_opt_in"] = bool(leaderboard)
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
        item["listing_tips"] = _approved_listing_tips(item.get("product_id"))
        if not recognition.supplier_visible_to_creators({"shopify_domain": item.get("shopify_domain")}):
            continue
        offers.append(item)
    return offers


def creator_links(creator_id: str) -> list[dict]:
    return attribution.list_links(shop_id=None, creator_id=creator_id)


def login_message(detail: str) -> str:
    text = (detail or "").lower()
    if "too many" in text:
        return "Too many sign-in attempts. Try again in 15 minutes."
    return "That email or password is not right. Try again."


def login_page(role: str, error: str = "", *, status: int = 200) -> HTMLResponse:
    purpose = "seller-login" if role == "seller" else "creator-login"
    action = "/seller/login" if role == "seller" else "/creator/login"
    title = "Supplier sign in" if role == "seller" else "Creatr sign in"
    token = auth.issue_form_token(purpose)
    alert = f"<p class='error' role='alert'>{html.escape(error)}</p>" if error else ""
    body = f"""
    <h1>{title}</h1>
    {alert}
    <form method="post" action="{action}">
      <input type="hidden" name="csrf_token" value="{html.escape(token)}">
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required>
      <label for="password">Password</label>
      <div class="password-row">
        <input id="password" name="password" type="password" autocomplete="current-password" autocapitalize="none" autocorrect="off" spellcheck="false" required>
        <button class="secondary" type="button" data-show-password="password">Show password</button>
      </div>
      <button type="submit">Sign in</button>
    </form>
    """
    return HTMLResponse(_layout(title, body, None, None), status_code=status)


def render_seller(session: Optional[dict], page: str, *, base_url: str = "", **data) -> HTMLResponse:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    if session is None:
        return login_page("seller", str(data.get("error") or ""))
    shop = shop_profile(session["subject_id"])
    csrf = html.escape(session["csrf"])
    data["locked"] = not stripe_billing.commerce_open("seller", session["subject_id"])
    banner = ""
    if session.get("impersonator"):
        banner = "<p class='banner'>Viewing as this shop.</p>"
    if data["locked"] and page != "profile":
        banner += f"<p class='banner'>{html.escape(stripe_billing.SUBSCRIBE_PROMPT)}</p>"
    nav = """
    <nav>
      <a href="/seller">Home</a>
      <a href="/seller/products">Catalogue</a>
      <a href="/dashboard/catalogue/listing-studio">Listing Studio</a>
      <a href="/seller/find">Collabs</a>
      <a href="/seller/samples">Samples</a>
      <a href="/seller/profile">Settings</a>
    </nav>
    <p class="pills"><a href="/seller/badges">Pays on time</a> <a href="/seller/badges">Top seller</a></p>
    """
    content = banner + _seller_body(page, shop, csrf, base_url=base_url, **data)
    return HTMLResponse(_layout(shop["name"], content, "/seller/logout", session["csrf"], nav=nav))


def render_creator(session: Optional[dict], page: str, *, base_url: str = "", **data) -> HTMLResponse:
    if not portal_enabled():
        raise HTTPException(status_code=503, detail="Portal is disabled")
    if session is None:
        return login_page("creator", str(data.get("error") or ""))
    profile = creator_profile(session["subject_id"])
    csrf = html.escape(session["csrf"])
    data["locked"] = not stripe_billing.commerce_open("creator", session["subject_id"])
    banner = "<p class='banner'>Viewing as this creator.</p>" if session.get("impersonator") else ""
    if data["locked"] and page not in {"profile", "links"}:
        banner += f"<p class='banner'>{html.escape(stripe_billing.SUBSCRIBE_PROMPT)}</p>"
    nav = """
    <nav>
      <a href="/creator">Home</a>
      <a href="/creator/offers">Matches</a>
      <a href="/creator/links">Sales</a>
      <a href="/creator/profile">Settings</a>
    </nav>
    """
    content = banner + _creator_body(page, profile, csrf, base_url=base_url, **data)
    return HTMLResponse(_layout(profile.get("handle") or "Creatr", content, "/creator/logout", session["csrf"], nav=nav))


def _profile_form(shop: dict, csrf: str, error: str) -> str:
    current = shop.get("primary_category") or ""
    options = ["<option value=''>Choose a category</option>"]
    for slug, label in taxonomy.TOP_LEVEL_CATEGORIES:
        selected = " selected" if slug == current else ""
        options.append(f"<option value='{html.escape(slug)}'{selected}>{html.escape(label)}</option>")
    banner = f"<p class='banner'>{html.escape(error)}</p>" if error else ""
    return f"""
    <h1>{html.escape(shop['name'])}</h1>
    <p><a href="/seller/badges">Notes Creatrs may see</a> · <a href="/seller/creatr-tiers">What Creatr tiers mean</a></p>
    <p>The account name above is the name from your invite. The business name is the trading name, and you can change it here.</p>
    {banner}
    <form method="post" action="/seller/profile">
      <input type="hidden" name="csrf_token" value="{csrf}">
      <label>Business name <input name="business_name" value="{html.escape(shop.get('business_name') or '')}" autocomplete="organization"></label>
      <label>ABN (optional) <input name="abn" inputmode="numeric" value="{html.escape(shop.get('abn') or '')}" aria-describedby="abn-help"></label>
      <!-- TODO: replace this note with onboarding_help_text when that file is in the repo. -->
      <p id="abn-help"><small>We use an ABN to show the right tax warning. You can leave it blank.</small></p>
      <label>Primary category <select name="primary_category">{''.join(options)}</select></label>
      <label>Website <input name="store_url" type="url" value="{html.escape(shop.get('store_url') or '')}" placeholder="https://"></label>
      <label>Shopify domain <input name="shopify_domain" value="{html.escape(shop.get('shopify_domain') or '')}" placeholder="the-store.myshopify.com"></label>
      <button type="submit">Save profile</button>
    </form>
    """


def _listing_studio_page(shop: dict, csrf: str, data: dict) -> str:
    import listing_studio

    products = data.get("products") or []
    quote = recognition.listing_studio_price(4)
    price_line = (
        "In the pilot, Listing Studio is free per product for up to 4 scenes plus positioning."
        if quote["in_pilot"]
        else "From 20 January 2027, Listing Studio is A$4.99 per product, including 4 scenes plus positioning."
    )
    selected = str(data.get("product_id") or "")
    rows = []
    for row in products:
        state = db.memory_listing.get(row["id"]) or {}
        status = state.get("status") or recognition.listing_status(row.get("attributes"))
        rows.append(
            f"<tr><td><a href='/dashboard/catalogue/listing-studio?product={html.escape(row['id'])}'>{html.escape(row['title'])}</a></td>"
            f"<td>{html.escape(str(status))}</td></tr>"
        )
    detail = ""
    if selected:
        state = listing_studio.state_for(selected)
        sheet = state.get("sheet") or {}
        notice = state.get("message") or ("" if state.get("scenes") or state.get("angles") else listing_studio.unavailable_message())
        angles = "".join(
            f"<article><h2>{html.escape(row['name'])}</h2><p>{html.escape(row['insight'])}</p>"
            f"<p>{html.escape(row['hook'])}</p><p>{html.escape(row['caption'])}</p></article>"
            for row in state.get("angles") or []
        )
        scenes = []
        for scene in state.get("scenes") or []:
            reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in scene.get("reasons") or [])
            check = "pass" if scene.get("status") == "ready" or scene.get("approved") else scene.get("status")
            review = ""
            if scene.get("status") in {"ready", "Changes requested"}:
                review = f"""
                <form method="post" action="/dashboard/catalogue/listing-studio/review">
                  <input type="hidden" name="csrf_token" value="{csrf}">
                  <input type="hidden" name="product_id" value="{html.escape(selected)}">
                  <input type="hidden" name="scene_id" value="{html.escape(scene['id'])}">
                  <label>Decision <select name="decision"><option>approve</option><option>reject</option><option>request changes</option></select></label>
                  <label>Reason <select name="tag"><option></option><option>off brand</option><option>wrong positioning</option><option>product inaccurate</option><option>wrong setting or model</option><option>other</option></select></label>
                  <label>Note <input name="note"></label>
                  <button type="submit">Save review</button>
                </form>
                """
            download = ""
            if scene.get("approved"):
                download = f"<p><a href='/dashboard/catalogue/listing-studio/scenes/{html.escape(scene['id'])}?product={html.escape(selected)}'>Download</a></p>"
            scenes.append(
                f"<article><h2>{html.escape(str(scene.get('type')))}</h2>"
                f"<p>Product check: {html.escape(str(check))}</p><ul>{reasons}</ul>{download}{review}</article>"
            )
        detail = f"""
        <h2>Attribute sheet</h2>
        <p>{html.escape(notice)}</p>
        <form method="post" action="/dashboard/catalogue/listing-studio/sheet">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="product_id" value="{html.escape(selected)}">
          <label>Colour <input name="colour" value="{html.escape(str(sheet.get('colour') or ''))}"></label>
          <label>Piece count <input name="piece_count" value="{html.escape(str(sheet.get('piece_count') or ''))}"></label>
          <label>Label text <input name="label_text" value="{html.escape(str(sheet.get('label_text') or ''))}"></label>
          <label>Sleeve length, if this is apparel <input name="sleeve_length" value="{html.escape(str(sheet.get('sleeve_length') or ''))}"></label>
          <label>Button count <input name="button_count" value="{html.escape(str(sheet.get('button_count') or ''))}"></label>
          <button type="submit">Confirm attribute sheet</button>
        </form>
        <form method="post" action="/dashboard/catalogue/listing-studio/generate">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="product_id" value="{html.escape(selected)}">
          <button type="submit">Generate scenes</button>
        </form>
        <h2>Positioning</h2>
        {angles or '<p>No angles yet.</p>'}
        <h2>Scenes</h2>
        {''.join(scenes) or '<p>No scenes yet.</p>'}
        """
    return f"""
    <h1>Listing Studio</h1>
    <p>{html.escape(price_line)} Extra images are A$1.50 each, ex GST. No pack price. Product-inaccuracy rejections are free.</p>
    <table><tr><th>Product</th><th>Listing Studio</th></tr>{''.join(rows) or '<tr><td colspan="2">No products yet.</td></tr>'}</table>
    {detail}
    """


def _products_page(shop: dict, csrf: str, data: dict) -> str:
    products = data.get("products") or []
    count = len(products)
    noun = "product" if count == 1 else "products"
    rows = []
    for row in products:
        prompt = ""
        if row.get("needs_completion"):
            prompt = (
                f" <a href='/seller/products?edit={html.escape(row['id'])}'>{html.escape(catalogue.COMPLETE_PROMPT)}</a>"
            )
        rows.append(
            "<li>"
            f"{html.escape(row['title'])} · {html.escape(row.get('category') or '')} · "
            f"{'' if row.get('price') is None else row.get('price')} · {html.escape(row.get('url') or '')}"
            f" · {html.escape(recognition.listing_status(row.get('attributes')))}"
            f" <a href='/seller/products?edit={html.escape(row['id'])}'>Edit</a>"
            f"{prompt}"
            "</li>"
        )
    warning = catalogue.fee_warning(count)
    warning_html = f"<p class='banner' id='catalogue-fee'>{html.escape(warning)}</p>" if warning else ""
    first = ""
    if count == 0:
        first = f"<p class='curation' id='first-product'>{html.escape(catalogue.FIRST_PRODUCT_COPY)}</p>"
    edit = data.get("edit") or {}
    form = ""
    if not data.get("locked"):
        commission = edit.get("commission_rate")
        commission_value = "" if commission is None else str(commission)
        form = f"""
        <h2>{'Edit' if edit else 'Add'} product</h2>
        <form method="post" action="/seller/products">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="product_id" value="{html.escape(edit.get('id') or '')}">
          <label>Title <input name="title" value="{html.escape(edit.get('title') or '')}" required></label>
          <label>Category <input name="category" value="{html.escape(edit.get('category') or '')}"></label>
          <label>Price <input name="price" value="{html.escape('' if edit.get('price') is None else str(edit.get('price')))}"></label>
          <label>URL <input name="url" type="url" value="{html.escape(edit.get('url') or '')}"></label>
          <label>Image URL (optional) <input name="image_url" type="url" value="{html.escape(edit.get('image_url') or '')}"></label>
          <label>Why you chose it <textarea name="why_chosen" required>{html.escape(edit.get('why_chosen') or '')}</textarea></label>
          <label>Value for creators <textarea name="value_to_creators" required>{html.escape(edit.get('value_to_creators') or '')}</textarea></label>
          <label>Commission percent <input name="commission" value="{html.escape(commission_value)}" required></label>
          <label>Promo commission % for 7 to 30 days (optional) <input name="promo_rate" value=""></label>
          <label>Promo length in days <input name="promo_days" inputmode="numeric" placeholder="14"></label>
          <label>What it is <input name="fact_what" value=""></label>
          <label>Who it is for <input name="fact_who" value=""></label>
          <label><input type="checkbox" name="selling_fast" value="yes"> Selling fast</label>
          <label><input type="checkbox" name="samples_enabled" value="yes"> Samples are available for this product</label>
          <label>Description <textarea name="description">{html.escape(edit.get('description') or '')}</textarea></label>
          <button type="submit">Save product</button>
        </form>
        <h2>Add from a CSV</h2>
        <p>Upload a spreadsheet, tick the rows you want, and leave the rest. Nothing is ticked to start with. Drafts are not used for matching until you add why you chose them, the value for creators, and a commission.</p>
        <p><a href="/seller/products/csv-template">Download the template</a></p>
        <form method="post" action="/seller/products/import" enctype="multipart/form-data">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Products CSV <input type="file" name="file" accept=".csv,text/csv" required></label>
          <button type="submit">Preview CSV</button>
        </form>
        """
    return f"""
    <h1>Products</h1>
    <p class="curation">{html.escape(catalogue.PRODUCTS_COPY)}</p>
    <p>{html.escape(supplier_copy.PRODUCT_CAP)}</p>
    <p id="product-count">{count} {noun}</p>
    {warning_html}
    {first}
    <ul>{''.join(rows) or '<li>No products yet.</li>'}</ul>
    {form}
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
              <label>Value for creators <textarea name="value_to_creators_{index}" required>{html.escape(row.get('value_to_creators') or '')}</textarea></label>
              <label>Commission percent <input name="commission_{index}" value="{html.escape(row.get('commission_text') or '')}" required></label>
            </fieldset>
            """
        )
    return f"""
    <h1>Finish each product</h1>
    <p>These products are not used for matching until each one has a reason, the value for creators, and a commission.</p>
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
    if page == "home":
        import quality_standing

        standing = quality_standing.standing_for("supplier", shop.get("id") or "")
        notice = standing.get("notice") or ""
        if standing.get("demoted"):
            notice = "Pays on time and Top seller are paused after repeated missed quality months."
        notice_html = f"<p class='banner'>{html.escape(notice)}</p>" if notice else ""
        return f"""
        <h1>Home</h1>
        <p>{html.escape(supplier_copy.HOME_LINE)}</p>
        <p>{html.escape(recognition.momentum_line(int(data.get('confirmed_7d') or 0)))}</p>
        <p>{html.escape(recognition.QUALITY_STANDARD_SUPPLIER)}</p>
        {notice_html}
        <form method="post" action="/seller/quality-review">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Month <input name="month" placeholder="2026-11" required></label>
          <label>What happened <input name="note"></label>
          <p>{html.escape(supplier_copy.REVIEW_NOTE)}</p>
          <button type="submit">Request a review</button>
        </form>
        <p><a href="/seller/products">Open catalogue</a></p>
        """
    if page == "samples":
        import samples as sample_flow

        cards = []
        for row in data.get("samples") or []:
            status = sample_flow.effective_status(row)
            address = sample_flow.address_for(row, viewer="supplier")
            address_html = f"<p>Ship to: {html.escape(address)}</p>" if address else "<p>The address stays hidden until you approve.</p>"
            actions = ""
            if status == "requested":
                actions = f"""
                <form method="post" action="/seller/samples/{html.escape(row['id'])}/approve">
                  <input type="hidden" name="csrf_token" value="{csrf}"><button type="submit">Approve</button>
                </form>
                <form method="post" action="/seller/samples/{html.escape(row['id'])}/decline">
                  <input type="hidden" name="csrf_token" value="{csrf}">
                  <label>Reason <input name="reason"></label>
                  <button class="secondary" type="submit">Decline</button>
                </form>
                """
            if status == "approved":
                actions = f"""
                <form method="post" action="/seller/samples/{html.escape(row['id'])}/ship">
                  <input type="hidden" name="csrf_token" value="{csrf}">
                  <label>Tracking number or link <input name="tracking"></label>
                  <button type="submit">Mark shipped</button>
                </form>
                """
            cards.append(
                f"<article><h2>{html.escape(row.get('product_title') or 'Sample')}</h2>"
                f"<p>Status: {html.escape(status)}</p><p>{html.escape(row.get('note') or '')}</p>"
                f"{address_html}{actions}</article>"
            )
        settings = data.get("sample_settings") or {}
        return f"""
        <h1>Sample requests</h1>
        {''.join(cards) or '<p>No sample requests yet.</p>'}
        <h2>Sample settings</h2>
        <form method="post" action="/seller/samples/settings">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Maximum samples a month <input name="max_per_month" inputmode="numeric" value="{html.escape(str(settings.get('max_per_month') or 5))}"></label>
          <label>Days after delivery for the post (7 to 30) <input name="post_due_days" inputmode="numeric" value="{html.escape(str(settings.get('post_due_days') or 14))}"></label>
          <button type="submit">Save sample settings</button>
        </form>
        """
    if page == "badges":
        return f"""
        <h1>{html.escape(supplier_copy.BADGE_PAGE_TITLE)}</h1>
        <p>{html.escape(supplier_copy.PAYS_ON_TIME_NOTE)}</p>
        <p>{html.escape(supplier_copy.TOP_SELLER_NOTE)}</p>
        """
    if page == "creatr-tiers":
        items = "".join(f"<li>{html.escape(line)}</li>" for line in supplier_copy.GLOSSARY)
        return f"""
        <h1>What Creatr tiers mean</h1>
        <p>{html.escape(supplier_copy.GLOSSARY_LEAD)}</p>
        <ul>{items}</ul>
        """
    if page == "listing":
        return _listing_studio_page(shop, csrf, data)
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
            earned = recognition.tier_status(
                0,
                pilot_invite=bool(creator.get("founding_number") or creator.get("signup_campaign") == "founding_curators"),
            )
            handle = html.escape(str(creator.get("handle") or creator.get("influencer_id") or ""))
            handle = f"{handle} · <a href='/seller/creatr-tiers'>{html.escape(earned['label'])}</a>"
            reasons = "".join(f"<li>{html.escape(str(reason))}</li>" for reason in match.get("reasons") or [])
            estimates = html.escape(json.dumps(match.get("estimates") or {}, default=str))
            item_id = html.escape(str(match.get("shortlist_item_id") or ""))
            actions = ""
            if item_id and not data.get("locked"):
                actions = f"""
                <form method="post" action="/seller/shortlist-items/{item_id}/accept"><input type="hidden" name="csrf_token" value="{csrf}"><button type="submit">Accept</button></form>
                <form method="post" action="/seller/shortlist-items/{item_id}/decline"><input type="hidden" name="csrf_token" value="{csrf}"><button type="submit">Decline</button></form>
                """
            cards.append(f"<article><h3>{handle}</h3><p>Score {html.escape(str(match.get('score')))}</p><ul>{reasons}</ul><pre>{estimates}</pre>{actions}</article>")
        match_form = ""
        if not data.get("locked") and ready:
            match_form = f"""
        <form method="post" action="/seller/find">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Product <select name="product_id">{options}</select></label>
          <button type="submit">Run match</button>
        </form>
        """
        waiting = ""
        if not ready:
            waiting = "<p>Complete a product before it can be matched. It needs a reason, the value for Creatrs, and a commission.</p>"
        tier_filter = data.get("tier_filter") or ""
        if tier_filter == "trendsettr_or_headlinr":
            cards = [card for card in cards if "Trendsettr" in card or "Headlinr" in card]
        return f"""
        <h1>Collabs</h1>
        <p>{html.escape(supplier_copy.SHORTLIST_INTRO)}</p>
        <p><a href="/seller/find?tier=trendsettr_or_headlinr">{html.escape(supplier_copy.FILTER_LABEL)}</a></p>
        <p><a href="/seller/creatr-tiers">What Creatr tiers mean</a></p>
        <p><a href="/seller/accepted">Accepted</a> · <a href="/seller/campaigns">Campaigns</a></p>
        {waiting}
        {match_form}
        {''.join(cards) or '<p>Run a match to see ranked Creatrs.</p>'}
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
          <label>Name <input name="name" required></label>
          <button type="submit">Create campaign</button>
        </form>
        """
        return f"""
        <h1>Campaigns</h1>
        <ul>{rows or '<li>No campaigns yet.</li>'}</ul>
        {form}
        """
    if page == "billing":
        return _billing_body("seller", data.get("billing") or {}, csrf, "/seller/billing")
    return f"<h1>Accepted Creatrs</h1><ul>{''.join(rows) or '<li>None yet.</li>'}</ul><p>{html.escape(supplier_copy.ATTRIBUTION_NOTE)}</p>"


def _creator_body(page: str, profile: dict, csrf: str, *, base_url: str = "", **data) -> str:
    if page == "home":
        sales = int(data.get("confirmed_sales") or 0)
        pilot = bool(profile.get("founding_number") or profile.get("signup_campaign") == "founding_curators")
        shown = recognition.progression(sales, pilot_invite=pilot)
        import quality_standing

        standing = quality_standing.standing_for("creator", profile.get("id") or "", sales=sales, pilot_invite=pilot)
        if standing.get("demoted"):
            shown["label"] = standing["label"]
        lines = "".join(f"<li>{html.escape(line)}</li>" for line in shown["lines"])
        target = shown.get("next_sales") or shown["sales"] or 1
        notice = standing.get("notice") or ""
        if standing.get("demoted"):
            notice = "Earned tier and early access are paused after repeated missed quality months."
        notice_html = f"<p class='banner'>{html.escape(notice)}</p>" if notice else ""
        return f"""
        <h1>Home</h1>
        <p>Find products you genuinely rate. Cultivate their story, your way.</p>
        <h2>How you move up</h2>
        <p>Your tier: {html.escape(shown['label'])}.</p>
        <p>{html.escape(shown['next_line'])}</p>
        <div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax="{html.escape(str(target))}" aria-valuenow="{html.escape(str(shown['sales']))}" aria-label="{html.escape(shown['next_line'])}"><span style="width:{int(shown['percent'])}%"></span></div>
        <ul>{lines}</ul>
        <p>{html.escape(recognition.QUALITY_STANDARD_CREATR)}</p>
        {notice_html}
        <form method="post" action="/creator/quality-review">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Month <input name="month" placeholder="2026-11" required></label>
          <label>What happened <input name="note"></label>
          <button type="submit">Request a review</button>
        </form>
        <p><a href="/creator/offers">See matches</a></p>
        """
    if page == "profile":
        active = ", ".join(profile.get("active_categories") or [])
        desired = ", ".join(profile.get("desired_categories") or [])
        number = profile.get("founding_number")
        badge = f"<p>Foundr #{html.escape(str(number))}</p>" if number else ""
        return badge + f"""
        <h1>{html.escape(profile.get('handle') or '')}</h1>
        <form method="post" action="/creator/profile">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <label>Active categories <input name="active_categories" value="{html.escape(active)}"></label>
          <label>Desired categories <input name="desired_categories" value="{html.escape(desired)}"></label>
          <label><input type="checkbox" name="leaderboard_opt_in" value="yes"> Show me on the leaderboard</label>
          <p><small>{html.escape(supplier_copy.LEADERBOARD_HELP)}</small></p>
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
            if not offer.get("accepted_at") and not data.get("locked"):
                accept = (
                    f"<form method='post' action='/creator/offers/{html.escape(str(offer.get('id')))}/accept'>"
                    f"<input type='hidden' name='csrf_token' value='{csrf}'>"
                    "<button type='submit'>Accept offer</button></form>"
                )
            photo = ""
            image = offer.get("image_url")
            if image:
                photo = f"<img src='{html.escape(str(image))}' alt='' width='160' height='160'>"
            badges = []
            if offer.get("selling_fast"):
                badges.append("Selling fast")
            if offer.get("reliability_badge"):
                badges.append(str(offer["reliability_badge"]))
            if offer.get("top_seller"):
                badges.append("Top seller")
            badge_html = f"<p>{html.escape(' · '.join(badges))}</p>" if badges else ""
            facts = offer.get("key_facts") or {}
            fact_html = ""
            if facts:
                fact_html = (
                    "<section><h3>Key facts</h3>"
                    f"<p>{html.escape(str(facts.get('what') or ''))}</p>"
                    f"<p>{html.escape(str(facts.get('who') or ''))}</p></section>"
                )
            promo = ""
            if offer.get("promo_line"):
                promo = f"<p>{html.escape(str(offer['promo_line']))}</p>"
            top_note = ""
            if offer.get("top_seller"):
                top_note = f"<p><small>{html.escape(recognition.TOP_SELLER_EXPLANATION)}</small></p>"
            tips = ""
            if offer.get("listing_tips"):
                tips = (
                    "<section><h3>Listing Studio · selling tips</h3>"
                    "<p>Example suggestions</p>"
                    f"<p>{html.escape(str(offer['listing_tips']))}</p>"
                    "<p>check before you post</p></section>"
                )
            import listing_studio

            approved_scenes = listing_studio.approved_for_creatr(str(offer.get("product_id") or ""))["scenes"]
            if approved_scenes:
                links = "".join(
                    f"<li><a href='/dashboard/catalogue/listing-studio/scenes/{html.escape(scene['id'])}?product={html.escape(str(offer.get('product_id') or ''))}'>Download {html.escape(str(scene.get('type')))}</a></li>"
                    for scene in approved_scenes
                )
                tips += f"<section><h3>Approved scenes</h3><ul>{links}</ul><p>check before you post</p></section>"
            blocks.append(
                f"<article>{photo}<h2>{html.escape(str(offer.get('product_title') or 'Product'))}</h2>"
                f"<p>{html.escape(str(offer.get('shop_name') or ''))}</p>"
                f"{badge_html}{promo}{fact_html}{top_note}{tips}"
                f"<p>Commission: {html.escape(rate_text)} Plantr does not pay this.</p>"
                f"<ul>{points or '<li>No talking points yet.</li>'}</ul>"
                f"{accept}"
                f"<p><a href='/creator/samples/request?product={html.escape(str(offer.get('product_id') or ''))}'>Request a sample</a></p>"
                f"{_tracking_block(offer.get('discount_code'), offer.get('tracking_path'), base_url, offer.get('platform_links'), offer.get('direct_url'))}</article>"
            )
        return f"<h1>Matches</h1>{''.join(blocks) or '<p>No matches yet.</p>'}"
    if page == "sample-request":
        import samples as sample_flow

        product = data.get("product") or {}
        return f"""
        <h1>Request a sample</h1>
        <p>{html.escape(str(product.get('title') or 'This product'))}</p>
        <form method="post" action="/creator/samples">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="product_id" value="{html.escape(str(product.get('id') or ''))}">
          <label>How you plan to feature it <textarea name="note" required></textarea></label>
          <label>Address line <input name="address_line" autocomplete="address-line1" required></label>
          <label>Suburb <input name="suburb" autocomplete="address-level2"></label>
          <label>State <input name="region" autocomplete="address-level1"></label>
          <label>Postcode <input name="postcode" autocomplete="postal-code" required></label>
          <button type="submit">Request a sample</button>
        </form>
        """
    if page == "sample":
        import listing_studio
        import samples as sample_flow

        row = data.get("sample") or {}
        status = sample_flow.effective_status(row)
        kind = sample_flow.reminder_kind(row)
        reminder = ""
        if kind == "7":
            reminder = "<p class='banner'>Your post is due within 7 days.</p>"
        elif kind == "2":
            reminder = "<p class='banner'>Your post is due within 2 days.</p>"
        if status == "overdue":
            reminder = "<p class='error'>This post is overdue.</p>"
        kit = data.get("kit") or {}
        hooks = "".join(f"<li>{html.escape(item.get('hook') or '')} {html.escape(item.get('caption') or '')}</li>" for item in kit.get("angles") or [])
        scenes = "".join(
            f"<li><a href='/dashboard/catalogue/listing-studio/scenes/{html.escape(scene['id'])}?product={html.escape(str(row.get('product_id') or ''))}'>Download {html.escape(str(scene.get('type')))}</a></li>"
            for scene in kit.get("scenes") or []
        )
        code = html.escape(str(data.get("code") or ""))
        link = html.escape(str(data.get("link") or ""))
        post_form = ""
        if status in {"delivered", "overdue"}:
            post_form = f"""
            <form method="post" action="/creator/samples/{html.escape(row['id'])}/post">
              <input type="hidden" name="csrf_token" value="{csrf}">
              <label>Post link <input name="links" type="url" required></label>
              <label><input type="checkbox" name="disclosure" value="yes"> This post is clearly labelled #Ad or gifted</label>
              <p><small>{html.escape(sample_flow.DISCLOSURE_HELP)}</small></p>
              <button type="submit">Submit the post</button>
            </form>
            """
        delivered = ""
        if status == "shipped":
            delivered = f"""
            <form method="post" action="/creator/samples/{html.escape(row['id'])}/delivered">
              <input type="hidden" name="csrf_token" value="{csrf}">
              <button type="submit">Mark delivered</button>
            </form>
            """
        return f"""
        <h1>Sample</h1>
        {reminder}
        <p>Status: {html.escape(status)}</p>
        <p>{html.escape(row.get('note') or '')}</p>
        <h2>Content kit</h2>
        <ul>{hooks or '<li>No approved positioning yet.</li>'}</ul>
        <ul>{scenes}</ul>
        <p>Link or code: {link} {code}</p>
        {delivered}
        {post_form}
        """
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
    return f"<h1>Links and codes</h1><ul>{items or '<li>No links yet.</li>'}</ul>"


def _billing_body(role: str, billing: dict, csrf: str, action: str) -> str:
    notice = "" if billing.get("configured") else "<p>Stripe is not configured.</p>"
    if role == "seller":
        notice += f"<p>{html.escape(supplier_copy.BILLING_TIP)}</p>"
    tier = billing.get("plan_tier") or ("starter" if role == "seller" else "member")
    label = billing_config.tier_label(role, str(tier))
    plans = []
    for plan in billing.get("plans") or []:
        soon = ", ".join(plan.get("coming_soon") or [])
        soon_text = f" Coming soon: {html.escape(soon)}." if soon else ""
        extra = ""
        if role == "creator":
            extra = " " + html.escape(". ".join(recognition.plan_card_lines(str(plan.get("tier") or "")))) + "."
        plans.append(
            f"<li><strong>{html.escape(plan['label'])}</strong> "
            f"{html.escape(plan['monthly'])} / month or {html.escape(plan['annual'])} / year, ex GST.{extra}{soon_text}</li>"
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
    return f"""
    <h1>Subscription</h1>
    {prompt}
    {notice}
    <p>Current plan: {html.escape(str(label))}. Status: {html.escape(str(shown_status))}.</p>
    <p>Prices are ex GST. Stripe Tax is not enabled.</p>
    <ul>{''.join(plans)}</ul>
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


def _layout(title: str, body: str, logout_path: Optional[str], csrf: Optional[str], *, nav: str = "") -> str:
    logout = ""
    if logout_path and csrf:
        logout = (
            f'<form method="post" action="{html.escape(logout_path)}">'
            f'<input type="hidden" name="csrf_token" value="{html.escape(csrf)}">'
            "<button class='secondary' type='submit'>Sign out</button></form>"
        )
    return brand_ui.document(title, body, nav=nav, logout=logout)


def _approved_listing_tips(product_id: Optional[str]) -> str:
    if not product_id:
        return ""
    product = db.memory_products.get(product_id) if not db.configured() else None
    if db.configured():
        with db.session_scope() as session:
            product = session.get(Product, product_id)
            attrs = dict(getattr(product, "attributes", None) or {}) if product is not None else {}
    else:
        attrs = dict(getattr(product, "attributes", None) or {})
    if recognition.listing_status(attrs) != "Approved":
        return ""
    return str(attrs.get("listing_tips") or "")


def save_listing_tips(shop_id: str, product_id: str, tips: str, *, approved: bool) -> None:
    problem = recognition.tips_problem(tips)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    status = "Approved" if approved else "Awaiting your approval"

    def _apply(product):
        if product is None or getattr(product, "shop_id", None) != shop_id:
            raise HTTPException(status_code=404, detail="Product not found")
        attrs = dict(getattr(product, "attributes", None) or {})
        attrs["listing_tips"] = (tips or "").strip()
        attrs["listing_studio_status"] = status
        product.attributes = attrs

    if db.configured():
        with db.session_scope() as session:
            _apply(session.get(Product, product_id))
        return
    _apply(db.memory_products.get(product_id))


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
        "attributes": dict(getattr(row, "attributes", None) or {}),
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
                        "shopify_domain": shop.shopify_domain if shop is not None else None,
                        "image_url": product.image_url if product is not None else None,
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
                "shopify_domain": getattr(shop, "shopify_domain", None),
                "image_url": getattr(product, "image_url", None) if product is not None else None,
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
                        "shopify_domain": shop.shopify_domain if shop is not None else None,
                        "image_url": product.image_url if product is not None else None,
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
                "shopify_domain": getattr(shop, "shopify_domain", None),
                "image_url": getattr(product, "image_url", None) if product is not None else None,
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
