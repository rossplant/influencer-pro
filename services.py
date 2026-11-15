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

import db
import events
from events import PlanLimitReached
from models import Brief, Creator, Product, Shop, Shortlist, ShortlistItem, utcnow

MATCH_VERSION = "placeholder-0"
DISCLOSURE_LINE = "Paid partnership. The creator may earn a commission."


def create_shop(
    *,
    name: str,
    shopify_domain: Optional[str],
    plan_tier: Optional[str],
    plan_status: Optional[str],
    is_pilot: bool,
) -> dict:
    tier = (plan_tier or "").strip() or "starter"
    # Pilot shops default to starter. An explicit plan_tier is kept.
    if is_pilot and not (plan_tier or "").strip():
        tier = "starter"
    if tier not in events.KNOWN_PLAN_TIERS:
        raise HTTPException(status_code=422, detail=f"Unknown plan_tier '{tier}'")
    status = (plan_status or "").strip() or "active"
    shop_id = str(uuid.uuid4())
    now = utcnow()
    fields = dict(
        id=shop_id,
        name=name,
        shopify_domain=(shopify_domain or None),
        plan_tier=tier,
        plan_status=status,
        is_pilot=bool(is_pilot),
        products_enriched=0,
        matches_run=0,
        briefs_generated=0,
        first_enrich_at=None,
        first_match_at=None,
        first_brief_at=None,
        created_at=now,
    )
    if db.configured():
        with db.session_scope() as session:
            shop = Shop(**fields)
            session.add(shop)
            session.flush()
            return shop_dict(shop)
    shop = SimpleNamespace(memory_kind="shop", **fields)
    db.memory_shops[shop_id] = shop
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
        "mrr": events.mrr(shop.plan_tier, status=shop.plan_status),
        "products_enriched": int(shop.products_enriched or 0),
        "matches_run": int(shop.matches_run or 0),
        "briefs_generated": int(shop.briefs_generated or 0),
        "first_enrich_at": shop.first_enrich_at,
        "first_match_at": shop.first_match_at,
        "first_brief_at": shop.first_brief_at,
        "created_at": shop.created_at,
    }


def persist_creator(record: dict) -> None:
    if not db.configured():
        db.memory_creators[record["influencer_id"]] = record
        creator = SimpleNamespace(
            memory_kind="creator",
            id=record["influencer_id"],
            handle=record.get("handle"),
        )
        events.emit("creator_onboarded", creator, "api", handle=record.get("handle"))
        return
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
            average_engagement_rate=record.get("average_engagement_rate"),
            past_categories=list(record.get("past_categories") or []),
            restrictions=list(record.get("restrictions") or []),
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
                shop, _product = _resolve_db(session, shop_id, product_id)
                events.guard_plan_limit(shop, "products", session)
            return
        shop, _product = _resolve_memory(shop_id, product_id)
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
    images: list,
    pet_safe: Optional[bool],
    light: Optional[str],
    care_level: Optional[str],
    size: Optional[str],
    price: Optional[float],
    claims: list,
    fields_set: Optional[set] = None,
    source: str,
    talking_points: list,
    faqs: list,
) -> Optional[str]:
    shop_id = _clean(shop_id)
    product_id = _clean(product_id)
    creator_id = _clean(creator_id)
    if not shop_id and not product_id:
        return None
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
                images=images,
                pet_safe=pet_safe,
                light=light,
                care_level=care_level,
                size=size,
                price=price,
                claims=claims,
            )
            session.flush()
            events.emit("product_enriched", product, "api", source=brief_source)
            brief = Brief(
                id=str(uuid.uuid4()),
                product_id=product.id,
                creator_id=_existing_creator_id(session, creator_id),
                content=content,
                source=brief_source,
                disclosure_line=DISCLOSURE_LINE,
                created_at=utcnow(),
                product=product,
            )
            session.add(brief)
            session.flush()
            events.emit("brief_generated", brief, "api", source=brief_source)
            return product.id

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
        images=images,
        pet_safe=pet_safe,
        light=light,
        care_level=care_level,
        size=size,
        price=price,
        claims=claims,
    )
    events.emit("product_enriched", product, "api", source=brief_source)
    brief = SimpleNamespace(
        memory_kind="brief",
        id=str(uuid.uuid4()),
        product_id=product.id,
        product=product,
        creator_id=creator_id if creator_id in db.memory_creators else None,
        content=content,
        source=brief_source,
        disclosure_line=DISCLOSURE_LINE,
        created_at=utcnow(),
    )
    db.memory_briefs.append(brief)
    events.emit("brief_generated", brief, "api", source=brief_source)
    return product.id


def persist_match(
    *,
    influencer: dict,
    ranked: list,
    shop_id: Optional[str],
    product_id: Optional[str],
) -> Optional[str]:
    """Record a placeholder shortlist. Returns the shortlist id, or None in memory with no shop.

    A configured database records every match. Memory mode records a shortlist
    only when shop_id is sent, so the historical no-shop call stays a pure score.
    """
    shop_id = _clean(shop_id)
    product_id = _clean(product_id)
    try:
        if db.configured():
            return _persist_match_db(influencer, ranked, shop_id, product_id)
        if not shop_id:
            return None
        return _persist_match_memory(influencer, ranked, shop_id, product_id)
    except PlanLimitReached as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc


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


_OPTIONAL_PRODUCT_FIELDS = ("images", "pet_safe", "light", "care_level", "size", "price", "claims")


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
            if name in {"images", "claims"}:
                value = list(value or [])
            values[name] = value
    if creating:
        values.setdefault("images", [])
        values.setdefault("claims", [])
    return values


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
