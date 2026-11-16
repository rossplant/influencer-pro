"""Pilot tracking links and CSV attribution.

One creator and one product share one Plantr Collab code. Importing the same
shop and order id again does not create a second order.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

import db
import events
from models import (
    AttributedOrder,
    Brief,
    Creator,
    CreatorPlatformCode,
    CreatorProductLink,
    Event,
    Product,
    Shop,
    Shortlist,
    ShortlistItem,
    utcnow,
)
from services import _currency_code

logger = logging.getLogger("plantr.attribution")

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SOURCE_PLATFORMS = ("tiktok", "instagram", "youtube", "facebook", "other", "unknown")
TAGGED_PLATFORMS = ("tiktok", "instagram", "youtube", "facebook", "other")
SOURCE_PARAMS = (
    ("tt", "tiktok", "TikTok"),
    ("ig", "instagram", "Instagram"),
    ("yt", "youtube", "YouTube"),
    ("fb", "facebook", "Facebook"),
    ("ot", "other", "Other"),
)
SOURCE_PARAM_TO_PLATFORM = {param: platform for param, platform, _label in SOURCE_PARAMS}
CODE_PLATFORM_UNKNOWN = "code, platform not known"
PLATFORM_CODE_RE = re.compile(r"[A-Z0-9_\-]{3,64}")
HEADER_ALIASES = {
    "order_id": "order_id",
    "order": "order_id",
    "external_order_id": "order_id",
    "id": "order_id",
    "date": "date",
    "attribution_date": "date",
    "order_date": "date",
    "code": "code",
    "link": "code",
    "discount_code": "code",
    "tracking_link": "code",
    "referral_code": "code",
    "gross": "gross",
    "gross_amount": "gross",
    "gmv_gross": "gross",
    "amount": "gross",
    "currency": "currency",
    "shop": "shop",
    "shop_id": "shop",
    "shop_name": "shop",
}


def normalise_source_param(raw: Optional[str]) -> str:
    return SOURCE_PARAM_TO_PLATFORM.get((raw or "").strip().lower(), "unknown")


def channel_from_query(source_param: Optional[str], channel_param: Optional[str]) -> tuple[str, str]:
    """s=tt|ig|yt|fb|ot and c=ad. An ad click is always TikTok."""
    if (channel_param or "").strip().lower() == "ad":
        return "tiktok", "ad"
    return normalise_source_param(source_param), "link"


def direct_url(link_url: Optional[str], code: str, *, source_param: Optional[str] = None, channel: Optional[str] = None) -> Optional[str]:
    """Seller product URL with plantr=<code>, plus optional s and c. Skips Plantr's redirect."""
    base = safe_destination(link_url)
    if not base or not code:
        return None
    parts = urlparse(base)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key not in {"plantr", "s", "c"}]
    query.append(("plantr", code))
    if source_param:
        query.append(("s", source_param))
    if channel:
        query.append(("c", channel))
    return urlunparse(parts._replace(query=urlencode(query)))


def platform_variants(code: str, link_url: Optional[str] = None) -> list[dict]:
    """One Plantr link and one direct seller URL per source platform. The untagged link stays separate."""
    return [
        {
            "param": param,
            "source_platform": platform,
            "label": label,
            "tracking_path": f"/r/{code}?s={param}",
            "direct_url": direct_url(link_url, code, source_param=param),
        }
        for param, platform, label in SOURCE_PARAMS
    ]


def join_note(*parts: Optional[str]) -> Optional[str]:
    text = "; ".join(part for part in parts if part)
    return text[:255] or None


def parse_direct_signal(order: dict) -> Optional[dict]:
    """Read plantr, s and c from landing_site, then referring_site. The URL is not returned or stored."""
    for field in ("landing_site", "referring_site"):
        params = _query_params(str(order.get(field) or ""))
        code = (params.get("plantr") or "").strip().upper()
        if not code:
            continue
        return {"code": code, "s": params.get("s"), "c": params.get("c")}
    return None


def _query_params(value: str) -> dict:
    text = value.strip()
    if not text:
        return {}
    if "://" not in text:
        text = "https://x" + (text if text.startswith("/") else "/" + text)
    try:
        parsed = urlparse(text)
    except ValueError:
        return {}
    return {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}


def classify_sale(
    *,
    method: Optional[str],
    link: Optional[dict],
    order_date: date,
    code_platform: Optional[str] = None,
    direct: Optional[dict] = None,
) -> dict:
    """source_platform and channel_kind for reporting. Does not decide whether the sale is credited."""
    if not method:
        return {"source_platform": "unknown", "channel_kind": None, "platform_note": None}
    tagged = code_platform if code_platform in TAGGED_PLATFORMS else None
    if method == "code" or (method == "link+code" and tagged):
        if tagged:
            return {"source_platform": tagged, "channel_kind": "code", "platform_note": None}
        return {"source_platform": "unknown", "channel_kind": "code", "platform_note": CODE_PLATFORM_UNKNOWN}
    if direct:
        source, channel = channel_from_query(direct.get("s"), direct.get("c"))
        return {"source_platform": source, "channel_kind": channel, "platform_note": None}
    click = winning_click(link or {}, order_date) if link else None
    if click:
        if click.get("channel_kind") == "ad":
            return {"source_platform": "tiktok", "channel_kind": "ad", "platform_note": None}
        source = click.get("source_platform")
        if source not in SOURCE_PLATFORMS:
            source = "unknown"
        return {"source_platform": source, "channel_kind": "link", "platform_note": None}
    if method == "link+code":
        return {"source_platform": "unknown", "channel_kind": "code", "platform_note": CODE_PLATFORM_UNKNOWN}
    return {"source_platform": "unknown", "channel_kind": "link", "platform_note": None}


def winning_click(link: dict, order_date: date) -> Optional[dict]:
    """The latest click on this link in the 7 days up to and including the order date."""
    start = order_date - timedelta(days=7)
    code = str(link.get("discount_code") or "")
    link_id = str(link.get("id") or "")
    best = None
    best_key = None
    for click in _link_clicks():
        props = click["properties"]
        if str(props.get("discount_code") or "") != code and str(click["entity_id"]) != link_id:
            continue
        occurred = click["occurred_at"]
        click_day = occurred.date() if hasattr(occurred, "date") else occurred
        if not (start <= click_day <= order_date):
            continue
        key = (occurred, str(click["id"]))
        if best_key is None or key >= best_key:
            best = props
            best_key = key
    return best


def match_shop_code(session, shop_id: str, code: str) -> tuple[Optional[CreatorProductLink], Optional[str]]:
    """A shop code, and the source platform when it is an extra per-platform code."""
    clean = (code or "").strip().upper()
    if not clean:
        return None, None
    tagged = session.scalar(
        select(CreatorPlatformCode).where(CreatorPlatformCode.shop_id == shop_id, CreatorPlatformCode.code == clean)
    )
    if tagged is not None:
        link = session.get(CreatorProductLink, tagged.link_id)
        if link is not None and link.shop_id == shop_id:
            return link, tagged.source_platform
    link = session.scalar(
        select(CreatorProductLink).where(
            CreatorProductLink.shop_id == shop_id,
            or_(func.upper(CreatorProductLink.discount_code) == clean, func.upper(CreatorProductLink.store_discount_code) == clean),
        )
    )
    return link, None


def register_platform_code(shop_id: str, link_id: str, code: Optional[str], source_platform: Optional[str], *, actor: str) -> dict:
    """Seller registers an extra code for the same creator, tagged to one platform."""
    if not db.configured():
        raise HTTPException(status_code=503, detail="Platform codes need the database")
    platform = (source_platform or "").strip().lower()
    if platform not in TAGGED_PLATFORMS:
        raise HTTPException(status_code=422, detail="source_platform must be tiktok, instagram, youtube, facebook, or other")
    clean = (code or "").strip().upper()
    if not PLATFORM_CODE_RE.fullmatch(clean):
        raise HTTPException(status_code=422, detail="Codes use letters, numbers, - and _ only")
    with db.session_scope() as session:
        link = session.get(CreatorProductLink, link_id)
        if link is None or link.shop_id != shop_id:
            raise HTTPException(status_code=404, detail="Link not found")
        if clean in {(link.discount_code or "").upper(), (link.store_discount_code or "").upper()}:
            raise HTTPException(status_code=409, detail="That code is already this creator's tracking or store code")
        clash = session.scalar(
            select(CreatorProductLink.id).where(
                CreatorProductLink.shop_id == shop_id,
                CreatorProductLink.id != link.id,
                or_(func.upper(CreatorProductLink.discount_code) == clean, func.upper(CreatorProductLink.store_discount_code) == clean),
            )
        )
        if clash is None:
            clash = session.scalar(
                select(CreatorPlatformCode.id).where(CreatorPlatformCode.shop_id == shop_id, CreatorPlatformCode.code == clean)
            )
        if clash is not None:
            raise HTTPException(status_code=409, detail="That code is already linked to another creator")
        row = CreatorPlatformCode(
            id=str(uuid.uuid4()),
            shop_id=shop_id,
            creator_id=link.creator_id,
            link_id=link.id,
            code=clean,
            source_platform=platform,
            created_at=utcnow(),
        )
        session.add(row)
        session.flush()
        logger.info("Platform code set for link %s by %s on %s", link.id, actor, platform)
        return {"id": row.id, "link_id": link.id, "creator_id": link.creator_id, "code": row.code, "source_platform": row.source_platform}


def delete_platform_code(shop_id: str, link_id: str, code_id: str) -> dict:
    if not db.configured():
        raise HTTPException(status_code=503, detail="Platform codes need the database")
    with db.session_scope() as session:
        row = session.get(CreatorPlatformCode, code_id)
        if row is None or row.shop_id != shop_id or row.link_id != link_id:
            raise HTTPException(status_code=404, detail="Code not found")
        session.delete(row)
        return {"deleted": True, "id": code_id}


def _group_codes(session, link_ids: list[str]) -> dict[str, list[dict]]:
    if not link_ids:
        return {}
    grouped: dict[str, list[dict]] = {}
    rows = session.scalars(select(CreatorPlatformCode).where(CreatorPlatformCode.link_id.in_(link_ids))).all()
    for row in rows:
        grouped.setdefault(row.link_id, []).append(
            {"id": row.id, "code": row.code, "source_platform": row.source_platform}
        )
    return grouped


def issue_link(
    *,
    creator_id: str,
    product_id: str,
    destination_url: Optional[str],
    shortlist_item_id: Optional[str],
    platform: str = "plantr",
) -> tuple[dict, bool]:
    creator_id = creator_id.strip()
    product_id = product_id.strip()
    if db.configured():
        return _issue_db(creator_id, product_id, destination_url, shortlist_item_id, platform)
    return _issue_memory(creator_id, product_id, destination_url, shortlist_item_id, platform)


def ensure_link_for_acceptance(
    *,
    creator_id: Optional[str],
    product_id: Optional[str],
    shortlist_item_id: Optional[str],
) -> bool:
    """Issue the tracking link for an accepted item. Missing data is skipped.

    Returns True only when a new link row is created. Repeating the call keeps
    the same code and fills shortlist_item_id when it was empty.
    """
    if not creator_id or not product_id or not shortlist_item_id:
        return False
    destination = product_destination(product_id)
    if destination is None:
        logger.warning(
            "Skipped tracking link for shortlist item %s because product %s has no http(s) URL",
            shortlist_item_id,
            product_id,
        )
        return False
    try:
        _row, created = issue_link(
            creator_id=str(creator_id),
            product_id=str(product_id),
            destination_url=destination,
            shortlist_item_id=str(shortlist_item_id),
        )
    except HTTPException as exc:
        logger.warning(
            "Skipped tracking link for shortlist item %s: %s",
            shortlist_item_id,
            exc.detail,
        )
        return False
    return created


def backfill_accepted_links() -> int:
    """Create missing links for shortlist items that are already accepted."""
    created = 0
    for creator_id, product_id, item_id in _accepted_pairs():
        if ensure_link_for_acceptance(
            creator_id=creator_id,
            product_id=product_id,
            shortlist_item_id=item_id,
        ):
            created += 1
    logger.info("Accepted-link backfill created %s tracking link(s)", created)
    return created


def product_destination(product_id: str) -> Optional[str]:
    import catalogue

    if db.configured():
        with db.session_scope() as session:
            product = session.get(Product, product_id)
    else:
        product = db.memory_products.get(product_id)
    return safe_destination(catalogue.product_public_url(product))


def list_links(*, shop_id: Optional[str], creator_id: Optional[str]) -> list[dict]:
    shop_id = (shop_id or "").strip() or None
    creator_id = (creator_id or "").strip() or None
    if db.configured():
        with db.session_scope() as session:
            query = select(CreatorProductLink).order_by(CreatorProductLink.created_at.desc())
            if shop_id:
                query = query.where(CreatorProductLink.shop_id == shop_id)
            if creator_id:
                query = query.where(CreatorProductLink.creator_id == creator_id)
            rows = session.scalars(query).all()
            grouped = _group_codes(session, [row.id for row in rows])
            return [_link_dict(row, grouped.get(row.id, [])) for row in rows]
    rows = list(db.memory_creator_product_links)
    if shop_id:
        rows = [row for row in rows if row.shop_id == shop_id]
    if creator_id:
        rows = [row for row in rows if row.creator_id == creator_id]
    rows.sort(key=lambda row: str(row.created_at), reverse=True)
    return [_link_dict(row) for row in rows]


def link_detail(link_id: str) -> Optional[dict]:
    return next((row for row in list_links(shop_id=None, creator_id=None) if row["id"] == link_id), None)


def safe_destination(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return str(url).strip()


def resolve_redirect(code: str):
    code = code.strip()
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(CreatorProductLink).where(CreatorProductLink.discount_code == code))
            if row is None:
                return None
            return _link_dict(row)
    return next(
        (_link_dict(row) for row in db.memory_creator_product_links if row.discount_code == code),
        None,
    )


def import_orders(csv_text: str, *, dry_run: bool) -> dict:
    rows = _parse_csv(csv_text)
    if not rows:
        raise HTTPException(status_code=422, detail="CSV has no data rows")
    results = []
    created = existing = errors = 0
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(rows, start=1):
        outcome = _import_row(raw, dry_run=dry_run, seen=seen)
        results.append({"row": index, **outcome})
        if outcome["status"] == "created":
            created += 1
        elif outcome["status"] == "existing":
            existing += 1
        else:
            errors += 1
    return {"dry_run": dry_run, "created": created, "existing": existing, "errors": errors, "rows": results}


def list_orders(*, shop_id: Optional[str] = None, creator_id: Optional[str] = None) -> list[dict]:
    """Orders that count as attributed. Unattributed rows stay in the table and are omitted here."""
    return [_order_dict(row) for row in _order_rows(shop_id=shop_id, creator_id=creator_id, attributed_only=True)]


def attributed_gmv(*, shop_id: Optional[str] = None, creator_id: Optional[str] = None) -> dict:
    """Gross and net GMV from attributed orders only. Raw gross on excluded rows is not added."""
    rows = _order_rows(shop_id=shop_id, creator_id=creator_id, attributed_only=True)
    return {
        "orders": len(rows),
        "gmv_gross": round(sum(float(row.gmv_gross or 0) for row in rows), 2),
        "net_gmv": round(sum(float(row.net_gmv or 0) for row in rows), 2),
    }


def _order_rows(*, shop_id: Optional[str] = None, creator_id: Optional[str] = None, attributed_only: bool = True) -> list:
    shop_id = (shop_id or "").strip() or None if isinstance(shop_id, str) else shop_id
    creator_id = (creator_id or "").strip() or None if isinstance(creator_id, str) else creator_id
    if db.configured():
        with db.session_scope() as session:
            query = select(AttributedOrder).order_by(AttributedOrder.created_at.desc())
            if shop_id:
                query = query.where(AttributedOrder.shop_id == shop_id)
            if creator_id:
                query = query.where(AttributedOrder.creator_id == creator_id)
            if attributed_only:
                query = query.where(AttributedOrder.attributed.is_(True))
            return list(session.scalars(query).all())
    rows = list(db.memory_attributed_orders)
    if shop_id:
        rows = [row for row in rows if row.shop_id == shop_id]
    if creator_id:
        rows = [row for row in rows if row.creator_id == creator_id]
    if attributed_only:
        rows = [row for row in rows if getattr(row, "attributed", True)]
    rows.sort(key=lambda row: str(row.created_at), reverse=True)
    return rows


def order_detail(order_id: str) -> Optional[dict]:
    return next((row for row in list_orders() if row["id"] == order_id), None)


def _issue_db(creator_id, product_id, destination_url, shortlist_item_id, platform) -> tuple[dict, bool]:
    with db.session_scope() as session:
        product = session.get(Product, product_id)
        creator = session.get(Creator, creator_id)
        if product is None or creator is None:
            raise HTTPException(status_code=404, detail="Creator or product not found")
        existing = session.scalar(
            select(CreatorProductLink).where(
                CreatorProductLink.product_id == product.id,
                CreatorProductLink.creator_id == creator.id,
            )
        )
        if existing is not None:
            if shortlist_item_id and not existing.shortlist_item_id:
                existing.shortlist_item_id = shortlist_item_id
                session.flush()
            return _link_dict(existing, _group_codes(session, [existing.id]).get(existing.id, [])), False
        item_id = _checked_item(session, shortlist_item_id, product.id, creator.id)
        destination = _destination(destination_url, product.attributes)
        link = CreatorProductLink(
            id=str(uuid.uuid4()),
            shop_id=product.shop_id,
            product_id=product.id,
            creator_id=creator.id,
            shortlist_item_id=item_id,
            link_url=destination,
            discount_code=_fresh_code(session),
            platform=(platform or "plantr")[:32] or "plantr",
            status="active",
            created_at=utcnow(),
        )
        session.add(link)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(CreatorProductLink).where(
                    CreatorProductLink.product_id == product.id,
                    CreatorProductLink.creator_id == creator.id,
                )
            )
            if existing is None:
                raise
            return _link_dict(existing, _group_codes(session, [existing.id]).get(existing.id, [])), False
        return _link_dict(link, []), True


def _issue_memory(creator_id, product_id, destination_url, shortlist_item_id, platform) -> tuple[dict, bool]:
    from types import SimpleNamespace

    product = db.memory_products.get(product_id)
    creator = db.memory_creators.get(creator_id)
    if product is None or creator is None:
        raise HTTPException(status_code=404, detail="Creator or product not found")
    existing = next(
        (row for row in db.memory_creator_product_links if row.product_id == product_id and row.creator_id == creator_id),
        None,
    )
    if existing is not None:
        if shortlist_item_id and not existing.shortlist_item_id:
            existing.shortlist_item_id = shortlist_item_id
        return _link_dict(existing), False
    item_id = _checked_item_memory(shortlist_item_id, product_id, creator_id)
    destination = _destination(destination_url, getattr(product, "attributes", None))
    link = SimpleNamespace(
        id=str(uuid.uuid4()),
        shop_id=product.shop_id,
        product_id=product_id,
        creator_id=creator_id,
        shortlist_item_id=item_id,
        link_url=destination,
        discount_code=_fresh_code(None),
        platform=(platform or "plantr")[:32] or "plantr",
        status="active",
        created_at=utcnow(),
    )
    db.memory_creator_product_links.append(link)
    return _link_dict(link), True


def _accepted_pairs() -> list[tuple]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.execute(
                select(ShortlistItem.creator_id, Shortlist.product_id, ShortlistItem.id)
                .join(Shortlist, Shortlist.id == ShortlistItem.shortlist_id)
                .where(ShortlistItem.accepted_at.is_not(None))
            ).all()
            return [(creator_id, product_id, item_id) for creator_id, product_id, item_id in rows]
    pairs = []
    for item in db.memory_shortlist_items:
        if not getattr(item, "accepted_at", None):
            continue
        shortlist = getattr(item, "shortlist", None)
        if shortlist is None:
            shortlist = next((row for row in db.memory_shortlists if row.id == item.shortlist_id), None)
        pairs.append((getattr(item, "creator_id", None), getattr(shortlist, "product_id", None), item.id))
    return pairs


def _import_row(raw: dict, *, dry_run: bool, seen: set[tuple[str, str]]) -> dict:
    try:
        parsed = _validated_row(raw)
    except HTTPException as exc:
        return {"status": "error", "error": str(exc.detail)}
    key = (parsed["shop_id"], parsed["order_id"])
    if key in seen:
        return {"status": "existing", "external_order_id": parsed["order_id"], "shop_id": parsed["shop_id"]}
    current = _find_order(parsed["shop_id"], parsed["order_id"])
    if current is not None:
        seen.add(key)
        return {"status": "existing", "external_order_id": parsed["order_id"], "shop_id": parsed["shop_id"], "id": current["id"]}
    seen.add(key)
    if dry_run:
        return {"status": "created", "external_order_id": parsed["order_id"], "shop_id": parsed["shop_id"], "dry_run": True}
    stored = _write_order(parsed)
    return {"status": "created", "external_order_id": parsed["order_id"], "shop_id": parsed["shop_id"], "id": stored["id"]}


def _validated_row(raw: dict) -> dict:
    order_id = str(raw.get("order_id") or "").strip()
    if not order_id:
        raise HTTPException(status_code=422, detail="order id is required")
    code = _code_from_cell(str(raw.get("code") or ""))
    if not code:
        raise HTTPException(status_code=422, detail="code or link is required")
    try:
        gross = float(str(raw.get("gross") or "").strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="gross amount must be a number") from None
    if gross < 0:
        raise HTTPException(status_code=422, detail="gross amount must not be negative")
    currency = _currency_code(str(raw.get("currency") or ""))
    if not currency:
        raise HTTPException(status_code=422, detail="currency is required")
    try:
        attribution_date = date.fromisoformat(str(raw.get("date") or "").strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD") from None
    shop = _find_shop(str(raw.get("shop") or "").strip())
    if shop is None:
        raise HTTPException(status_code=422, detail="shop was not found")
    link = _find_link_by_code(code)
    code_platform = None
    if link is None and db.configured():
        with db.session_scope() as session:
            found, code_platform = match_shop_code(session, shop["id"], code)
            if found is not None:
                link = _link_dict(found)
    if link is None:
        raise HTTPException(status_code=422, detail=f"Unknown code '{code}'")
    if link["shop_id"] != shop["id"]:
        raise HTTPException(status_code=422, detail="code belongs to a different shop")
    window = raw.get("click_window_days")
    if window not in (None, "", "7", 7):
        raise HTTPException(status_code=422, detail="click window must be 7 days")
    source_cell = str(raw.get("code") or raw.get("link") or "")
    click_source = "link" if _cell_is_link(source_cell) else "code"
    attributed = _clicked_within_window(link, attribution_date)
    direct = None
    if click_source == "link" and winning_click(link, attribution_date) is None:
        params = _query_params(source_cell)
        if params.get("plantr") or params.get("s") or params.get("c"):
            direct = {"s": params.get("s"), "c": params.get("c")}
    reporting = classify_sale(
        method="code" if click_source == "code" else "link",
        link=link,
        order_date=attribution_date,
        code_platform=code_platform if click_source == "code" else None,
        direct=direct,
    )
    return {
        "order_id": order_id[:128],
        "date": attribution_date,
        "gross": round(gross, 2),
        "currency": currency,
        "shop_id": shop["id"],
        "country": shop.get("country"),
        "market": shop.get("market"),
        "link": link,
        "click_window_days": 7,
        "click_window_source": click_source,
        "attributed": attributed,
        "source_platform": reporting["source_platform"],
        "channel_kind": reporting["channel_kind"],
        "attribution_note": reporting["platform_note"],
        "raw": {key: value for key, value in raw.items() if key != "password"},
    }


def _write_order(parsed: dict) -> dict:
    link = parsed["link"]
    brief_id = _brief_id(link)
    now = utcnow()
    occurred = datetime(parsed["date"].year, parsed["date"].month, parsed["date"].day, tzinfo=timezone.utc)
    fields = dict(
        id=str(uuid.uuid4()),
        shop_id=parsed["shop_id"],
        platform="csv",
        country=parsed["country"],
        market=parsed["market"],
        external_order_id=parsed["order_id"],
        gmv_gross=parsed["gross"],
        refunded_amount=0,
        cancelled_amount=0,
        net_gmv=round(parsed["gross"] - 0 - 0, 2),
        currency=parsed["currency"],
        creator_id=link.get("creator_id"),
        product_id=link.get("product_id"),
        shortlist_item_id=link.get("shortlist_item_id"),
        brief_id=brief_id,
        attribution_date=parsed["date"],
        click_window_days=parsed["click_window_days"],
        click_window_source=parsed["click_window_source"],
        settlement_status="imported",
        fee_revenue=0,
        ingest_source="csv",
        raw_payload=parsed["raw"],
        attributed=parsed["attributed"],
        attribution_method=parsed["click_window_source"],
        attribution_note=parsed.get("attribution_note"),
        source_platform=parsed.get("source_platform") or "unknown",
        channel_kind=parsed.get("channel_kind"),
        occurred_at=occurred,
        created_at=now,
    )
    if db.configured():
        with db.session_scope() as session:
            existing = session.scalar(
                select(AttributedOrder).where(
                    AttributedOrder.shop_id == parsed["shop_id"],
                    AttributedOrder.external_order_id == parsed["order_id"],
                )
            )
            if existing is not None:
                return _order_dict(existing)
            order = AttributedOrder(**fields)
            session.add(order)
            shop = session.get(Shop, parsed["shop_id"])
            if shop is not None and parsed["attributed"]:
                _emit_sale(shop, parsed, link)
            session.flush()
            return _order_dict(order)
    from types import SimpleNamespace

    order = SimpleNamespace(**fields)
    db.memory_attributed_orders.append(order)
    shop = db.memory_shops.get(parsed["shop_id"])
    if shop is not None and parsed["attributed"]:
        _emit_sale(shop, parsed, link)
    return _order_dict(order)


def _emit_sale(shop, parsed, link) -> None:
    import tracking

    tracking.track(
        "attributed_sale",
        event_id=f"sale:{parsed['shop_id']}:{parsed['order_id']}",
        entity=shop,
        actor="operator",
        source="csv_import",
        order_id=parsed["order_id"],
        gross=parsed["gross"],
        currency=parsed["currency"],
        creator_id=link.get("creator_id"),
        shop_id=parsed["shop_id"],
        refunded=False,
        cancelled=False,
    )


def _clicked_within_window(link: dict, order_date: date) -> bool:
    """True when this link or code was clicked in the 7 days up to and including the order date."""
    return winning_click(link, order_date) is not None


def _link_clicks() -> list:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Event).where(Event.event_name == "link_clicked")).all()
            return [
                {"id": row.id, "entity_id": row.entity_id, "occurred_at": row.occurred_at, "properties": dict(row.properties or {})}
                for row in rows
            ]
    return [
        {
            "id": getattr(row, "id", ""),
            "entity_id": row.entity_id,
            "occurred_at": row.occurred_at,
            "properties": dict(row.properties or {}),
        }
        for row in db.memory_events
        if getattr(row, "event_name", None) == "link_clicked"
    ]


def adjust_order(order_id: str, *, refunded_amount: Optional[float] = None, cancelled_amount: Optional[float] = None) -> dict:
    """Update refund and cancel amounts on the order. Events stay append-only."""
    if db.configured():
        with db.session_scope() as session:
            order = session.get(AttributedOrder, order_id)
            if order is None:
                raise HTTPException(status_code=404, detail="Order not found")
            if refunded_amount is not None:
                order.refunded_amount = refunded_amount
            if cancelled_amount is not None:
                order.cancelled_amount = cancelled_amount
            order.net_gmv = round(float(order.gmv_gross) - float(order.refunded_amount) - float(order.cancelled_amount), 2)
            session.flush()
            return _order_dict(order)
    order = next((row for row in db.memory_attributed_orders if row.id == order_id), None)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if refunded_amount is not None:
        order.refunded_amount = refunded_amount
    if cancelled_amount is not None:
        order.cancelled_amount = cancelled_amount
    order.net_gmv = round(float(order.gmv_gross) - float(order.refunded_amount) - float(order.cancelled_amount), 2)
    return _order_dict(order)


def _find_order(shop_id: str, order_id: str) -> Optional[dict]:
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(
                select(AttributedOrder).where(
                    AttributedOrder.shop_id == shop_id,
                    AttributedOrder.external_order_id == order_id,
                )
            )
            return _order_dict(row) if row is not None else None
    row = next(
        (item for item in db.memory_attributed_orders if item.shop_id == shop_id and item.external_order_id == order_id),
        None,
    )
    return _order_dict(row) if row is not None else None


def _find_shop(value: str) -> Optional[dict]:
    if not value:
        return None
    if db.configured():
        with db.session_scope() as session:
            shop = session.get(Shop, value)
            if shop is None:
                matches = session.scalars(select(Shop).where(Shop.name.ilike(value))).all()
                if len(matches) != 1:
                    return None
                shop = matches[0]
            return {"id": shop.id, "country": shop.country, "market": shop.market}
    shop = db.memory_shops.get(value)
    if shop is None:
        matches = [row for row in db.memory_shops.values() if str(row.name).lower() == value.lower()]
        if len(matches) != 1:
            return None
        shop = matches[0]
    return {"id": shop.id, "country": getattr(shop, "country", None), "market": getattr(shop, "market", None)}


def _find_link_by_code(code: str) -> Optional[dict]:
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(CreatorProductLink).where(CreatorProductLink.discount_code == code))
            return _link_dict(row) if row is not None else None
    row = next((item for item in db.memory_creator_product_links if item.discount_code == code), None)
    return _link_dict(row) if row is not None else None


def _brief_id(link: dict) -> Optional[str]:
    item_id = link.get("shortlist_item_id")
    product_id = link.get("product_id")
    creator_id = link.get("creator_id")
    if db.configured():
        with db.session_scope() as session:
            query = select(Brief).order_by(Brief.created_at.desc())
            if item_id:
                query = query.where(Brief.shortlist_item_id == item_id)
            else:
                query = query.where(Brief.product_id == product_id, Brief.creator_id == creator_id)
            brief = session.scalars(query).first()
            return brief.id if brief is not None else None
    for brief in reversed(db.memory_briefs):
        if item_id and getattr(brief, "shortlist_item_id", None) == item_id:
            return brief.id
        if getattr(brief, "product_id", None) == product_id and getattr(brief, "creator_id", None) == creator_id:
            return brief.id
    return None


def _checked_item(session, shortlist_item_id, product_id, creator_id) -> Optional[str]:
    if shortlist_item_id:
        item = session.get(ShortlistItem, shortlist_item_id)
        if item is None or item.creator_id != creator_id:
            raise HTTPException(status_code=422, detail="shortlist_item_id does not match this creator")
        return item.id
    item = session.scalars(
        select(ShortlistItem)
        .join(Shortlist, Shortlist.id == ShortlistItem.shortlist_id)
        .where(Shortlist.product_id == product_id, ShortlistItem.creator_id == creator_id)
        .order_by(Shortlist.created_at.desc())
    ).first()
    return item.id if item is not None else None


def _checked_item_memory(shortlist_item_id, product_id, creator_id) -> Optional[str]:
    if shortlist_item_id:
        item = next((row for row in db.memory_shortlist_items if row.id == shortlist_item_id), None)
        if item is None or item.creator_id != creator_id:
            raise HTTPException(status_code=422, detail="shortlist_item_id does not match this creator")
        return item.id
    shortlists = {row.id: row for row in db.memory_shortlists if row.product_id == product_id}
    matches = [row for row in db.memory_shortlist_items if row.creator_id == creator_id and row.shortlist_id in shortlists]
    if not matches:
        return None
    matches.sort(key=lambda row: str(getattr(shortlists[row.shortlist_id], "created_at", "")), reverse=True)
    return matches[0].id


def _destination(destination_url: Optional[str], attributes: Any) -> str:
    candidate = (destination_url or "").strip()
    if not candidate and isinstance(attributes, dict):
        candidate = str(attributes.get("url") or attributes.get("product_url") or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="destination_url must be an http(s) URL")
    return candidate


def _fresh_code(session) -> str:
    for _ in range(8):
        code = "PC" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if session is None:
            if any(row.discount_code == code for row in db.memory_creator_product_links):
                continue
            return code
        taken = session.scalar(select(CreatorProductLink.id).where(CreatorProductLink.discount_code == code))
        if taken is None:
            return code
    raise HTTPException(status_code=503, detail="Could not allocate a tracking code")


def _cell_is_link(value: str) -> bool:
    text = value.strip()
    return "://" in text or text.startswith("/r/") or "plantr=" in text.lower()


def _code_from_cell(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if _cell_is_link(text) or text.startswith("/"):
        params = _query_params(text)
        plantr = (params.get("plantr") or "").strip()
        if plantr:
            return plantr
        parsed = urlparse(text if "://" in text else "https://x" + (text if text.startswith("/") else "/" + text))
        segment = parsed.path.rstrip("/").split("/")[-1]
        return segment.strip()
    return text


def _parse_csv(csv_text: str) -> list[dict]:
    sample = csv_text.lstrip("\ufeff")
    if not sample.strip():
        raise HTTPException(status_code=422, detail="CSV is empty")
    reader = csv.DictReader(io.StringIO(sample))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV header is missing")
    rows = []
    for raw in reader:
        mapped = {}
        for key, value in raw.items():
            if key is None:
                continue
            canonical = HEADER_ALIASES.get(key.strip().lower().replace(" ", "_"))
            if canonical:
                mapped[canonical] = "" if value is None else str(value).strip()
        rows.append(mapped)
    return rows


def _link_dict(row: Any, platform_codes: Optional[list] = None) -> dict:
    code = row.discount_code
    return {
        "id": row.id,
        "shop_id": row.shop_id,
        "product_id": row.product_id,
        "creator_id": row.creator_id,
        "shortlist_item_id": row.shortlist_item_id,
        "discount_code": code,
        "tracking_path": f"/r/{code}",
        "direct_url": direct_url(row.link_url, code) if code else None,
        "platform_links": platform_variants(code, row.link_url) if code else [],
        "platform_codes": list(platform_codes or []),
        "link_url": row.link_url,
        "platform": row.platform,
        "status": row.status,
        "created_at": row.created_at,
    }


def _order_dict(row: Any) -> dict:
    gross = row.gmv_gross
    return {
        "id": row.id,
        "shop_id": row.shop_id,
        "external_order_id": row.external_order_id,
        "gmv_gross": float(gross) if gross is not None else None,
        "net_gmv": float(row.net_gmv) if row.net_gmv is not None else None,
        "currency": row.currency,
        "creator_id": row.creator_id,
        "product_id": row.product_id,
        "shortlist_item_id": row.shortlist_item_id,
        "brief_id": row.brief_id,
        "attribution_date": row.attribution_date,
        "ingest_source": row.ingest_source,
        "created_at": row.created_at,
        "attributed": bool(getattr(row, "attributed", True)),
        "source_platform": getattr(row, "source_platform", None) or "unknown",
        "channel_kind": getattr(row, "channel_kind", None),
    }
