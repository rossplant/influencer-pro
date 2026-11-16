"""Curated seller catalogue: profile helpers, product completeness, and CSV import.

Plantr Collab is a curated service. A product is ready for matching and briefs
only when the seller has said why it was chosen, what it offers creators, and
the commission. Catalogue size is not a hard cap. Above CATALOGUE_FEE_THRESHOLD
the seller sees a warning. Nothing is blocked and nothing is charged.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import HTTPException

import taxonomy

MAX_CSV_BYTES = 1_048_576
MAX_CSV_ROWS = 500
CSV_COLUMNS = ("title", "url", "price", "category", "image_url", "commission")
TEMPLATE_CSV = (
    "title,url,price,category,image_url,commission\n"
    "Ceramic pourer,https://example.com/pourer,42,home,https://example.com/pourer.jpg,15\n"
)

PRODUCTS_COPY = (
    "List the products you actually want a creator to talk about. "
    "A short list, with a plain reason for each one, is what we use. You do not need to add the whole shop."
)
FIRST_PRODUCT_COPY = (
    "Start with one product you have chosen on purpose. "
    "Say why it is on the list and what a creator's audience gets from it."
)
COMPLETE_PROMPT = "Complete this product"


def catalogue_fee_threshold() -> int:
    """How many products a seller can list before the fee warning. Default 10."""
    raw = os.getenv("CATALOGUE_FEE_THRESHOLD", "").strip()
    if not raw:
        return 10
    try:
        return max(0, int(raw))
    except ValueError:
        return 10


def fee_warning(count: int) -> str:
    threshold = catalogue_fee_threshold()
    if count <= threshold:
        return ""
    return (
        f"You have {count} products, which is above {threshold}. "
        "Extra products may attract a fee after the pilot. There is no charge now."
    )


def http_url(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def website_url(value: Optional[str]) -> Optional[str]:
    """A public site from an application contact field. Handles are ignored."""
    text = str(value or "").strip()
    if not text or text.startswith("@") or " " in text:
        return None
    direct = http_url(text)
    if direct:
        return direct
    if "." not in text:
        return None
    return http_url("https://" + text.lstrip("/"))


def first_category(raw: Optional[str]) -> Optional[str]:
    """First taxonomy slug in a comma-separated application category list."""
    allowed = taxonomy.slugs()
    for part in str(raw or "").split(","):
        slug = part.strip().lower()
        if slug in allowed:
            return slug
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def product_public_url(product: Any) -> Optional[str]:
    """Column url, then the legacy attributes.url, then attributes.product_url."""
    if product is None:
        return None
    if isinstance(product, dict):
        column = product.get("url")
        attributes = product.get("attributes") or {}
    else:
        column = getattr(product, "url", None)
        attributes = getattr(product, "attributes", None) or {}
    if _text(column):
        return _text(column)
    if isinstance(attributes, dict):
        legacy = attributes.get("url") or attributes.get("product_url")
        if _text(legacy):
            return _text(legacy)
    return None


def product_is_complete(product: Any) -> bool:
    """Ready for seller matching and briefs: why, value, and a commission."""
    if product is None:
        return False
    if isinstance(product, dict):
        why = product.get("why_chosen")
        value = product.get("value_to_creators")
        rate = product.get("commission_rate")
    else:
        why = getattr(product, "why_chosen", None)
        value = getattr(product, "value_to_creators", None)
        rate = getattr(product, "commission_rate", None)
    return bool(_text(why)) and bool(_text(value)) and rate is not None


def needs_completion_prompt(product: Any) -> bool:
    """Legacy and draft rows that are missing why or value."""
    if product is None:
        return False
    if isinstance(product, dict):
        why = product.get("why_chosen")
        value = product.get("value_to_creators")
    else:
        why = getattr(product, "why_chosen", None)
        value = getattr(product, "value_to_creators", None)
    return not _text(why) or not _text(value)


def parse_commission(raw: Any, *, required: bool) -> Optional[float]:
    text = _text(raw)
    if not text:
        if required:
            raise HTTPException(status_code=422, detail="Commission is required")
        return None
    try:
        rate = float(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Commission must be a number") from exc
    if rate < 0 or rate > 100:
        raise HTTPException(status_code=422, detail="Commission must be between 0 and 100")
    return rate


def parse_price(raw: Any) -> Optional[float]:
    text = _text(raw)
    if not text:
        return None
    try:
        amount = float(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Price must be a number") from exc
    if amount < 0:
        raise HTTPException(status_code=422, detail="Price cannot be negative")
    return amount


def require_curation(why: Any, value: Any, commission: Any) -> float:
    if not _text(why):
        raise HTTPException(status_code=422, detail="Say why you chose this product")
    if not _text(value):
        raise HTTPException(status_code=422, detail="Say what this product offers creators")
    rate = parse_commission(commission, required=True)
    assert rate is not None
    return rate


def copy_urls_from_attributes(connection) -> int:
    """Copy attributes.url onto products.url where the column is still empty."""
    from sqlalchemy import text

    rows = connection.execute(text("SELECT id, attributes FROM products")).mappings().all()
    updated = 0
    for row in rows:
        raw = row["attributes"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw, dict):
            continue
        url = _text(raw.get("url"))
        if not url:
            continue
        result = connection.execute(
            text("UPDATE products SET url = :url WHERE id = :id AND url IS NULL"),
            {"url": url, "id": row["id"]},
        )
        updated += int(result.rowcount or 0)
    return updated


def _category_error(slug: str) -> Optional[str]:
    if not slug:
        return None
    import services

    try:
        services.validate_category_lists([slug])
    except HTTPException as exc:
        return str(exc.detail)
    return None


def normalise_import_row(raw: dict) -> dict:
    """One CSV or preview row. `ok` is false when the row cannot be ticked."""
    title = _text(raw.get("title"))
    errors = []
    if not title:
        errors.append("Title is required")
    elif len(title) > 500:
        errors.append("Title is too long")
    url = _text(raw.get("url"))
    if url and http_url(url) is None:
        errors.append("URL must be an http or https address")
    image = _text(raw.get("image_url"))
    if image and http_url(image) is None:
        errors.append("Image URL must be an http or https address")
    price_text = _text(raw.get("price"))
    price = None
    if price_text:
        try:
            price = float(price_text)
        except ValueError:
            errors.append("Price must be a number")
        else:
            if price < 0:
                errors.append("Price cannot be negative")
    category = _text(raw.get("category")).lower()
    if category:
        problem = _category_error(category)
        if problem:
            errors.append(problem)
    commission_text = _text(raw.get("commission") or raw.get("commission_rate"))
    commission = None
    if commission_text:
        try:
            commission = float(commission_text)
        except ValueError:
            errors.append("Commission must be a number")
        else:
            if commission < 0 or commission > 100:
                errors.append("Commission must be between 0 and 100")
    return {
        "ok": not errors,
        "error": "; ".join(errors),
        "title": title,
        "url": http_url(url) if url and http_url(url) else "",
        "price": price,
        "price_text": price_text,
        "category": category,
        "image_url": http_url(image) if image and http_url(image) else "",
        "commission": commission,
        "commission_text": commission_text,
    }


def parse_product_csv(payload: bytes) -> list[dict]:
    """Parse a products CSV. Raises 422 when the file itself is not acceptable."""
    if payload is None:
        raise HTTPException(status_code=422, detail="Choose a CSV file")
    if len(payload) > MAX_CSV_BYTES:
        raise HTTPException(status_code=422, detail="CSV must be 1MB or smaller")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8") from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="The CSV is empty")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=422, detail="The CSV needs a header row")
    fields = {(name or "").strip().lower() for name in reader.fieldnames}
    missing = [name for name in ("title", "url", "price", "category", "image_url") if name not in fields]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="The CSV needs these columns: " + ", ".join(missing),
        )
    rows = []
    for raw in reader:
        if raw is None:
            continue
        lowered = {(key or "").strip().lower(): value for key, value in raw.items()}
        if not any(_text(value) for value in lowered.values()):
            continue
        rows.append(normalise_import_row(lowered))
        if len(rows) > MAX_CSV_ROWS:
            raise HTTPException(status_code=422, detail="A CSV can list at most 500 products")
    if not rows:
        raise HTTPException(status_code=422, detail="The CSV has no products")
    return rows


def shopify_host(raw: Optional[str]) -> Optional[str]:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if "://" in text:
        text = (urlparse(text).hostname or "").lower()
    text = text.split("/")[0].strip()
    if not text or " " in text or len(text) > 255 or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", text):
        raise HTTPException(status_code=422, detail="Enter the Shopify domain, for example the-store.myshopify.com")
    return text
