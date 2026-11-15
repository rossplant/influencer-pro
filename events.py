"""Append-only events and plan-limit checks.

events.emit(name, entity, actor, **props) inserts the event and, for usage
events, updates the shop's counters and first-* milestone timestamps. Shop
events copy plan_tier, plan_status, and the usage counters into properties
after those updates.

Hand-run operator actions use the same event names with actor='operator'.
The helper records whatever it is asked to record. Plan limits are enforced
by guard_plan_limit, which the API calls before a usage event. A shop already
at its cap emits usage_limit_hit. Pilot shops (is_pilot) are then allowed
through. Other shops are blocked.

Starter limits follow docs/PlantR_Pricing.md: 50 product enrichments and 5
match runs per calendar month (UTC). Growth allows 500 product enrichments.
Scale has no product cap. Free uses the starter caps. MRR for starter, free,
and trial is 0; an annual price counts as price / 12.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import object_session

import db
from models import Brief, Creator, Event, Product, Shop, Shortlist, utcnow

ACTORS = frozenset({"system", "operator", "api"})

# event name -> (counter column, milestone column) on shops
USAGE_EVENTS = {
    "product_enriched": ("products_enriched", "first_enrich_at"),
    "match_run": ("matches_run", "first_match_at"),
    "brief_generated": ("briefs_generated", "first_brief_at"),
}

# None means the plan does not cap that dimension.
PLAN_CAPS = {
    "starter": {"products": 50, "matches_per_month": 5},
    "free": {"products": 50, "matches_per_month": 5},
    "growth": {"products": 500, "matches_per_month": None},
    "scale": {"products": None, "matches_per_month": None},
}

PLAN_MONTHLY_PRICE = {
    "starter": 0,
    "free": 0,
    "growth": 49,
    "scale": 149,
}

KNOWN_PLAN_TIERS = frozenset(PLAN_CAPS)


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

    A monthly price is the MRR. An annual price is divided by 12.
    Free, trial, and starter contribute 0.
    """
    if (status or "").lower() in {"free", "trial"}:
        return 0.0
    if plan_tier in {"starter", "free", "trial"}:
        return 0.0
    amount = PLAN_MONTHLY_PRICE.get(plan_tier, 0) if price is None else price
    if (interval or "month").lower() == "year":
        return float(amount) / 12.0
    return float(amount)


def emit(name: str, entity: Any, actor: str, **props: Any) -> Any:
    """Insert an event and update shop usage counters and milestones.

    `actor` is 'system', 'operator', or 'api'. Shop events always include
    plan_tier, plan_status, products_enriched, matches_run, and briefs_generated
    in properties, taken after the counter update.
    """
    if actor not in ACTORS:
        raise ValueError("actor must be 'system', 'operator', or 'api'")

    entity_type, entity_id, shop = _resolve(entity)
    if shop is not None and name in USAGE_EVENTS:
        counter, milestone = USAGE_EVENTS[name]
        setattr(shop, counter, int(getattr(shop, counter) or 0) + 1)
        if getattr(shop, milestone) is None:
            setattr(shop, milestone, utcnow())

    properties = dict(props)
    if shop is not None:
        properties["plan_tier"] = shop.plan_tier
        properties["plan_status"] = shop.plan_status
        properties["products_enriched"] = int(shop.products_enriched or 0)
        properties["matches_run"] = int(shop.matches_run or 0)
        properties["briefs_generated"] = int(shop.briefs_generated or 0)

    payload = {
        "id": _new_id(),
        "occurred_at": utcnow(),
        "event_name": name,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "shop_id": getattr(shop, "id", None) if shop is not None else None,
        "actor": actor,
        "properties": properties,
    }

    if getattr(entity, "memory_kind", None):
        from types import SimpleNamespace

        event = SimpleNamespace(**payload)
        db.memory_events.append(event)
        return event

    session = object_session(entity)
    if session is None:
        raise RuntimeError("emit() requires a persistent entity")
    event = Event(**payload)
    session.add(event)
    session.flush()
    return event


def guard_plan_limit(shop: Any, kind: str, session=None) -> None:
    """Emit usage_limit_hit when `shop` is already at the plan cap.

    Returns normally when the action may proceed, including when a pilot shop
    is over the cap. Raises PlanLimitReached for every other shop at the cap.
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


def matches_this_month(shop: Any, session=None) -> int:
    start = _month_start()
    if session is None:
        return sum(
            1
            for event in db.memory_events
            if event.event_name == "match_run"
            and event.shop_id == shop.id
            and _as_utc(event.occurred_at) >= start
        )
    stamps = session.scalars(
        select(Event.occurred_at).where(
            Event.shop_id == shop.id,
            Event.event_name == "match_run",
        )
    ).all()
    return sum(1 for stamp in stamps if _as_utc(stamp) >= start)


def _observed(shop: Any, kind: str, session) -> int:
    if kind == "products":
        return int(shop.products_enriched or 0)
    if kind == "matches_per_month":
        return matches_this_month(shop, session)
    raise ValueError(f"Unknown plan limit '{kind}'")


def _resolve(entity: Any):
    kind = getattr(entity, "memory_kind", None)
    if kind == "shop" or isinstance(entity, Shop):
        return "shop", entity.id, entity
    if kind == "product" or isinstance(entity, Product):
        return "product", entity.id, _shop_of(entity)
    if kind == "creator" or isinstance(entity, Creator):
        return "creator", entity.id, None
    if kind == "shortlist" or isinstance(entity, Shortlist):
        return "shortlist", entity.id, _shop_of(entity)
    if kind == "brief" or isinstance(entity, Brief):
        product = getattr(entity, "product", None)
        if product is None and getattr(entity, "product_id", None):
            if kind == "brief":
                product = db.memory_products.get(entity.product_id)
            else:
                session = object_session(entity)
                product = session.get(Product, entity.product_id) if session is not None else None
        return "brief", entity.id, _shop_of(product) if product is not None else None
    raise TypeError("emit() entity must be a shop, product, creator, shortlist, or brief")


def _shop_of(entity: Any):
    if entity is None:
        return None
    shop = getattr(entity, "shop", None)
    if shop is not None:
        return shop
    shop_id = getattr(entity, "shop_id", None)
    if not shop_id:
        return None
    if getattr(entity, "memory_kind", None):
        return db.memory_shops.get(shop_id)
    session = object_session(entity)
    if session is None:
        return None
    return session.get(Shop, shop_id)


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
