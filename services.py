"""Persistence for shops, creators, enrichments, and placeholder matches.

When DATABASE_URL is unset these functions use the process-local stores in db.py.
When it is set they write the SQLAlchemy models. Notion is not involved.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import db
import events
import taxonomy
from events import PlanLimitReached
from sqlalchemy import select

from models import Brief, Category, Creator, Product, Shop, Shortlist, ShortlistExclusion, ShortlistItem, utcnow

MATCH_VERSION = "rules-v1"
DISCLOSURE_LINE = "Paid partnership. The creator may earn a commission."


def create_shop(
    *,
    name: str,
    shopify_domain: Optional[str],
    plan_tier: Optional[str],
    plan_status: Optional[str],
    is_pilot: bool,
    is_underserved: bool = False,
    underserved_reason: Optional[str] = None,
    country: Optional[str] = None,
    market: Optional[str] = None,
    currency: Optional[str] = None,
    primary_category: Optional[str] = None,
) -> dict:
    tier = (plan_tier or "").strip() or "starter"
    # Pilot shops default to starter. An explicit plan_tier is kept.
    if is_pilot and not (plan_tier or "").strip():
        tier = "starter"
    if tier not in events.KNOWN_PLAN_TIERS:
        raise HTTPException(status_code=422, detail=f"Unknown plan_tier '{tier}'")
    status = _default_plan_status(tier, plan_status)
    country_code = _country_code(country)
    market_code = _market_code(market)
    currency_code = _currency_code(currency)
    if primary_category:
        validate_category_lists([primary_category])
    shop_id = str(uuid.uuid4())
    now = utcnow()
    fields = dict(
        id=shop_id,
        name=name,
        shopify_domain=(shopify_domain or None) or None,
        plan_tier=tier,
        plan_status=status,
        is_pilot=bool(is_pilot),
        is_underserved=bool(is_underserved),
        underserved_reason=(underserved_reason or None),
        country=country_code,
        market=market_code,
        currency=currency_code,
        primary_category=(primary_category or None),
        products_enriched=0,
        matches_run=0,
        briefs_generated=0,
        first_enrich_at=None,
        first_match_at=None,
        first_brief_at=None,
        shopify_connected_at=None,
        first_attributed_sale_at=None,
        first_paid_at=None,
        last_match_at=None,
        last_campaign_at=None,
        created_at=now,
    )
    try:
        if db.configured():
            with db.session_scope() as session:
                shop = Shop(**fields)
                session.add(shop)
                session.flush()
                events.emit("shop_signed_up", shop, "api")
                return shop_dict(shop)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="A shop with this Shopify domain already exists") from exc
    shop = SimpleNamespace(memory_kind="shop", **fields)
    db.memory_shops[shop_id] = shop
    events.emit("shop_signed_up", shop, "api")
    return shop_dict(shop)


def fetch_shop(shop_id: str) -> Optional[dict]:
    if db.configured():
        with db.session_scope() as session:
            shop = session.get(Shop, shop_id)
            return shop_dict(shop) if shop is not None else None
    shop = db.memory_shops.get(shop_id)
    return shop_dict(shop) if shop is not None else None


def shop_dict(shop: Any) -> dict:
    return {
        "id": shop.id,
        "name": shop.name,
        "shopify_domain": shop.shopify_domain,
        "plan_tier": shop.plan_tier,
        "plan_status": shop.plan_status,
        "is_pilot": bool(shop.is_pilot),
        "is_underserved": bool(getattr(shop, "is_underserved", False)),
        "underserved_reason": getattr(shop, "underserved_reason", None),
        "country": getattr(shop, "country", None),
        "market": getattr(shop, "market", None),
        "currency": getattr(shop, "currency", None),
        "primary_category": getattr(shop, "primary_category", None),
        "mrr": events.mrr(shop.plan_tier, status=shop.plan_status),
        "products_enriched": int(shop.products_enriched or 0),
        "matches_run": int(shop.matches_run or 0),
        "briefs_generated": int(shop.briefs_generated or 0),
        "first_enrich_at": shop.first_enrich_at,
        "first_match_at": shop.first_match_at,
        "first_brief_at": shop.first_brief_at,
        "shopify_connected_at": getattr(shop, "shopify_connected_at", None),
        "first_attributed_sale_at": getattr(shop, "first_attributed_sale_at", None),
        "first_paid_at": getattr(shop, "first_paid_at", None),
        "last_match_at": getattr(shop, "last_match_at", None),
        "last_campaign_at": getattr(shop, "last_campaign_at", None),
        "created_at": shop.created_at,
        "stripe_customer_id": getattr(shop, "stripe_customer_id", None),
    }


def _default_plan_status(plan_tier: str, explicit: Optional[str]) -> str:
    """Starter and free default to free. Paid growth and scale default to active."""
    chosen = (explicit or "").strip()
    if chosen:
        return chosen
    if plan_tier in {"growth", "scale"}:
        return "active"
    return "free"


def allowed_category_slugs() -> set[str]:
    allowed = taxonomy.slugs()
    if not db.configured():
        return allowed
    with db.session_scope() as session:
        allowed.update(session.scalars(select(Category.slug)).all())
    return allowed


def validate_category_lists(*groups: list) -> None:
    allowed = allowed_category_slugs()
    unknown = [item for group in groups for item in group if item not in allowed]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown categories: {unknown}. Use a taxonomy slug, including 'other'.",
        )


def persist_creator(record: dict) -> None:
    active = list(record.get("active_categories") or [])
    desired = list(record.get("desired_categories") or [])
    validate_category_lists(active, desired)
    if not db.configured():
        db.memory_creators[record["influencer_id"]] = record
        creator = SimpleNamespace(
            memory_kind="creator",
            id=record["influencer_id"],
            handle=record.get("handle"),
            active_categories=active,
            preferred_promotion_styles=list(record.get("preferred_promotion_styles") or []),
            platforms=list(record.get("platforms") or []),
            profile=record,
            profile_completed_at=None,
            first_match_accepted_at=None,
            first_post_at=None,
        )
        db.memory_creator_objects[record["influencer_id"]] = creator
        events.emit("creator_onboarded", creator, "api", handle=record.get("handle"))
        return
    try:
        with db.session_scope() as session:
            creator = Creator(
                id=record["influencer_id"],
                handle=record["handle"],
                name=record.get("name"),
                bio=record.get("bio"),
                interests=list(record.get("areas_of_interest") or []),
                expertise_level=record.get("expertise_level") or {},
                preferred_promotion_styles=list(record.get("preferred_promotion_styles") or []),
                audience_demographics=record.get("audience_demographics"),
                content_formats=list(record.get("content_formats") or []),
                past_categories=list(record.get("past_categories") or []),
                restrictions=list(record.get("restrictions") or []),
            excluded_categories=list(record.get("excluded_categories") or []),
            excluded_claims=list(record.get("excluded_claims") or []),
            active_categories=active,
            desired_categories=desired,
            avg_views=record.get("avg_views"),
            engagement_rate=record.get("engagement_rate"),
            metrics_source=record.get("metrics_source") or "self_reported",
            metrics_as_of=_aware_timestamp(record.get("metrics_as_of")),
            est_conversion_rate=record.get("est_conversion_rate"),
            avg_order_value=record.get("avg_order_value"),
            typical_price_band=record.get("typical_price_band"),
                availability=record.get("availability") or "open",
                commission_preference=record.get("commission_preference"),
                platforms=list(record.get("platforms") or []),
                follower_band=record.get("follower_band"),
                profile=record,
                created_at=utcnow(),
            )
            session.add(creator)
            session.flush()
            events.emit("creator_onboarded", creator, "api", handle=creator.handle)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="A creator with this handle already exists") from exc


def fetch_creator(creator_id: str) -> Optional[dict]:
    if not db.configured():
        record = db.memory_creators.get(creator_id)
        if record is None:
            return None
        return {"id": creator_id, "influencer": record}
    with db.session_scope() as session:
        creator = session.get(Creator, creator_id)
        if creator is None:
            return None
        return {
            "id": creator.id,
            "influencer": creator.profile,
            "created_at": creator.created_at,
        }


def prepare_enrichment(shop_id: Optional[str], product_id: Optional[str]) -> None:
    """Resolve the shop and enforce the product cap before a Grok call.

    No-op when neither id is present. Raises 404 when the id does not resolve
    and 402 when a non-pilot shop is already at its product cap.
    """
    shop_id = _clean(shop_id)
    product_id = _clean(product_id)
    if not shop_id and not product_id:
        return
    try:
        if db.configured():
            with db.session_scope() as session:
                shop, product = _resolve_db(session, shop_id, product_id)
                if product is None:
                    events.guard_plan_limit(shop, "products", session)
            return
        shop, product = _resolve_memory(shop_id, product_id)
        if product is None:
            events.guard_plan_limit(shop, "products", None)
    except PlanLimitReached as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc


def save_enrichment(
    *,
    shop_id: Optional[str],
    product_id: Optional[str],
    creator_id: Optional[str],
    title: str,
    description_short: str,
    description_long: str,
    category: str,
    subcategory: Optional[str],
    images: list,
    price: Optional[float],
    currency: Optional[str],
    tags: list,
    attributes: dict,
    claims: list,
    commission_rate: Optional[float] = None,
    fields_set: Optional[set] = None,
    source: str,
    talking_points: list,
    faqs: list,
) -> Optional[dict]:
    shop_id = _clean(shop_id)
    product_id = _clean(product_id)
    creator_id = _clean(creator_id)
    if not shop_id and not product_id:
        return None
    if category and category.strip():
        validate_category_lists([category.strip()])
    subcategory = _subcategory_value(subcategory, category)
    brief_source = source if source in ("grok", "fallback") else "fallback"
    content = json.dumps({"talking_points": talking_points, "faqs": faqs})
    if db.configured():
        with db.session_scope() as session:
            shop, product = _resolve_db(session, shop_id, product_id)
            product = _apply_product(
                session,
                shop,
                product,
                product_id,
                fields_set=fields_set or set(),
                title=title,
                description_short=description_short,
                description_long=description_long,
                category=category,
                subcategory=subcategory,
                images=images,
                price=price,
                currency=currency,
                tags=tags,
                attributes=attributes,
                claims=claims,
                commission_rate=commission_rate,
            )
            session.flush()
            events.emit("product_enriched", product, "api", source=brief_source)
            stored_creator = _existing_creator_id(session, creator_id)
            brief = Brief(
                id=str(uuid.uuid4()),
                shop_id=shop.id,
                product_id=product.id,
                creator_id=stored_creator,
                shortlist_item_id=_matching_shortlist_item_id(session, product.id, stored_creator),
                content=content,
                source=brief_source,
                disclosure_line=DISCLOSURE_LINE,
                sent_at=None,
                created_at=utcnow(),
                product=product,
                shop=shop,
            )
            session.add(brief)
            session.flush()
            return {"product_id": product.id, "brief_id": brief.id}

    shop, product = _resolve_memory(shop_id, product_id)
    product = _apply_product_memory(
        shop,
        product,
        product_id,
        fields_set=fields_set or set(),
        title=title,
        description_short=description_short,
        description_long=description_long,
        category=category,
        subcategory=subcategory,
        images=images,
        price=price,
        currency=currency,
        tags=tags,
        attributes=attributes,
        claims=claims,
        commission_rate=commission_rate,
    )
    events.emit("product_enriched", product, "api", source=brief_source)
    brief = SimpleNamespace(
        memory_kind="brief",
        id=str(uuid.uuid4()),
        shop_id=shop.id,
        shop=shop,
        product_id=product.id,
        product=product,
        creator_id=creator_id if creator_id in db.memory_creators else None,
        shortlist_item_id=_matching_shortlist_item_memory(
            product.id, creator_id if creator_id in db.memory_creators else None
        ),
        content=content,
        source=brief_source,
        disclosure_line=DISCLOSURE_LINE,
        sent_at=None,
        created_at=utcnow(),
    )
    db.memory_briefs.append(brief)
    return {"product_id": product.id, "brief_id": brief.id}


def _matching_shortlist_item_id(session, product_id: Optional[str], creator_id: Optional[str]) -> Optional[str]:
    if not product_id or not creator_id:
        return None
    item = session.scalars(
        select(ShortlistItem)
        .join(Shortlist, Shortlist.id == ShortlistItem.shortlist_id)
        .where(Shortlist.product_id == product_id, ShortlistItem.creator_id == creator_id)
        .order_by(Shortlist.created_at.desc())
    ).first()
    return item.id if item is not None else None


def _matching_shortlist_item_memory(product_id: Optional[str], creator_id: Optional[str]) -> Optional[str]:
    if not product_id or not creator_id:
        return None
    shortlists = {row.id for row in db.memory_shortlists if getattr(row, "product_id", None) == product_id}
    matches = [
        row for row in db.memory_shortlist_items if row.creator_id == creator_id and row.shortlist_id in shortlists
    ]
    return matches[-1].id if matches else None


def _store_exclusions(session, shortlist_id: str, excluded: list) -> None:
    for row in excluded:
        session.add(
            ShortlistExclusion(
                id=str(uuid.uuid4()),
                shortlist_id=shortlist_id,
                creator_id=row.get("creator_id"),
                handle=row.get("handle"),
                reasons=list(row.get("reasons") or []),
                created_at=utcnow(),
            )
        )


def decide_shortlist_item(item_id: str, event_name: str, reason: Optional[str] = None) -> dict:
    if event_name not in {"match_accepted", "match_declined"}:
        raise HTTPException(status_code=422, detail="Unsupported shortlist decision")
    if not db.configured():
        item = next((row for row in db.memory_shortlist_items if row.id == item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Shortlist item not found")
        _reject_if_decided(item)
        events.emit(event_name, item, "api", decline_reason=reason)
        _issue_link_for_acceptance(item, event_name)
        return _decision_payload(item)
    with db.session_scope() as session:
        item = session.get(ShortlistItem, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Shortlist item not found")
        _reject_if_decided(item)
        item.shortlist = session.get(Shortlist, item.shortlist_id)
        if item.creator_id:
            item.creator = session.get(Creator, item.creator_id)
        events.emit(event_name, item, "api", decline_reason=reason)
        _issue_link_for_acceptance(item, event_name)
        return _decision_payload(item)


def _issue_link_for_acceptance(item, event_name: str) -> None:
    if event_name != "match_accepted":
        return
    import attribution

    shortlist = getattr(item, "shortlist", None)
    attribution.ensure_link_for_acceptance(
        creator_id=getattr(item, "creator_id", None),
        product_id=getattr(shortlist, "product_id", None),
        shortlist_item_id=getattr(item, "id", None),
    )


def _reject_if_decided(item) -> None:
    if getattr(item, "accepted_at", None) or getattr(item, "declined_at", None):
        raise HTTPException(status_code=409, detail="Shortlist item has already been decided")


def _decision_payload(item) -> dict:
    return {
        "id": item.id,
        "accepted_at": getattr(item, "accepted_at", None),
        "declined_at": getattr(item, "declined_at", None),
        "decline_reason": getattr(item, "decline_reason", None),
    }


def mark_brief_sent(brief_id: str, actor: str) -> dict:
    """Record that a brief was actually sent. Operators pass actor='operator'."""
    if actor not in events.ACTORS:
        raise HTTPException(status_code=422, detail="actor must be 'system', 'operator', or 'api'")
    if not db.configured():
        brief = next((row for row in db.memory_briefs if row.id == brief_id), None)
        if brief is None:
            raise HTTPException(status_code=404, detail="Brief not found")
        events.emit("brief_sent", brief, actor)
        return {"id": brief.id, "shop_id": brief.shop_id, "sent_at": brief.sent_at, "actor": actor}
    with db.session_scope() as session:
        brief = session.get(Brief, brief_id)
        if brief is None:
            raise HTTPException(status_code=404, detail="Brief not found")
        if brief.shop is None and brief.shop_id:
            brief.shop = session.get(Shop, brief.shop_id)
        events.emit("brief_sent", brief, actor)
        return {"id": brief.id, "shop_id": brief.shop_id, "sent_at": brief.sent_at, "actor": actor}


def load_match_inputs(
    *,
    product: Optional[dict],
    product_id: Optional[str],
    creators: Optional[list],
    all_onboarded: bool,
    shop_id: Optional[str],
) -> dict:
    """Resolve the product, creators, and shop for a rules match."""
    product_id = _clean(product_id)
    shop_id = _clean(shop_id)
    if db.configured():
        with db.session_scope() as session:
            stored = session.get(Product, product_id) if product_id else None
            if product_id and stored is None and not product:
                raise HTTPException(status_code=404, detail="Product not found")
            product_payload = _product_payload(stored) if stored is not None else dict(product or {})
            if stored is not None and product:
                product_payload.update({key: value for key, value in product.items() if value not in (None, "", [], {})})
            if stored is not None:
                product_id = stored.id
                shop_id = shop_id or stored.shop_id
            shop = session.get(Shop, shop_id) if shop_id else None
            if shop_id and shop is None:
                raise HTTPException(status_code=404, detail="Shop not found")
            if shop is not None and stored is not None and stored.shop_id != shop.id:
                raise HTTPException(status_code=400, detail="Product does not belong to this shop")
            creator_rows = _creator_payloads(session, creators, all_onboarded)
            return {
                "product": product_payload,
                "creators": creator_rows,
                "shop": _shop_payload(shop) if shop is not None else None,
                "shop_id": shop.id if shop is not None else None,
                "product_id": product_id,
            }
    if product_id and product_id not in db.memory_products and not product:
        raise HTTPException(status_code=404, detail="Product not found")
    stored = db.memory_products.get(product_id) if product_id else None
    product_payload = _product_payload(stored) if stored is not None else dict(product or {})
    if not product_payload:
        raise HTTPException(status_code=422, detail="Provide a product or a product_id")
    shop = db.memory_shops.get(shop_id) if shop_id else None
    if shop_id and shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return {
        "product": product_payload,
        "creators": _memory_creators(creators, all_onboarded),
        "shop": _shop_payload(shop) if shop is not None else None,
        "shop_id": shop_id,
        "product_id": product_id,
    }


def persist_rules_match(
    *,
    shop_id: Optional[str],
    product_id: Optional[str],
    matches: list,
    excluded: Optional[list] = None,
    optimise_for: str = "balanced",
) -> tuple[Optional[str], list[str]]:
    """Store the ranked creators. Excluded creators are stored with their reasons, not as items.

    Returns the shortlist id and the shortlist item ids in match order.
    """
    shop_id = _clean(shop_id)
    product_id = _clean(product_id)
    try:
        if db.configured():
            return _persist_rules_db(shop_id, product_id, matches, excluded or [], optimise_for)
        if not shop_id:
            return None, []
        return _persist_rules_memory(shop_id, product_id, matches, excluded or [], optimise_for)
    except PlanLimitReached as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc


def _persist_rules_db(shop_id, product_id, matches, excluded, optimise_for) -> tuple[str, list[str]]:
    with db.session_scope() as session:
        shop = session.get(Shop, shop_id) if shop_id else None
        if shop_id and shop is None:
            raise HTTPException(status_code=404, detail="Shop not found")
        if shop is not None:
            events.guard_plan_limit(shop, "matches_per_month", session)
        product = session.get(Product, product_id) if product_id else None
        if product_id and product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        shortlist = Shortlist(
            id=str(uuid.uuid4()),
            shop_id=shop.id if shop is not None else None,
            product_id=product.id if product is not None else None,
            match_version=MATCH_VERSION,
            optimise_for=optimise_for,
            created_at=utcnow(),
            shop=shop,
        )
        session.add(shortlist)
        session.flush()
        _store_exclusions(session, shortlist.id, excluded)
        item_ids = []
        for index, match in enumerate(matches, start=1):
            creator_id = match.get("creator_id")
            if creator_id and session.get(Creator, creator_id) is None:
                creator_id = None
            item_id = str(uuid.uuid4())
            item_ids.append(item_id)
            session.add(
                ShortlistItem(
                    id=item_id,
                    shortlist_id=shortlist.id,
                    creator_id=creator_id,
                    score=float(match["score"]),
                    reasons={
                        "components": match["components"],
                        "estimates": match["estimates"],
                        "reasons": match["reasons"],
                    },
                    rank=index,
                )
            )
        session.flush()
        events.emit("match_run", shortlist, "api", match_version=MATCH_VERSION, shortlist_id=shortlist.id)
        return shortlist.id, item_ids


def _persist_rules_memory(shop_id, product_id, matches, excluded, optimise_for) -> tuple[str, list[str]]:
    shop = db.memory_shops.get(shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    events.guard_plan_limit(shop, "matches_per_month", None)
    shortlist = SimpleNamespace(
        memory_kind="shortlist",
        id=str(uuid.uuid4()),
        shop_id=shop.id,
        shop=shop,
        product_id=product_id,
        match_version=MATCH_VERSION,
        optimise_for=optimise_for,
        created_at=utcnow(),
    )
    db.memory_shortlists.append(shortlist)
    for row in excluded:
        db.memory_shortlist_exclusions.append(
            SimpleNamespace(
                id=str(uuid.uuid4()),
                shortlist_id=shortlist.id,
                creator_id=row.get("creator_id"),
                handle=row.get("handle"),
                reasons=list(row.get("reasons") or []),
            )
        )
    item_ids = []
    for index, match in enumerate(matches, start=1):
        creator_id = match.get("creator_id")
        creator = db.memory_creator_objects.get(str(creator_id)) if creator_id else None
        item_id = str(uuid.uuid4())
        item_ids.append(item_id)
        db.memory_shortlist_items.append(
            SimpleNamespace(
                memory_kind="shortlist_item",
                id=item_id,
                shortlist_id=shortlist.id,
                shortlist=shortlist,
                creator=creator,
                creator_id=str(creator_id) if creator_id else None,
                score=float(match["score"]),
                reasons={
                    "components": match["components"],
                    "estimates": match["estimates"],
                    "reasons": match["reasons"],
                },
                rank=index,
                accepted_at=None,
                declined_at=None,
                decline_reason=None,
            )
        )
    events.emit("match_run", shortlist, "api", match_version=MATCH_VERSION, shortlist_id=shortlist.id)
    return shortlist.id, item_ids


def _product_payload(product) -> dict:
    if product is None:
        return {}
    if isinstance(product, dict):
        return dict(product)
    commission = product.commission_rate
    return {
        "id": product.id,
        "title": product.title,
        "category": product.category,
        "subcategory": product.subcategory,
        "price": float(product.price) if product.price is not None else None,
        "currency": product.currency,
        "tags": list(product.tags or []),
        "attributes": dict(product.attributes or {}),
        "claims": list(product.claims or []),
        "commission_rate": float(commission) if commission is not None else None,
    }


def _shop_payload(shop) -> dict:
    return {
        "id": shop.id,
        "is_underserved": bool(shop.is_underserved),
        "underserved_reason": shop.underserved_reason,
    }


def _creator_payloads(session, creators: Optional[list], all_onboarded: bool) -> list:
    if all_onboarded:
        return [_creator_from_row(row) for row in session.scalars(select(Creator)).all()]
    return [dict(creator) for creator in (creators or [])]


def _memory_creators(creators: Optional[list], all_onboarded: bool) -> list:
    if all_onboarded:
        return [dict(record) for record in db.memory_creators.values()]
    return [dict(creator) for creator in (creators or [])]


def _creator_from_row(row) -> dict:
    profile = dict(row.profile or {})
    profile.update(
        {
            "influencer_id": row.id,
            "handle": row.handle,
            "active_categories": list(row.active_categories or []),
            "desired_categories": list(row.desired_categories or []),
            "excluded_categories": list(row.excluded_categories or []),
            "excluded_claims": list(row.excluded_claims or []),
            "restrictions": list(row.restrictions or []),
            "platforms": list(row.platforms or []),
            "follower_band": row.follower_band,
            "areas_of_interest": list(row.interests or profile.get("areas_of_interest") or []),
            "audience_demographics": row.audience_demographics,
            "avg_views": row.avg_views,
            "engagement_rate": row.engagement_rate if row.engagement_rate is not None else profile.get("average_engagement_rate"),
            "est_conversion_rate": row.est_conversion_rate,
            "avg_order_value": float(row.avg_order_value) if row.avg_order_value is not None else None,
            "typical_price_band": row.typical_price_band,
        }
    )
    return profile


def _persist_match_db(influencer, ranked, shop_id, product_id) -> str:
    with db.session_scope() as session:
        shop = None
        if shop_id:
            shop = session.get(Shop, shop_id)
            if shop is None:
                raise HTTPException(status_code=404, detail="Shop not found")
            events.guard_plan_limit(shop, "matches_per_month", session)
        product = None
        if product_id:
            product = session.get(Product, product_id)
            if product is None:
                raise HTTPException(status_code=404, detail="Product not found")
            if shop is not None and product.shop_id != shop.id:
                raise HTTPException(status_code=400, detail="Product does not belong to this shop")
        shortlist = Shortlist(
            id=str(uuid.uuid4()),
            shop_id=shop.id if shop is not None else None,
            product_id=product.id if product is not None else None,
            match_version=MATCH_VERSION,
            created_at=utcnow(),
            shop=shop,
        )
        session.add(shortlist)
        session.flush()
        raw_creator_id = influencer.get("influencer_id")
        creator_id = None
        if raw_creator_id and session.get(Creator, str(raw_creator_id)) is not None:
            creator_id = str(raw_creator_id)
        item = ShortlistItem(
            id=str(uuid.uuid4()),
            shortlist_id=shortlist.id,
            creator_id=creator_id,
            score=_match_score(ranked, product_id),
            reasons=_reasons(influencer, ranked, raw_creator_id),
            rank=1,
        )
        session.add(item)
        session.flush()
        events.emit(
            "match_run",
            shortlist,
            "api",
            match_version=MATCH_VERSION,
            shortlist_id=shortlist.id,
        )
        return shortlist.id


def _persist_match_memory(influencer, ranked, shop_id, product_id) -> str:
    shop = db.memory_shops.get(shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    events.guard_plan_limit(shop, "matches_per_month", None)
    product = db.memory_products.get(product_id) if product_id else None
    if product_id and product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if product is not None and product.shop_id != shop.id:
        raise HTTPException(status_code=400, detail="Product does not belong to this shop")
    shortlist = SimpleNamespace(
        memory_kind="shortlist",
        id=str(uuid.uuid4()),
        shop_id=shop.id,
        shop=shop,
        product_id=product.id if product is not None else None,
        match_version=MATCH_VERSION,
        created_at=utcnow(),
    )
    db.memory_shortlists.append(shortlist)
    raw_creator_id = influencer.get("influencer_id")
    item = SimpleNamespace(
        id=str(uuid.uuid4()),
        shortlist_id=shortlist.id,
        creator_id=str(raw_creator_id) if raw_creator_id in db.memory_creators else None,
        score=_match_score(ranked, product_id),
        reasons=_reasons(influencer, ranked, raw_creator_id),
        rank=1,
    )
    db.memory_shortlist_items.append(item)
    events.emit(
        "match_run",
        shortlist,
        "api",
        match_version=MATCH_VERSION,
        shortlist_id=shortlist.id,
    )
    return shortlist.id


def _resolve_db(session, shop_id, product_id):
    product = session.get(Product, product_id) if product_id else None
    if product is not None:
        if shop_id and product.shop_id != shop_id:
            raise HTTPException(status_code=400, detail="Product does not belong to this shop")
        shop = session.get(Shop, product.shop_id)
        if shop is None:
            raise HTTPException(status_code=404, detail="Shop not found")
        return shop, product
    if product_id and not shop_id:
        raise HTTPException(status_code=404, detail="Product not found")
    shop = session.get(Shop, shop_id) if shop_id else None
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop, None


def _resolve_memory(shop_id, product_id):
    product = db.memory_products.get(product_id) if product_id else None
    if product is not None:
        if shop_id and product.shop_id != shop_id:
            raise HTTPException(status_code=400, detail="Product does not belong to this shop")
        shop = db.memory_shops.get(product.shop_id)
        if shop is None:
            raise HTTPException(status_code=404, detail="Shop not found")
        return shop, product
    if product_id and not shop_id:
        raise HTTPException(status_code=404, detail="Product not found")
    shop = db.memory_shops.get(shop_id) if shop_id else None
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop, None


_OPTIONAL_PRODUCT_FIELDS = (
    "images",
    "subcategory",
    "price",
    "currency",
    "tags",
    "attributes",
    "claims",
    "commission_rate",
)


def _product_values(fields: dict, fields_set: set, *, creating: bool) -> dict:
    values = {
        "title": fields["title"],
        "description_short": fields["description_short"],
        "description_long": fields["description_long"],
        "category": fields["category"],
    }
    for name in _OPTIONAL_PRODUCT_FIELDS:
        if creating or name in fields_set:
            value = fields[name]
            if name in {"images", "tags", "claims"}:
                value = list(value or [])
            if name == "attributes":
                value = dict(value or {})
            values[name] = value
    if creating:
        values.setdefault("images", [])
        values.setdefault("tags", [])
        values.setdefault("attributes", {})
        values.setdefault("claims", [])
        if not values.get("currency"):
            values["currency"] = "AUD"
        values.setdefault("source", "manual")
    return values


def _aware_timestamp(value):
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime."""
    from datetime import datetime, timezone

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="metrics_as_of must be an ISO-8601 timestamp",
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _market_code(value: Optional[str]) -> Optional[str]:
    """Uppercase a market code, using the same trim-and-validate style as country."""
    import re

    if value is None or not str(value).strip():
        return None
    code = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,31}", code):
        raise HTTPException(
            status_code=422,
            detail="market must be 1-32 characters: a letter, then letters, digits, hyphens, or underscores",
        )
    return code


def _subcategory_value(value: Optional[str], category: Optional[str]) -> Optional[str]:
    """Use taxonomy subcategories when they exist; otherwise require a short slug."""
    import re

    if value is None or not str(value).strip():
        return None
    text = str(value).strip().lower()
    known = _taxonomy_subcategories()
    if known:
        if text not in known:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown subcategory '{text}'. Use a taxonomy subcategory.",
            )
        parent = known[text]
        category_slug = (category or "").strip().lower()
        if category_slug and parent and parent != category_slug:
            raise HTTPException(
                status_code=422,
                detail=f"Subcategory '{text}' does not belong to category '{category_slug}'.",
            )
        return text
    if len(text) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", text):
        raise HTTPException(
            status_code=422,
            detail="subcategory must be a slug of up to 64 characters: lowercase letters, digits, and hyphens",
        )
    return text


def _taxonomy_subcategories() -> dict:
    """Map subcategory slug to its parent. Empty when the taxonomy has no children."""
    if not db.configured():
        return {}
    with db.session_scope() as session:
        rows = session.execute(
            select(Category.slug, Category.parent_slug).where(Category.parent_slug.is_not(None))
        ).all()
    return {slug: parent for slug, parent in rows}


def _country_code(value: Optional[str]) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    code = str(value).strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise HTTPException(status_code=422, detail="country must be an ISO-3166 alpha-2 code")
    return code


def _currency_code(value: Optional[str]) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    code = str(value).strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise HTTPException(status_code=422, detail="currency must be a 3-letter code")
    return code


def _apply_product(session, shop, product, requested_id, **fields):
    fields_set = fields.pop("fields_set")
    values = _product_values(fields, fields_set, creating=product is None)
    if product is None:
        product = Product(
            id=requested_id or str(uuid.uuid4()),
            shop_id=shop.id,
            created_at=utcnow(),
            updated_at=utcnow(),
            shop=shop,
            **values,
        )
        session.add(product)
        return product
    for name, value in values.items():
        setattr(product, name, value)
    product.updated_at = utcnow()
    product.shop = shop
    return product


def _apply_product_memory(shop, product, requested_id, **fields):
    fields_set = fields.pop("fields_set")
    values = _product_values(fields, fields_set, creating=product is None)
    if product is None:
        product = SimpleNamespace(
            memory_kind="product",
            id=requested_id or str(uuid.uuid4()),
            shop_id=shop.id,
            shop=shop,
            created_at=utcnow(),
            updated_at=utcnow(),
            **values,
        )
        db.memory_products[product.id] = product
        return product
    for name, value in values.items():
        setattr(product, name, value)
    product.updated_at = utcnow()
    product.shop = shop
    return product


def _existing_creator_id(session, creator_id: Optional[str]) -> Optional[str]:
    if creator_id and session.get(Creator, creator_id) is not None:
        return creator_id
    return None


def _match_score(ranked: list, product_id: Optional[str]) -> float:
    if not ranked:
        return 0.0
    if product_id:
        for row in ranked:
            payload = row.get("product") or {}
            payload_id = str(payload.get("product_id") or payload.get("id") or "")
            if payload_id == product_id:
                return float(row.get("match_score") or 0)
    return float(ranked[0].get("match_score") or 0)


def _reasons(influencer: dict, ranked: list, raw_creator_id) -> dict:
    return {
        "basis": MATCH_VERSION,
        "handle": influencer.get("handle"),
        "influencer_id": raw_creator_id,
        "scores": [
            {"title": (row.get("product") or {}).get("title"), "match_score": row.get("match_score")}
            for row in ranked[:10]
        ],
    }


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
