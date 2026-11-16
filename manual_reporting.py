"""Manual sales reporting for sellers who are not on Shopify.

A seller answers "Do you sell on Shopify?" on their profile. No marks the shop
`sales_reporting = manual`. Those sellers upload one CSV a month, or say there
were no sales. An admin accepts or rejects the file. Accepting runs the
existing order importer, and statements then generate the same way as today.

Creatrs do not see a manual seller until an admin has approved them and
MANUAL_REPORTING_LIVE is on. Staging defaults that flag on. Production defaults
it off. NON_SHOPIFY_CAP (default 10) is the number of approved manual sellers.
Going past it needs an explicit confirmation.

Money is AUD. Months and displayed times use Australia/Sydney.
"""

from __future__ import annotations

import csv
import io
import os
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select

import attribution
import db
import events
from models import CreatorPlatformCode, CreatorProductLink, SalesReport, Shop, utcnow

SYDNEY = ZoneInfo("Australia/Sydney")
CENT = Decimal("0.01")
MAX_BYTES = 1_048_576
MAX_ROWS = 500
COLUMNS = (
    "order_id",
    "order_date",
    "discount_code",
    "product_sku_or_name",
    "quantity",
    "gross_amount_aud",
    "shipping_aud",
    "gst_aud",
    "net_amount_aud",
)
TEMPLATE_CSV = ",".join(COLUMNS) + "\n"
STATUS_LABELS = {"received": "Received", "accepted": "Accepted", "rejected": "Rejected"}
MANUAL_LABEL = (
    "Manual sales reporting: sales are credited to Creatrs when the seller reports them each month."
)
TRADING_HOLD = "Products and briefs open after Plantr approves manual sales reporting."
SHOPIFY_UPLOAD_BLOCK = "Shops that sync from Shopify can't upload a sales report."
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
MONEY_RE = re.compile(r"\d+(\.\d{1,2})?")
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def non_shopify_cap() -> int:
    raw = os.getenv("NON_SHOPIFY_CAP", "").strip()
    if not raw:
        return 10
    try:
        value = int(raw)
    except ValueError:
        return 10
    return value if value >= 0 else 10


def manual_reporting_live() -> bool:
    """Creatrs see approved manual sellers only when this is true.

    An explicit MANUAL_REPORTING_LIVE wins. Otherwise staging is on and every
    other environment, including production, is off.
    """
    raw = os.getenv("MANUAL_REPORTING_LIVE", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    env = (os.getenv("ENV") or os.getenv("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
    return env == "staging"


def format_aud(amount: Any) -> str:
    """A$14.50. Always two decimal places."""
    try:
        value = Decimal(str(amount if amount is not None else "0")).quantize(CENT, rounding=ROUND_HALF_UP)
    except Exception:
        value = Decimal("0.00")
    sign = "-" if value < 0 else ""
    text = f"{abs(value):,.2f}"
    return f"{sign}A${text}"


def reporting_month(now: Optional[datetime] = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(SYDNEY)
    return f"{local.year:04d}-{local.month:02d}"


def month_label(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return f"{MONTH_NAMES[month - 1]} {year}"


def format_sydney(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(SYDNEY)
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local.day} {local.strftime('%b')} {local.year}, {hour}:{local.strftime('%M')} {local.strftime('%p').lower()}"


def normalise_shopify_domain(raw: Optional[str]) -> str:
    """Trim the scheme and slashes, lowercase, and require *.myshopify.com."""
    text = (raw or "").strip()
    if not text:
        raise HTTPException(
            status_code=422,
            detail="Enter the Shopify domain. It looks like the-store.myshopify.com.",
        )
    text = re.sub(r"^https?://", "", text, flags=re.IGNORECASE)
    text = text.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    text = text.split("/", 1)[0].strip().lower()
    if not DOMAIN_RE.fullmatch(text):
        raise HTTPException(
            status_code=422,
            detail="Enter the Shopify domain ending in .myshopify.com, for example the-store.myshopify.com.",
        )
    return text


def _get(shop: Any, name: str, default=None):
    if shop is None:
        return default
    if isinstance(shop, dict):
        return shop.get(name, default)
    return getattr(shop, name, default)


def is_manual(shop: Any) -> bool:
    return _get(shop, "sales_reporting") == "manual"


def needs_approval(shop: Any) -> bool:
    return is_manual(shop) and not bool(_get(shop, "manual_approved"))


def creator_visible(shop: Any) -> bool:
    """Manual sellers stay hidden from Creatrs until approved and the flag is on."""
    if not is_manual(shop):
        return True
    return bool(_get(shop, "manual_approved")) and manual_reporting_live()


def require_trading(shop: Any) -> None:
    if needs_approval(shop):
        raise HTTPException(status_code=403, detail=TRADING_HOLD)


def shop_flags(shop_id: str) -> Optional[dict]:
    if db.configured():
        with db.session_scope() as session:
            shop = session.get(Shop, shop_id)
            return None if shop is None else _flags(shop)
    shop = db.memory_shops.get(shop_id)
    return None if shop is None else _flags(shop)


def _flags(shop: Any) -> dict:
    return {
        "id": _get(shop, "id"),
        "name": _get(shop, "name"),
        "business_name": _get(shop, "business_name"),
        "sales_reporting": _get(shop, "sales_reporting"),
        "sells_on_shopify": _get(shop, "sells_on_shopify"),
        "manual_approved": bool(_get(shop, "manual_approved")),
        "manual_approved_by": _get(shop, "manual_approved_by"),
        "manual_approved_at": _get(shop, "manual_approved_at"),
        "shopify_domain": _get(shop, "shopify_domain"),
    }


def visible_shop(shop_id: Optional[str]) -> bool:
    if not shop_id:
        return True
    flags = shop_flags(shop_id)
    if flags is None:
        return True
    return creator_visible(flags)


def filter_for_creators(rows: list[dict], *, shop_key: str = "shop_id") -> list[dict]:
    return [row for row in rows if visible_shop(row.get(shop_key))]


def parse_period(value: str, *, now: Optional[datetime] = None) -> str:
    text = (value or "").strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        raise HTTPException(status_code=422, detail="Choose a month, like 2026-11.")
    if text > reporting_month(now):
        raise HTTPException(status_code=422, detail="You can't report a month that hasn't started.")
    return text


def status_label(status: Optional[str]) -> str:
    return STATUS_LABELS.get(status or "", "")


# ---------------------------------------------------------------- shops


def approved_manual_count() -> int:
    if db.configured():
        with db.session_scope() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(Shop)
                    .where(Shop.sales_reporting == "manual", Shop.manual_approved.is_(True))
                )
                or 0
            )
    return sum(1 for shop in db.memory_shops.values() if is_manual(shop) and bool(getattr(shop, "manual_approved", False)))


def waiting_manual_sellers() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(
                select(Shop).where(Shop.sales_reporting == "manual", Shop.manual_approved.is_(False)).order_by(Shop.created_at)
            ).all()
            return [_flags(row) for row in rows]
    rows = [shop for shop in db.memory_shops.values() if needs_approval(shop)]
    rows.sort(key=lambda shop: str(getattr(shop, "created_at", "")))
    return [_flags(shop) for shop in rows]


def approve_manual_seller(shop_id: str, actor: str, *, confirm: bool) -> dict:
    count = approved_manual_count()
    cap = non_shopify_cap()
    if count >= cap and not confirm:
        raise HTTPException(
            status_code=409,
            detail=(
                f"There are already {count} approved manual sellers, and the cap is {cap}. "
                "Tick the confirmation to approve another."
            ),
        )

    def _apply(shop) -> None:
        if shop is None:
            raise HTTPException(status_code=404, detail="Shop not found")
        if not is_manual(shop):
            raise HTTPException(status_code=422, detail="Only a seller who is not on Shopify can be approved here.")
        if bool(_get(shop, "manual_approved")):
            raise HTTPException(status_code=409, detail="This seller is already approved.")
        shop.manual_approved = True
        shop.manual_approved_by = actor
        shop.manual_approved_at = utcnow()
        events.emit(
            "manual_seller_approved",
            shop,
            "operator",
            approved_by=actor,
            approved_count_before=count,
            cap=cap,
            confirmed_over_cap=bool(confirm and count >= cap),
        )

    if db.configured():
        with db.session_scope() as session:
            _apply(session.get(Shop, shop_id))
    else:
        _apply(db.memory_shops.get(shop_id))
    return {"ok": True, "shop_id": shop_id, "approved_count": count + 1, "cap": cap}


# ---------------------------------------------------------------- files


def validate_csv(shop_id: str, period: str, payload: bytes, *, filename: str = "") -> tuple[list[dict], list[str]]:
    """Check the whole file. Any problem returns every plain-English error and no rows."""
    errors: list[str] = []
    if _looks_like_excel(payload, filename):
        return [], ["Upload a CSV file from the template. Excel files aren't accepted."]
    if len(payload) > MAX_BYTES:
        return [], ["This file is larger than 1MB. Remove rows or split the month."]
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["This file isn't valid text. Save it as a CSV and upload that."]
    if not text.strip():
        return [], ["The file is empty. Download the template, or use No sales this month."]
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ["The file needs a header row. Download the template and use those column names."]
    headers = [(name or "").strip() for name in reader.fieldnames]
    if any(not name for name in headers):
        errors.append("The header row has a blank column. Download the template and use those column names.")
    missing = [name for name in COLUMNS if name not in headers]
    extra = [name for name in headers if name and name not in COLUMNS]
    if missing:
        errors.append(
            "The file is missing required columns: "
            + ", ".join(missing)
            + ". Download the template and use those column names."
        )
    if extra:
        errors.append(
            "The file has columns that are not in the template: "
            + ", ".join(extra)
            + ". Don't rename or add columns."
        )
    if errors:
        return [], errors
    codes = collab_codes(shop_id)
    stored = stored_order_ids(shop_id)
    seen: dict[str, int] = {}
    rows: list[dict] = []
    data_rows = 0
    for raw in reader:
        if raw is None or not any((value or "").strip() for value in raw.values() if value is not None):
            continue
        data_rows += 1
        row_no = data_rows + 1
        if data_rows > MAX_ROWS:
            errors.append(f"This file has more than {MAX_ROWS} rows. Split it or remove rows.")
            break
        lowered = {(key or "").strip(): ("" if value is None else str(value).strip()) for key, value in raw.items()}
        parsed, row_errors = _validate_row(lowered, row_no, period, codes, stored, seen)
        errors.extend(row_errors)
        if parsed is not None:
            rows.append(parsed)
    if data_rows == 0:
        errors.append("The file has no sales rows. If you had no sales, use No sales this month.")
    if errors:
        return [], errors
    return rows, []


def _looks_like_excel(payload: bytes, filename: str) -> bool:
    name = (filename or "").lower()
    if name.endswith((".xls", ".xlsx", ".xlsm")):
        return True
    return payload.startswith(b"PK\x03\x04") or payload.startswith(b"\xd0\xcf\x11\xe0")


def _validate_row(raw: dict, row_no: int, period: str, codes: dict, stored: set, seen: dict) -> tuple[Optional[dict], list[str]]:
    errors: list[str] = []
    order_id = raw.get("order_id") or ""
    if not order_id:
        errors.append(f"Row {row_no}: order id is required.")
    elif order_id in seen:
        errors.append(f"Order {order_id} is listed more than once (rows {seen[order_id]} and {row_no}).")
    else:
        seen[order_id] = row_no
        if order_id in stored:
            errors.append(f"Order {order_id} is already recorded, so this file was not saved.")
    order_day = _parse_day(raw.get("order_date") or "", row_no, period, errors)
    code_text = raw.get("discount_code") or ""
    canonical = codes.get(code_text.upper())
    if not code_text:
        errors.append(f"Row {row_no}: discount code is required.")
    elif canonical is None:
        errors.append(f"Row {row_no}: the code {code_text} is not a collab code for this shop.")
    sku = raw.get("product_sku_or_name") or ""
    if not sku:
        errors.append(f"Row {row_no}: add the product SKU or name.")
    quantity = raw.get("quantity") or ""
    if not re.fullmatch(r"[1-9]\d*", quantity):
        errors.append(f"Row {row_no}: quantity must be a whole number of 1 or more.")
    gross = _money(raw.get("gross_amount_aud") or "", row_no, "gross amount", errors)
    shipping = _money(raw.get("shipping_aud") or "", row_no, "shipping", errors)
    gst = _money(raw.get("gst_aud") or "", row_no, "GST", errors)
    net = _money(raw.get("net_amount_aud") or "", row_no, "net amount", errors)
    if None not in (gross, shipping, gst, net):
        expected = gross - shipping - gst
        if abs(net - expected) > CENT:
            errors.append(
                f"Row {row_no}: net amount should be gross minus shipping minus GST "
                f"({format_aud(expected)}), but the file says {format_aud(net)}."
            )
    if errors or None in (order_day, canonical, gross, shipping, gst, net):
        return None, errors
    return (
        {
            "order_id": order_id[:128],
            "order_date": order_day.isoformat(),
            "discount_code": canonical,
            "product_sku_or_name": sku[:255],
            "quantity": quantity,
            "gross": format(gross, "f"),
            "shipping": format(shipping, "f"),
            "gst": format(gst, "f"),
            "net": format(net, "f"),
        },
        [],
    )


def _parse_day(value: str, row_no: int, period: str, errors: list[str]) -> Optional[date]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        errors.append(f"Row {row_no}: order date must be YYYY-MM-DD.")
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        errors.append(f"Row {row_no}: {value} is not a real date.")
        return None
    if f"{parsed.year:04d}-{parsed.month:02d}" != period:
        errors.append(f"Row {row_no}: order date {value} is not in {month_label(period)}.")
        return None
    return parsed


def _money(value: str, row_no: int, label: str, errors: list[str]) -> Optional[Decimal]:
    if not MONEY_RE.fullmatch(value):
        errors.append(f"Row {row_no}: {label} must be a dollar amount like 14.50, and it can't be negative.")
        return None
    return Decimal(value)


def collab_codes(shop_id: str) -> dict[str, str]:
    """Uppercase code -> the stored code. Plantr codes, store codes, and platform codes."""
    found: dict[str, str] = {}

    def _add(value: Optional[str]) -> None:
        text = (value or "").strip()
        if text:
            found[text.upper()] = text

    if db.configured():
        with db.session_scope() as session:
            links = session.scalars(select(CreatorProductLink).where(CreatorProductLink.shop_id == shop_id)).all()
            for link in links:
                _add(link.discount_code)
                _add(link.store_discount_code)
            extras = session.scalars(select(CreatorPlatformCode).where(CreatorPlatformCode.shop_id == shop_id)).all()
            for row in extras:
                _add(row.code)
        return found
    for link in db.memory_creator_product_links:
        if link.shop_id == shop_id:
            _add(getattr(link, "discount_code", None))
            _add(getattr(link, "store_discount_code", None))
    for row in getattr(db, "memory_creator_platform_codes", []):
        if getattr(row, "shop_id", None) == shop_id:
            _add(getattr(row, "code", None))
    return found


def stored_order_ids(shop_id: str) -> set[str]:
    from models import AttributedOrder

    if db.configured():
        with db.session_scope() as session:
            values = session.scalars(select(AttributedOrder.external_order_id).where(AttributedOrder.shop_id == shop_id)).all()
            return {str(value) for value in values}
    return {str(row.external_order_id) for row in db.memory_attributed_orders if row.shop_id == shop_id}


def importer_csv(shop_id: str, rows: list[dict]) -> str:
    """The existing importer's columns. `net` is the commission base, not the gross."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["order_id", "date", "code", "gross", "net", "shipping", "gst", "currency", "shop", "quantity", "sku"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "order_id": row["order_id"],
                "date": row["order_date"],
                "code": row["discount_code"],
                "gross": row["gross"],
                "net": row["net"],
                "shipping": row["shipping"],
                "gst": row["gst"],
                "currency": "AUD",
                "shop": shop_id,
                "quantity": row.get("quantity") or "",
                "sku": row.get("product_sku_or_name") or "",
            }
        )
    return buffer.getvalue()


# ---------------------------------------------------------------- submissions


def ensure_manual_seller(shop_id: str) -> Any:
    if db.configured():
        with db.session_scope() as session:
            shop = session.get(Shop, shop_id)
            if shop is None:
                raise HTTPException(status_code=404, detail="Shop not found")
            reporting = shop.sales_reporting
    else:
        shop = db.memory_shops.get(shop_id)
        if shop is None:
            raise HTTPException(status_code=404, detail="Shop not found")
        reporting = getattr(shop, "sales_reporting", None)
    if reporting == "shopify_sync":
        raise HTTPException(status_code=403, detail=SHOPIFY_UPLOAD_BLOCK)
    if reporting != "manual":
        raise HTTPException(
            status_code=403,
            detail="Answer no to Do you sell on Shopify on your profile before sending a sales report.",
        )
    return shop_id


def submit_csv(shop_id: str, period: str, payload: bytes, *, filename: str = "", actor: str = "seller") -> dict:
    period = parse_period(period)
    ensure_manual_seller(shop_id)
    rows, errors = validate_csv(shop_id, period, payload, filename=filename)
    if errors:
        return {"ok": False, "errors": errors, "period": period}
    net = sum((Decimal(row["net"]) for row in rows), Decimal("0.00"))
    report = _upsert(
        shop_id,
        period,
        kind="csv",
        csv_text=payload.decode("utf-8-sig"),
        row_count=len(rows),
        net_total=net,
        actor=actor,
    )
    return {"ok": True, "errors": [], "period": period, "report": report}


def submit_no_sales(shop_id: str, period: str, *, actor: str = "seller") -> dict:
    period = parse_period(period)
    ensure_manual_seller(shop_id)
    report = _upsert(shop_id, period, kind="no_sales", csv_text=None, row_count=0, net_total=Decimal("0.00"), actor=actor)
    return {"ok": True, "errors": [], "period": period, "report": report}


def _upsert(shop_id: str, period: str, *, kind: str, csv_text: Optional[str], row_count: int, net_total: Decimal, actor: str) -> dict:
    now = utcnow()
    replaced = False

    def _fill(row, created: bool) -> None:
        nonlocal replaced
        if not created and row.status != "received":
            raise HTTPException(
                status_code=409,
                detail=f"This month already has a sales report. It is {status_label(row.status)}.",
            )
        replaced = not created
        row.kind = kind
        row.status = "received"
        row.csv_text = csv_text
        row.row_count = row_count
        row.net_total = net_total
        row.rejection_reason = None
        row.reviewed_at = None
        row.reviewed_by = None
        row.submitted_at = now

    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(SalesReport).where(SalesReport.shop_id == shop_id, SalesReport.period == period))
            created = row is None
            if row is None:
                row = SalesReport(
                    id=str(uuid.uuid4()),
                    shop_id=shop_id,
                    period=period,
                    kind=kind,
                    status="received",
                    submitted_at=now,
                )
                session.add(row)
            _fill(row, created)
            shop = session.get(Shop, shop_id)
            if shop is not None:
                events.emit(
                    "sales_report_submitted",
                    shop,
                    "api",
                    report_id=row.id,
                    period=period,
                    kind=kind,
                    row_count=row_count,
                    net_total=str(net_total),
                    replaced=replaced,
                    actor_email=actor,
                )
            session.flush()
            return _report_dict(row)
    row = next((item for item in db.memory_sales_reports if item.shop_id == shop_id and item.period == period), None)
    created = row is None
    if row is None:
        row = SimpleNamespace(
            id=str(uuid.uuid4()),
            shop_id=shop_id,
            period=period,
            kind=kind,
            status="received",
            csv_text=None,
            row_count=0,
            net_total=0,
            rejection_reason=None,
            submitted_at=now,
            reviewed_at=None,
            reviewed_by=None,
        )
        db.memory_sales_reports.append(row)
    _fill(row, created)
    shop = db.memory_shops.get(shop_id)
    if shop is not None:
        events.emit(
            "sales_report_submitted",
            shop,
            "api",
            report_id=row.id,
            period=period,
            kind=kind,
            row_count=row_count,
            net_total=str(net_total),
            replaced=replaced,
            actor_email=actor,
        )
    return _report_dict(row)


def seller_view(shop_id: str, period: str) -> dict:
    report = _find_report(shop_id, period)
    summary = None
    reason = None
    label = ""
    kind = None
    if report is not None:
        label = status_label(report["status"])
        kind = report["kind"]
        reason = report.get("rejection_reason")
        if kind == "no_sales":
            summary = "No sales this month."
        else:
            summary = f"{report['row_count']} orders, net {format_aud(report['net_total'])}."
    return {
        "period": period,
        "month_label": month_label(period),
        "status": None if report is None else report["status"],
        "status_label": label,
        "reason": reason,
        "summary": summary,
        "kind": kind,
    }


def _find_report(shop_id: str, period: str) -> Optional[dict]:
    if db.configured():
        with db.session_scope() as session:
            row = session.scalar(select(SalesReport).where(SalesReport.shop_id == shop_id, SalesReport.period == period))
            return None if row is None else _report_dict(row)
    row = next((item for item in db.memory_sales_reports if item.shop_id == shop_id and item.period == period), None)
    return None if row is None else _report_dict(row)


def _report_dict(row: Any) -> dict:
    net = _get(row, "net_total") or 0
    return {
        "id": row.id,
        "shop_id": row.shop_id,
        "period": row.period,
        "kind": row.kind,
        "status": row.status,
        "status_label": status_label(row.status),
        "row_count": int(row.row_count or 0),
        "net_total": str(Decimal(str(net)).quantize(CENT)),
        "rejection_reason": row.rejection_reason,
        "submitted_at": row.submitted_at,
        "reviewed_at": row.reviewed_at,
        "reviewed_by": row.reviewed_by,
        "csv_text": row.csv_text,
    }


def pending_reports() -> list[dict]:
    names: dict[str, str] = {}
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(
                select(SalesReport).where(SalesReport.status == "received").order_by(SalesReport.submitted_at)
            ).all()
            reports = [_report_dict(row) for row in rows]
            for shop in session.scalars(select(Shop)).all():
                names[shop.id] = shop.business_name or shop.name or "Shop"
    else:
        reports = [_report_dict(row) for row in db.memory_sales_reports if row.status == "received"]
        reports.sort(key=lambda row: str(row["submitted_at"]))
        for shop in db.memory_shops.values():
            names[shop.id] = getattr(shop, "business_name", None) or getattr(shop, "name", None) or "Shop"
    for report in reports:
        report["shop_name"] = names.get(report["shop_id"]) or "Shop"
        report["submitted_label"] = format_sydney(report["submitted_at"])
        report["net_label"] = format_aud(report["net_total"])
        report["month_label"] = month_label(report["period"])
        report.pop("csv_text", None)
    return reports


def approve_report(report_id: str, actor: str) -> dict:
    report = _load_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Sales report not found")
    if report["status"] != "received":
        raise HTTPException(status_code=409, detail=f"This report is already {status_label(report['status'])}.")
    imported = {"created": 0, "existing": 0, "errors": 0}
    if report["kind"] == "csv":
        rows, errors = validate_csv(report["shop_id"], report["period"], (report["csv_text"] or "").encode("utf-8"))
        if errors:
            raise HTTPException(status_code=422, detail=errors)
        csv_text = importer_csv(report["shop_id"], rows)
        imported = attribution.import_orders(csv_text, dry_run=False, ingest_source="manual_sales")
        if imported.get("errors"):
            details = [row.get("error") for row in imported.get("rows") or [] if row.get("status") == "error"]
            raise HTTPException(status_code=422, detail=details or ["The file could not be imported."])
    _mark(report_id, "accepted", actor, reason=None)
    _generate_statements(report["period"], report["shop_id"])
    _audit_review(report, "sales_report_accepted", actor, reason=None, imported=imported)
    return {"ok": True, "imported": imported}


def reject_report(report_id: str, actor: str, reason: str) -> dict:
    text = (reason or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Add a reason before rejecting.")
    if len(text) > 1000:
        raise HTTPException(status_code=422, detail="The reason must be 1000 characters or fewer.")
    report = _load_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Sales report not found")
    if report["status"] != "received":
        raise HTTPException(status_code=409, detail=f"This report is already {status_label(report['status'])}.")
    _mark(report_id, "rejected", actor, reason=text)
    _audit_review(report, "sales_report_rejected", actor, reason=text, imported=None)
    return {"ok": True}


def _load_report(report_id: str) -> Optional[dict]:
    if db.configured():
        with db.session_scope() as session:
            row = session.get(SalesReport, report_id)
            return None if row is None else _report_dict(row)
    row = next((item for item in db.memory_sales_reports if item.id == report_id), None)
    return None if row is None else _report_dict(row)


def _mark(report_id: str, status: str, actor: str, *, reason: Optional[str]) -> None:
    now = utcnow()
    if db.configured():
        with db.session_scope() as session:
            row = session.get(SalesReport, report_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Sales report not found")
            row.status = status
            row.rejection_reason = reason
            row.reviewed_at = now
            row.reviewed_by = actor
        return
    row = next((item for item in db.memory_sales_reports if item.id == report_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Sales report not found")
    row.status = status
    row.rejection_reason = reason
    row.reviewed_at = now
    row.reviewed_by = actor


def _audit_review(report: dict, name: str, actor: str, *, reason: Optional[str], imported: Optional[dict]) -> None:
    def _emit(shop) -> None:
        if shop is None:
            return
        events.emit(
            name,
            shop,
            "operator",
            report_id=report["id"],
            period=report["period"],
            kind=report["kind"],
            reason=reason,
            reviewed_by=actor,
            imported=None
            if imported is None
            else {"created": imported.get("created", 0), "existing": imported.get("existing", 0)},
        )

    if db.configured():
        with db.session_scope() as session:
            _emit(session.get(Shop, report["shop_id"]))
        return
    _emit(db.memory_shops.get(report["shop_id"]))


def _generate_statements(period: str, shop_id: str) -> None:
    if not db.configured():
        return
    import commission

    try:
        commission.generate_statements(period, shop_id=shop_id, actor="operator")
    except HTTPException as exc:
        if exc.status_code != 409:
            raise


def render_admin_page(*, csrf: str, error: str = "") -> str:
    import html

    count = approved_manual_count()
    cap = non_shopify_cap()
    banner = f"<p class='banner'>{html.escape(error)}</p>" if error else ""
    confirm = ""
    if count >= cap:
        confirm = (
            "<label class='confirm'><input type='checkbox' name='confirm_over_cap' value='yes'> "
            f"I confirm this approval goes over the cap of {cap}.</label>"
        )
    waiting_rows = []
    for shop in waiting_manual_sellers():
        label = html.escape(shop.get("business_name") or shop.get("name") or "Shop")
        waiting_rows.append(
            "<tr>"
            f"<td>{label}</td>"
            "<td>Waiting</td>"
            "<td>"
            f"<form method=\"post\" action=\"/admin/manual-reporting/sellers/{html.escape(shop['id'])}/approve\">"
            f"<input type=\"hidden\" name=\"csrf_token\" value=\"{html.escape(csrf)}\">"
            f"{confirm}"
            "<button type='submit'>Approve</button>"
            "</form></td></tr>"
        )
    report_rows = []
    for report in pending_reports():
        if report["kind"] == "no_sales":
            what = "No sales declared"
        else:
            what = f"CSV · {report['row_count']} orders · {report['net_label']}"
        report_rows.append(
            "<tr>"
            f"<td>{html.escape(str(report['shop_name']))}</td>"
            f"<td>{html.escape(report['month_label'])}</td>"
            f"<td>{html.escape(what)}</td>"
            f"<td>{html.escape(report['submitted_label'])}</td>"
            "<td>"
            f"<form method=\"post\" action=\"/admin/manual-reporting/reports/{html.escape(report['id'])}/approve\">"
            f"<input type=\"hidden\" name=\"csrf_token\" value=\"{html.escape(csrf)}\">"
            "<button type=\"submit\">Approve</button></form>"
            f"<form method=\"post\" action=\"/admin/manual-reporting/reports/{html.escape(report['id'])}/reject\">"
            f"<input type=\"hidden\" name=\"csrf_token\" value=\"{html.escape(csrf)}\">"
            "<label>Reason <input name='reason' required></label>"
            "<button type='submit' class='secondary'>Reject</button></form>"
            "</td></tr>"
        )
    waiting_body = "".join(waiting_rows) or "<tr><td colspan='3'>No manual sellers are waiting for approval.</td></tr>"
    report_body = "".join(report_rows) or "<tr><td colspan='5'>No sales reports are waiting.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Manual sales</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; background: #f3f6f4; color: #17211c; }}
header {{ background: #16352b; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }}
header a {{ color: white; }}
main {{ max-width: 980px; margin: 24px auto; padding: 0 16px; }}
.card {{ background: white; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #e4ebe7; vertical-align: top; }}
button {{ background: #1f6b4a; color: white; border: 0; border-radius: 8px; padding: 8px 12px; font: inherit; cursor: pointer; }}
button.secondary {{ background: #e7eeea; color: #16352b; }}
label {{ display: block; margin: 8px 0; }}
input {{ font: inherit; padding: 8px; }}
.banner {{ background: #fff4d6; padding: 8px 12px; border-radius: 8px; }}
form {{ margin: 0; }}
</style></head><body>
<header><strong>Manual sales</strong><a href="/admin">Back to admin</a></header>
<main>
<input type="hidden" name="csrf_token" value="{html.escape(csrf)}">
{banner}
<section class="card" id="manual-cap">
  <h1>Manual sellers</h1>
  <p id="approved-count">Approved manual sellers: {count}. The cap is {cap}.</p>
  <p>Creatrs cannot see a manual seller until you approve them and manual reporting is live.</p>
  <table>
    <thead><tr><th>Seller</th><th>Status</th><th></th></tr></thead>
    <tbody>{waiting_body}</tbody>
  </table>
</section>
<section class="card" id="pending-reports">
  <h1>Sales reports to review</h1>
  <table>
    <thead><tr><th>Seller</th><th>Month</th><th>What was sent</th><th>Received</th><th></th></tr></thead>
    <tbody>{report_body}</tbody>
  </table>
</section>
</main></body></html>"""
