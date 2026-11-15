"""Append-only events and plan-limit checks.

events.emit(name, entity, actor, **props) inserts the event, updates usage
counters, and stamps milestones. first_* columns are set only when they are
still empty. last_* columns are updated on every matching event.

Shop events copy plan_tier and plan_status into properties. Catalogue and
period counters are columns on the event, not JSON: products_count,
product_limit, matches_count_period, match_limit, enrichments_count_period,
briefs_count_period, billing_period_start, and limit_hit.

Starter allows 5 products, 1 campaign, and 3 match runs per UTC calendar month.
Growth allows 50 products, 5 campaigns, and 25 matches. Scale allows 100 matches
and does not cap campaigns or products. Prices are AUD ex GST: Growth 79 or 790
a year, Scale 229 or 2290 a year. Annual MRR is the annual price / 12.
Free, trial, trialing, invited, and starter contribute 0.

Hand-run operator actions use the same event names with actor='operator'.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import object_session

import billing_config
import db
from models import Brief, Campaign, Creator, Event, Product, Shop, Shortlist, ShortlistItem, utcnow

ACTORS = frozenset({"system", "operator", "api"})

# event name -> shop counter incremented on every occurrence
USAGE_COUNTERS = {
    "product_enriched": "products_enriched",
    "match_run": "matches_run",
    "brief_sent": "briefs_generated",
}

# first = set only while null. last = set on every occurrence.
SHOP_MILESTONES = {
    "product_enriched": (("first_enrich_at", "first"),),
    "match_run": (("first_match_at", "first"), ("last_match_at", "last")),
    "brief_sent": (("first_brief_at", "first"), ("last_campaign_at", "last")),
    "shopify_connected": (("shopify_connected_at", "first"),),
    "attributed_sale": (("first_attributed_sale_at", "first"),),
    "payment_received": (("first_paid_at", "first"),),
}

CREATOR_MILESTONES = {
    "creator_onboarded": (("profile_completed_at", "first"),),
    "profile_completed": (("profile_completed_at", "first"),),
    "match_accepted": (("first_match_accepted_at", "first"),),
    "creator_posted": (("first_post_at", "first"),),
}

# None means the plan does not cap that dimension.
PLAN_CAPS = {
    "starter": {**billing_config.shop_limits("starter"), "campaigns": billing_config.shop_limits("starter")["campaigns"]},
    "free": billing_config.shop_limits("starter"),
    "growth": billing_config.shop_limits("growth"),
    "scale": billing_config.shop_limits("scale"),
}
PLAN_CAPS["starter"] = billing_config.shop_limits("starter")
PLAN_CAPS["free"] = billing_config.shop_limits("starter")

PLAN_MONTHLY_PRICE = {
    "starter": 0,
    "free": 0,
    "growth": billing_config.monthly_price("growth"),
    "scale": billing_config.monthly_price("scale"),
}

PLAN_ANNUAL_PRICE = {
    "starter": 0,
    "free": 0,
    "growth": billing_config.annual_price("growth"),
    "scale": billing_config.annual_price("scale"),
}

KNOWN_PLAN_TIERS = frozenset(PLAN_CAPS)

CHANGE_TYPES = frozenset(
    {"new", "expansion", "contraction", "churn", "reactivation", "trial_started", "trial_converted"}
)


class PlanLimitReached(Exception):
    def __init__(self, kind: str, observed: int, cap: int):
        self.kind = kind
        self.observed = observed
        self.cap = cap
        super().__init__(f"Plan limit reached: {kind} ({observed}/{cap})")


def mrr(
    plan_tier: str,
    *,
    price: Optional[float] = None,
    interval: str = "month",
    status: str = "active",
) -> float:
    """Monthly recurring revenue.

    With no explicit price, monthly MRR is the list price (growth 79, scale 229)
    and annual MRR is the annual list price / 12 (growth 790, scale 2290).
    An explicit price is used as-is for month and divided by 12 for year.
    Free, trial, trialing, and starter contribute 0.
    """
    if (status or "").lower() in {"free", "trial", "trialing", "invited"}:
        return 0.0
    if plan_tier in {"starter", "free", "trial", "member"}:
        return 0.0
    period = (interval or "month").lower()
    if price is None:
        table = PLAN_ANNUAL_PRICE if period == "year" else PLAN_MONTHLY_PRICE
        amount = float(table.get(plan_tier, 0))
    else:
        amount = float(price)
    if period == "year":
        return amount / 12.0
    return amount


def record_admin(name: str, **props: Any) -> Any:
    """Append an admin login event. Passwords are refused and never stored."""
    if any("password" in key.lower() for key in props):
        raise ValueError("admin events must not include a password")
    from types import SimpleNamespace

    payload = {
        "id": _new_id(),
        "occurred_at": utcnow(),
        "event_name": name,
        "entity_type": "admin",
        "entity_id": "admin",
        "shop_id": None,
        "actor": "operator",
        "request_id": db.current_request_id(),
        "properties": dict(props),
    }
    if not db.configured():
        event = SimpleNamespace(**payload)
        db.memory_events.append(event)
        return event
    with db.session_scope() as session:
        event = Event(**payload)
        session.add(event)
        session.flush()
        return event


def record_link_click(link: Any) -> Any:
    """Append a click for a tracking code. This does not change plan counters."""
    from types import SimpleNamespace

    def value(name: str):
        if isinstance(link, dict):
            return link.get(name)
        return getattr(link, name, None)

    payload = {
        "id": _new_id(),
        "occurred_at": utcnow(),
        "event_name": "link_clicked",
        "entity_type": "creator_product_link",
        "entity_id": str(value("id")),
        "shop_id": value("shop_id"),
        "actor": "api",
        "request_id": db.current_request_id(),
        "properties": {
            "discount_code": value("discount_code"),
            "creator_id": value("creator_id"),
            "product_id": value("product_id"),
        },
    }
    if not db.configured():
        event = SimpleNamespace(**payload)
        db.memory_events.append(event)
        return event
    with db.session_scope() as session:
        event = Event(**payload)
        session.add(event)
        session.flush()
        return event


def emit(name: str, entity: Any, actor: str, **props: Any) -> Any:
    """Insert an event and update counters and milestones for that event name."""
    if actor not in ACTORS:
        raise ValueError("actor must be 'system', 'operator', or 'api'")

    entity_type, entity_id, shop = _resolve(entity)
    session = None if getattr(entity, "memory_kind", None) else object_session(entity)
    if shop is not None and name in USAGE_COUNTERS:
        counter = USAGE_COUNTERS[name]
        setattr(shop, counter, int(getattr(shop, counter) or 0) + 1)
    _apply_milestones(name, entity, shop, props)

    properties = dict(props)
    metrics: dict[str, Any] = {}
    if shop is not None:
        properties["plan_tier"] = shop.plan_tier
        properties["plan_status"] = shop.plan_status
        metrics = _shop_metrics(shop, session, name)

    payload = {
        "id": _new_id(),
        "occurred_at": utcnow(),
        "event_name": name,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "shop_id": getattr(shop, "id", None) if shop is not None else None,
        "actor": actor,
        "request_id": db.current_request_id(),
        "properties": properties,
        **metrics,
    }

    if getattr(entity, "memory_kind", None):
        from types import SimpleNamespace

        event = SimpleNamespace(**payload)
        db.memory_events.append(event)
        return event

    if getattr(entity, "emit_detached", False):
        with db.session_scope() as session:
            event = Event(**payload)
            session.add(event)
            session.flush()
            return event

    if session is None:
        raise RuntimeError("emit() requires a persistent entity")
    event = Event(**payload)
    session.add(event)
    session.flush()
    return event


def guard_plan_limit(shop: Any, kind: str, session=None) -> None:
    """Emit usage_limit_hit when `shop` is already at the plan cap.

    `products` compares the shop's product-row count with the catalogue cap.
    `matches_per_month` compares match_run events in the current UTC month.
    Pilot shops emit the event and are allowed through. Other shops are blocked.
    The usage_limit_hit row is committed before the exception so a later
    rollback of the blocked request does not drop it.
    """
    caps = PLAN_CAPS.get(getattr(shop, "plan_tier", None) or "starter", PLAN_CAPS["starter"])
    cap = caps.get(kind)
    if cap is None:
        return
    observed = _observed(shop, kind, session)
    if observed < cap:
        return
    emit(
        "usage_limit_hit",
        shop,
        "api",
        limit=kind,
        cap=cap,
        observed=observed,
    )
    if session is not None:
        session.commit()
    if not bool(getattr(shop, "is_pilot", False)):
        raise PlanLimitReached(kind, observed, cap)


def catalogue_count(shop: Any, session=None) -> int:
    """Number of product rows for the shop. This is the Starter/Growth product cap."""
    if session is None:
        return sum(1 for product in db.memory_products.values() if product.shop_id == shop.id)
    return int(
        session.scalar(select(func.count()).select_from(Product).where(Product.shop_id == shop.id)) or 0
    )


def matches_this_month(shop: Any, session=None) -> int:
    return _count_named(shop, "match_run", session)


def _observed(shop: Any, kind: str, session) -> int:
    if kind == "products":
        return catalogue_count(shop, session)
    if kind == "matches_per_month":
        return matches_this_month(shop, session)
    if kind == "campaigns":
        return campaign_count(shop, session)
    raise ValueError(f"Unknown plan limit '{kind}'")


def campaign_count(shop: Any, session=None) -> int:
    if session is None:
        return sum(1 for row in db.memory_campaigns if getattr(row, "shop_id", None) == shop.id)
    return int(session.scalar(select(func.count()).select_from(Campaign).where(Campaign.shop_id == shop.id)) or 0)


def _shop_metrics(shop: Any, session, event_name: str) -> dict:
    caps = PLAN_CAPS.get(getattr(shop, "plan_tier", None) or "starter", PLAN_CAPS["starter"])
    products_count = catalogue_count(shop, session)
    matches_count = matches_this_month(shop, session)
    enrichments = _count_named(shop, "product_enriched", session)
    briefs = _count_named(shop, "brief_sent", session)
    if event_name == "match_run":
        matches_count += 1
    elif event_name == "product_enriched":
        enrichments += 1
    elif event_name == "brief_sent":
        briefs += 1
    product_limit = caps.get("products")
    match_limit = caps.get("matches_per_month")
    at_products = product_limit is not None and products_count >= product_limit
    at_matches = match_limit is not None and matches_count >= match_limit
    return {
        "products_count": products_count,
        "product_limit": product_limit,
        "matches_count_period": matches_count,
        "match_limit": match_limit,
        "enrichments_count_period": enrichments,
        "briefs_count_period": briefs,
        "billing_period_start": _month_start(),
        "limit_hit": event_name == "usage_limit_hit" or at_products or at_matches,
    }


def _count_named(shop: Any, event_name: str, session) -> int:
    start = _month_start()
    if session is None:
        return sum(
            1
            for event in db.memory_events
            if event.event_name == event_name
            and event.shop_id == shop.id
            and _as_utc(event.occurred_at) >= start
        )
    stamps = session.scalars(
        select(Event.occurred_at).where(
            Event.shop_id == shop.id,
            Event.event_name == event_name,
        )
    ).all()
    return sum(1 for stamp in stamps if _as_utc(stamp) >= start)


def _apply_milestones(name: str, entity: Any, shop: Any, props: dict) -> None:
    for column, mode in SHOP_MILESTONES.get(name, ()):
        _stamp(shop, column, mode)
    creator = entity if _is(entity, Creator, "creator") else None
    if creator is None and name in {"match_accepted", "match_declined", "creator_posted"}:
        creator = _creator_of(entity)
    for column, mode in CREATOR_MILESTONES.get(name, ()):
        if column == "profile_completed_at" and not profile_is_complete(creator):
            continue
        _stamp(creator, column, mode)
    if _is(entity, ShortlistItem, "shortlist_item"):
        if name == "match_accepted":
            _stamp(entity, "accepted_at", "first")
        if name == "match_declined":
            _stamp(entity, "declined_at", "first")
            reason = props.get("decline_reason")
            if reason and not getattr(entity, "decline_reason", None):
                entity.decline_reason = reason
    if name == "brief_sent" and _is(entity, Brief, "brief"):
        _stamp(entity, "sent_at", "first")


def profile_is_complete(entity: Any) -> bool:
    """A creator profile is complete when matching does not have to guess the basics.

    Required: a handle, at least one active category, at least one preferred
    promotion style, and at least one platform. Desired categories, follower
    band, and performance metrics are optional.
    """
    if entity is None:
        return False
    profile = getattr(entity, "profile", None)
    profile = profile if isinstance(profile, dict) else {}

    def values(attr: str, key: str) -> list:
        raw = getattr(entity, attr, None)
        if raw:
            return list(raw)
        return list(profile.get(key) or [])

    handle = str(getattr(entity, "handle", None) or profile.get("handle") or "").strip()
    return bool(
        handle
        and values("active_categories", "active_categories")
        and values("preferred_promotion_styles", "preferred_promotion_styles")
        and values("platforms", "platforms")
    )


def _stamp(obj: Any, column: str, mode: str) -> None:
    if obj is None or not hasattr(obj, column):
        return
    if mode == "first" and getattr(obj, column) is not None:
        return
    setattr(obj, column, utcnow())


def _resolve(entity: Any):
    kind = getattr(entity, "memory_kind", None)
    if kind == "tracking" or getattr(entity, "emit_detached", False):
        return getattr(entity, "entity_type", "tracking"), entity.id, None
    if kind == "shop" or isinstance(entity, Shop):
        return "shop", entity.id, entity
    if kind == "product" or isinstance(entity, Product):
        return "product", entity.id, _shop_of(entity)
    if kind == "creator" or isinstance(entity, Creator):
        return "creator", entity.id, None
    if kind == "shortlist" or isinstance(entity, Shortlist):
        return "shortlist", entity.id, _shop_of(entity)
    if kind == "shortlist_item" or isinstance(entity, ShortlistItem):
        return "shortlist_item", entity.id, _shop_of_item(entity)
    if kind == "brief" or isinstance(entity, Brief):
        shop = _shop_of(entity)
        if shop is None and getattr(entity, "shop_id", None):
            shop = _shop_by_id(entity, entity.shop_id)
        if shop is None:
            product = getattr(entity, "product", None)
            if product is None and getattr(entity, "product_id", None):
                if kind == "brief":
                    product = db.memory_products.get(entity.product_id)
                else:
                    session = object_session(entity)
                    product = session.get(Product, entity.product_id) if session is not None else None
            shop = _shop_of(product) if product is not None else None
        return "brief", entity.id, shop
    raise TypeError("emit() entity must be a shop, product, creator, shortlist, shortlist item, or brief")


def _shop_of(entity: Any):
    if entity is None:
        return None
    shop = getattr(entity, "shop", None)
    if shop is not None:
        return shop
    shop_id = getattr(entity, "shop_id", None)
    if not shop_id:
        return None
    return _shop_by_id(entity, shop_id)


def _shop_by_id(entity: Any, shop_id: str):
    if getattr(entity, "memory_kind", None):
        return db.memory_shops.get(shop_id)
    session = object_session(entity)
    if session is None:
        return None
    return session.get(Shop, shop_id)


def _shop_of_item(entity: Any):
    shortlist = getattr(entity, "shortlist", None)
    if shortlist is None and getattr(entity, "shortlist_id", None):
        if getattr(entity, "memory_kind", None):
            shortlist = next((row for row in db.memory_shortlists if row.id == entity.shortlist_id), None)
        else:
            session = object_session(entity)
            shortlist = session.get(Shortlist, entity.shortlist_id) if session is not None else None
    return _shop_of(shortlist)


def _creator_of(entity: Any):
    creator = getattr(entity, "creator", None)
    if creator is not None:
        return creator
    creator_id = getattr(entity, "creator_id", None)
    if not creator_id or getattr(entity, "memory_kind", None):
        return None
    session = object_session(entity)
    if session is None:
        return None
    return session.get(Creator, creator_id)


def _is(entity: Any, model: type, memory_kind: str) -> bool:
    return getattr(entity, "memory_kind", None) == memory_kind or isinstance(entity, model)


def _month_start(now: Optional[datetime] = None) -> datetime:
    current = _as_utc(now or utcnow())
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())
