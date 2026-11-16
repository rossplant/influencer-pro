"""Server-rendered operator pages.

The JSON routes under /admin/api stay as they are. These pages use the same
admin session, the same form token, and the same session rate limit.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select

import admin_data
import admin_format
import attribution
import auth
import billing_config
import catalogue
import db
import events
import password_reset
import portal
from models import Brief, PilotApplication, ShopifyWebhookReceipt, StripeEvent

ROOT = Path(__file__).resolve().parent
PAGE_SIZE = 20
NOTICES = {
    "created": "Sign-in created. The password is not shown here.",
    "reset": "Password updated. It is not shown here.",
    "mismatch": "Type the name exactly as shown, then try again. Nothing was changed.",
    "short": "Use at least 12 characters and enter it twice. Nothing was changed.",
    "reason": "Add a short reason for opening this view. Nothing was opened.",
    "taken": "That email is already used by another account. Choose a different sign-in email. Nothing was changed.",
    "email": "Enter a sign-in email. Nothing was changed.",
    "csrf": "This form expired. Reload the page and try again. Nothing was changed.",
    "approved": "Application approved.",
    "rejected": "Application declined.",
    "link": "One-time link created. It works once, expires in 24 hours, and is not emailed. Copy it now.",
    "unavailable": "That test account is not available.",
}

NAV = (
    ("dashboard", "/admin", "Dashboard"),
    ("sellers", "/admin/sellers", "Sellers"),
    ("creators", "/admin/creators", "Creatrs"),
    ("products", "/admin/products", "Products"),
    ("collabs", "/admin/collabs", "Collabs"),
    ("sales", "/admin/sales", "Sales & commission"),
    ("applications", "/admin/applications", "Applications"),
    ("settings", "/admin/settings", "Settings"),
)
SUB = {
    "collabs": (
        ("/admin/collabs/shortlists", "Shortlists"),
        ("/admin/collabs/matches", "Matches"),
        ("/admin/collabs/briefs", "Briefs"),
        ("/admin/collabs/links", "Links"),
    ),
    "sales": (
        ("/admin/sales/orders", "Orders"),
        ("/admin/sales/statements", "Statements"),
        ("/admin/sales/corrections", "Corrections"),
        ("/admin/sales/disputes", "Disputes"),
    ),
}
SKIP_EVENTS = {"admin_login_succeeded", "admin_login_failed"}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def names_match(provided: str, expected: str) -> bool:
    if not (expected or "").strip():
        return False
    return hmac.compare_digest(
        hashlib.sha256((provided or "").strip().encode()).digest(),
        hashlib.sha256(expected.strip().encode()).digest(),
    )


def operator(request: Request) -> Optional[str]:
    import main

    if not main._admin_available():
        raise HTTPException(status_code=503, detail="Admin area is disabled")
    email = auth.session_email(request)
    if not email:
        return None
    main.enforce_rate_limit(f"session:{email}")
    return email


def signed_out():
    return HTMLResponse(login_page("Sign in to open the admin."), status_code=401)


def notice_from(request: Request) -> str:
    return NOTICES.get(request.query_params.get("notice") or "", "")


def plain_error(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, str) else ""
    lower = detail.lower()
    if "traceback" in lower or "<" in detail:
        return "That didn't work. Check the form and try again. Nothing was changed."
    if "12" in lower or "confirmation" in lower or "same password" in lower:
        return NOTICES["short"]
    if "already used" in lower:
        return NOTICES["taken"]
    if "email" in lower:
        return NOTICES["email"]
    if "csrf" in lower:
        return NOTICES["csrf"]
    if detail:
        return detail if detail.endswith(".") else detail + "."
    return "That didn't work. Check the form and try again. Nothing was changed."


def login_page(message: str = "") -> str:
    badge = "<span class='env-badge'>STAGING</span>" if admin_format.show_staging_badge() else ""
    error = f"<p class='notice bad' role='alert'>{esc(message)}</p>" if message else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plantr Collab admin</title>
<link rel="stylesheet" href="/assets/plantr.css">
</head><body class="app app-admin">
<header class="top"><div class="brand"><a class="logo" href="/admin"><svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><circle cx="12" cy="16" r="8" fill="none" stroke="#0D0B18" stroke-width="2.4"/><circle cx="20" cy="16" r="8" fill="none" stroke="#5B2EFF" stroke-width="2.4"/></svg><strong>Plantr</strong> <span class="collab">Collab</span></a>{badge}</div></header>
<main class="login card">
<h1>Log in</h1>
{error}
<p>First time? <a href="/admin/setup">Set the password</a>.</p>
<form method="post" action="/admin/login">
<label for="email">Email</label>
<input id="email" name="email" type="email" autocomplete="username" required>
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Log in</button>
</form>
</main></body></html>"""


def layout(title: str, body: str, *, email: str, active: str, notice: str = "") -> str:
    token = esc(auth.issue_form_token("admin"))
    badge = "<span class='env-badge'>STAGING</span>" if admin_format.show_staging_badge() else ""
    links = []
    for key, href, label in NAV:
        current = " aria-current='page'" if key == active else ""
        links.append(f"<a href='{href}'{current}>{esc(label)}</a>")
        for sub_href, sub_label in SUB.get(key, ()):
            links.append(f"<a class='sub' href='{sub_href}'>{esc(sub_label)}</a>")
    alert = f"<p class='notice' role='status'>{esc(notice)}</p>" if notice else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf-token" content="{token}">
<title>{esc(title)} — Plantr Collab admin</title>
<link rel="stylesheet" href="/assets/plantr.css">
</head><body class="app app-admin">
<a class="skip" href="#content">Skip to content</a>
<header class="top">
<div class="brand"><a class="logo" href="/admin"><svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><circle cx="12" cy="16" r="8" fill="none" stroke="#0D0B18" stroke-width="2.4"/><circle cx="20" cy="16" r="8" fill="none" stroke="#5B2EFF" stroke-width="2.4"/></svg><strong>Plantr</strong> <span class="collab">Collab</span></a>{badge}</div>
<form class="search" method="get" action="/admin/search" role="search">
<label for="global-q">Search</label>
<input id="global-q" name="q" type="search" placeholder="Name, email, handle, ABN">
<button type="submit">Search</button>
</form>
<div class="who"><span>Signed in as {esc(email)}</span>
<form method="post" action="/admin/logout"><button class="secondary" type="submit">Log out</button></form>
</div>
</header>
<p class="zone">Times are Australia/Sydney.</p>
<div class="shell">
<nav class="side" aria-label="Admin">{''.join(links)}</nav>
<main class="content" id="content">
{alert}
{body}
</main>
</div>
<script>
document.querySelectorAll("tr[data-href]").forEach((row) => {{
  row.addEventListener("click", (event) => {{
    if (event.target.closest("a, button, input, select, textarea, label")) return;
    window.location = row.getAttribute("data-href");
  }});
}});
document.querySelectorAll("[data-copy]").forEach((button) => {{
  button.addEventListener("click", async () => {{
    try {{
      await navigator.clipboard.writeText(button.getAttribute("data-copy") || "");
      button.textContent = "Copied";
    }} catch (error) {{
      button.textContent = "Copy failed";
    }}
  }});
}});
</script>
</body></html>"""


def page(request: Request, email: str, active: str, title: str, body: str, *, status: int = 200, notice: str = "") -> HTMLResponse:
    text = notice or notice_from(request)
    return HTMLResponse(layout(title, body, email=email, active=active, notice=text), status_code=status)


def home(request: Request):
    email = operator(request)
    if not email:
        return HTMLResponse(login_page())
    return page(request, email, "dashboard", "Dashboard", dashboard_body())


def missing(request: Request, email: str, active: str, back: str, label: str):
    body = f"<h1>{esc(label)}</h1><p class='empty'>That record is not here. <a href='{esc(back)}'>Back to the list</a>.</p>"
    return page(request, email, active, label, body, status=404)


# ---------------------------------------------------------------- data


def _copy_application(row) -> dict:
    def get(name):
        return row.get(name) if isinstance(row, dict) else getattr(row, name, None)

    return {
        "id": get("id"),
        "cohort": get("cohort_slug"),
        "name": get("name") or "",
        "business_name": get("business_name") or "",
        "email": get("email") or "",
        "role": get("role") or "",
        "status": get("status") or "",
        "abn": get("abn") or "",
        "categories": get("categories") or "",
        "contact": get("contact") or "",
        "created_at": get("created_at"),
        "reviewed_at": get("reviewed_at"),
    }


def load_applications() -> list[dict]:
    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(PilotApplication).order_by(PilotApplication.created_at.desc())).all()
            return [_copy_application(row) for row in rows]
    return [_copy_application(row) for row in db.memory_applications]


def load_accounts() -> list[dict]:
    from models import PortalAccount

    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(PortalAccount)).all()
            return [
                {"role": row.role, "email": row.email, "shop_id": row.shop_id, "creator_id": row.creator_id}
                for row in rows
            ]
    return [
        {"role": row.role, "email": row.email, "shop_id": row.shop_id, "creator_id": row.creator_id}
        for row in db.memory_portal_accounts
    ]


def load_products() -> list[dict]:
    from models import Product

    def pack(row) -> dict:
        price = getattr(row, "price", None)
        rate = getattr(row, "commission_rate", None)
        return {
            "id": row.id,
            "shop_id": getattr(row, "shop_id", None),
            "title": getattr(row, "title", None) or "Untitled product",
            "category": getattr(row, "category", None),
            "price": float(price) if price is not None else None,
            "currency": getattr(row, "currency", None) or "AUD",
            "commission_rate": float(rate) if rate is not None else None,
            "why_chosen": getattr(row, "why_chosen", None) or "",
            "value_to_creators": getattr(row, "value_to_creators", None) or "",
            "url": catalogue.product_public_url(row),
            "source": getattr(row, "source", None),
            "created_at": getattr(row, "created_at", None),
            "complete": catalogue.product_is_complete(row),
        }

    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Product).order_by(Product.created_at.desc())).all()
            return [pack(row) for row in rows]
    rows = list(db.memory_products.values())
    rows.sort(key=lambda row: str(getattr(row, "created_at", "")), reverse=True)
    return [pack(row) for row in rows]


def load_briefs() -> list[dict]:
    def pack(row) -> dict:
        return {
            "id": row.id,
            "shop_id": getattr(row, "shop_id", None),
            "product_id": getattr(row, "product_id", None),
            "creator_id": getattr(row, "creator_id", None),
            "source": getattr(row, "source", None),
            "sent_at": getattr(row, "sent_at", None),
            "created_at": getattr(row, "created_at", None),
        }

    if db.configured():
        with db.session_scope() as session:
            rows = session.scalars(select(Brief).order_by(Brief.created_at.desc())).all()
            return [pack(row) for row in rows]
    rows = list(db.memory_briefs)
    rows.sort(key=lambda row: str(getattr(row, "created_at", "")), reverse=True)
    return [pack(row) for row in rows]


def load_statements(**filters) -> list[dict]:
    if not db.configured():
        return []
    try:
        import commission

        return commission.list_statements(viewer="admin", **filters)
    except HTTPException:
        return []


def load_corrections(**filters) -> list[dict]:
    if not db.configured():
        return []
    try:
        import commission

        return commission.list_corrections(**filters)
    except HTTPException:
        return []


def load_audit(shop_id: Optional[str] = None) -> list[dict]:
    if not db.configured() or not shop_id:
        return []
    try:
        import commission

        return commission.audit_log(shop_id=shop_id)
    except HTTPException:
        return []


class Directory:
    def __init__(self):
        self.shops = {row["id"]: row for row in admin_data._shops()}
        self.creators = {row["id"]: row for row in admin_data._creators(full=True)}
        self.products = {row["id"]: row for row in load_products()}
        self.accounts = load_accounts()
        self.by_shop = {row["shop_id"]: row for row in self.accounts if row["role"] == "seller" and row["shop_id"]}
        self.by_creator = {
            row["creator_id"]: row for row in self.accounts if row["role"] == "creator" and row["creator_id"]
        }
        for shop in self.shops.values():
            account = self.by_shop.get(shop["id"])
            shop["sign_in_email"] = account["email"] if account else ""
        for creator in self.creators.values():
            account = self.by_creator.get(creator["id"])
            creator["sign_in_email"] = account["email"] if account else ""

    def seller_name(self, shop_id: Optional[str]) -> str:
        shop = self.shops.get(shop_id or "")
        if shop is None:
            return "Seller"
        return _text(shop.get("business_name") or shop.get("name"), "Seller")

    def creator_name(self, creator_id: Optional[str]) -> str:
        creator = self.creators.get(creator_id or "")
        if creator is None:
            return "Creatr"
        handle = _text(creator.get("handle"), "")
        name = _text(creator.get("name"), "")
        if handle and name:
            return f"{handle} ({name})"
        return handle or name or "Creatr"

    def product_name(self, product_id: Optional[str]) -> str:
        product = self.products.get(product_id or "")
        if product is None:
            return "Product"
        return _text(product.get("title"), "Product")


def _text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or admin_format.is_uuid(text):
        return fallback
    return text


def badge(code: Any) -> str:
    label = admin_format.status_label(code)
    tone = admin_format.badge_tone(code)
    return f"<span class='badge {esc(tone)}'>{esc(label)}</span>"


def money(amount, currency="AUD", kind="") -> str:
    return esc(admin_format.money(amount, currency, kind=kind))


def when(value) -> str:
    return esc(admin_format.format_when(value))


def person_link(directory: Directory, kind: str, subject_id: Optional[str]) -> str:
    if kind == "seller":
        return f"<a href='/admin/sellers/{esc(subject_id)}'>{esc(directory.seller_name(subject_id))}</a>"
    if kind == "creator":
        return f"<a href='/admin/creators/{esc(subject_id)}'>{esc(directory.creator_name(subject_id))}</a>"
    return f"<a href='/admin/products/{esc(subject_id)}'>{esc(directory.product_name(subject_id))}</a>"


def technical(pairs: list[tuple[str, Any]]) -> str:
    rows = []
    for label, value in pairs:
        if not value:
            continue
        shown = esc(value)
        rows.append(
            f"<div><span>{esc(label)}</span> <code>{shown}</code> "
            f"<button type='button' class='secondary' data-copy='{shown}'>Copy</button></div>"
        )
    if not rows:
        return ""
    return "<details class='technical'><summary>Technical details</summary>" + "".join(rows) + "</details>"


def external(url: Any) -> str:
    text = str(url or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return f"<a href='{esc(text)}' rel='noreferrer'>{esc(text)}</a>"
    return esc(text or "—")


# ---------------------------------------------------------------- lists


def query_state(request: Request, default_sort: str):
    params = request.query_params
    try:
        page_no = max(1, int(params.get("page") or "1"))
    except ValueError:
        page_no = 1
    direction = "asc" if params.get("dir") == "asc" else "desc"
    return (params.get("q") or "").strip(), (params.get("status") or "").strip(), (params.get("sort") or default_sort).strip(), direction, page_no


def sort_rows(rows: list[dict], key: str, direction: str, allowed: set[str], default: str) -> list[dict]:
    chosen = key if key in allowed else default

    def value(row):
        item = row.get(chosen)
        if item is None or item == "":
            return (1, "")
        if isinstance(item, (int, float)):
            return (0, item)
        return (0, str(item).lower())

    ordered = sorted(rows, key=value)
    if direction == "desc":
        ordered.reverse()
    return ordered


def href_with(path: str, params: dict) -> str:
    cleaned = {key: value for key, value in params.items() if value not in (None, "")}
    query = urlencode(cleaned)
    return esc(path + ("?" + query if query else ""))


def filters_html(path: str, q: str, status: str, options: list[tuple[str, str]]) -> str:
    choices = ["<option value=''>Any status</option>"]
    for value, label in options:
        selected = " selected" if value == status else ""
        choices.append(f"<option value='{esc(value)}'{selected}>{esc(label)}</option>")
    return f"""<form class="filters" method="get" action="{esc(path)}">
<label for="q">Search</label>
<input id="q" name="q" type="search" value="{esc(q)}">
<label for="status">Status</label>
<select id="status" name="status">{''.join(choices)}</select>
<button type="submit">Apply</button>
<a href="{esc(path)}">Clear</a>
</form>"""


def table_html(request: Request, path: str, columns: list[tuple[str, str]], rows: list[dict], page_no: int, total: int) -> str:
    params = {key: request.query_params.get(key) or "" for key in ("q", "status", "sort", "dir")}
    heads = []
    for key, label in columns:
        current = params.get("sort") == key
        direction = params.get("dir") or "desc"
        nxt = "asc" if current and direction == "desc" else "desc"
        link = href_with(path, {**params, "sort": key, "dir": nxt, "page": "1"})
        heads.append(f"<th><a href='{link}'>{esc(label)}</a></th>")
    body = []
    for row in rows:
        href = esc(row.get("href") or "")
        cells = "".join(f"<td>{cell}</td>" for cell in row["cells"])
        body.append(f"<tr data-href='{href}'><td><a href='{href}'>{row['cells'][0]}</a></td>{''.join(f'<td>{cell}</td>' for cell in row['cells'][1:])}</tr>")
        # The line above duplicates the first cell. Rebuild cleanly.
        body[-1] = f"<tr data-href='{href}'>{cells}</tr>"
    # The first cell is expected to contain its own link.
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    pager = ""
    if pages > 1:
        parts = [f"<span>Page {page_no} of {pages}</span>"]
        if page_no > 1:
            parts.insert(0, f"<a href='{href_with(path, {**params, 'page': page_no - 1})}'>Previous</a>")
        if page_no < pages:
            parts.append(f"<a href='{href_with(path, {**params, 'page': page_no + 1})}'>Next</a>")
        pager = "<nav class='pager' aria-label='Pages'>" + "".join(parts) + "</nav>"
    return "<div class='table-wrap card'><table><thead><tr>" + "".join(heads) + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>" + pager


def render_list(
    request: Request,
    email: str,
    *,
    active: str,
    title: str,
    path: str,
    rows: list[dict],
    columns: list[tuple[str, str]],
    search_keys: tuple[str, ...],
    status_key: str,
    options: list[tuple[str, str]],
    empty: str,
    default_sort: str,
    intro: str = "",
) -> HTMLResponse:
    q, status, sort, direction, page_no = query_state(request, default_sort)
    allowed = {key for key, _label in columns}
    filtered = rows
    if q:
        needle = q.lower()
        filtered = [row for row in filtered if needle in " ".join(str(row.get(key) or "") for key in search_keys).lower()]
    if status:
        filtered = [row for row in filtered if row.get(status_key) == status]
    if not rows:
        message = empty
    elif not filtered:
        message = "Nothing matches. Clear the search or filter."
    else:
        message = ""
    ordered = sort_rows(filtered, sort, direction, allowed, default_sort)
    total = len(ordered)
    start = (page_no - 1) * PAGE_SIZE
    visible = ordered[start : start + PAGE_SIZE]
    intro_html = f"<p>{esc(intro)}</p>" if intro else ""
    body = f"<h1>{esc(title)}</h1>{intro_html}" + filters_html(path, q, status, options)
    if message:
        body += f"<p class='empty'>{esc(message)}</p>"
    else:
        body += table_html(request, path, columns, visible, page_no, total)
    return page(request, email, active, title, body)


def cell_link(href: str, label: str) -> str:
    return f"<a href='{esc(href)}'>{esc(label)}</a>"


# ---------------------------------------------------------------- dashboard


def attention_items(directory: Directory) -> list[dict]:
    items = []
    now = datetime.now(timezone.utc)

    def add(score: int, when_value, text: str, action: str, href: str):
        moment = admin_format._as_datetime(when_value)
        age = 0
        if moment is not None:
            age = int((now - moment).total_seconds())
        items.append({"score": (score, -age), "text": text, "action": action, "href": href})

    for shop in directory.shops.values():
        if str(shop.get("subscription_status") or "").lower() == "past_due":
            name = directory.seller_name(shop["id"])
            add(10, shop.get("created_at"), f"Payment overdue for {name}.", "Open seller", f"/admin/sellers/{shop['id']}")
    for creator in directory.creators.values():
        if str(creator.get("subscription_status") or "").lower() == "past_due":
            add(10, creator.get("created_at"), f"Payment overdue for {directory.creator_name(creator['id'])}.", "Open Creatr", f"/admin/creators/{creator['id']}")
    for product in directory.products.values():
        if not product.get("complete"):
            add(
                60,
                product.get("created_at"),
                f"{directory.product_name(product['id'])} from {directory.seller_name(product.get('shop_id'))} is incomplete, {admin_format.waiting_phrase(product.get('created_at'), now=now)}.",
                "Open product",
                f"/admin/products/{product['id']}",
            )
    for application in load_applications():
        if application.get("status") == "pending":
            who = _text(application.get("business_name") or application.get("name"), "Someone")
            add(
                40,
                application.get("created_at"),
                f"Application from {who}, {admin_format.waiting_phrase(application.get('created_at'), now=now)}.",
                "Review",
                f"/admin/applications/{application['id']}",
            )
    statements = load_statements()
    by_statement = {row.get("id"): row for row in statements}
    try:
        import commission

        resolve_after = commission.dispute_resolve_after_days()
    except Exception:
        resolve_after = 14
    for statement in statements:
        seller = directory.seller_name(statement.get("shop_id"))
        creator = statement.get("creator_handle") or directory.creator_name(statement.get("creator_id"))
        display = statement.get("display") or {}
        if display.get("code") == "overdue" or (
            statement.get("status") == "open" and admin_format.due_phrase(statement.get("overdue_on"), now=now).endswith("overdue")
        ):
            add(
                20,
                statement.get("overdue_on"),
                f"Statement for {seller} and {creator} is overdue, {admin_format.due_phrase(statement.get('overdue_on'), now=now)} ({admin_format.format_when(statement.get('overdue_on'))}).",
                "Open statement",
                f"/admin/sales/statements/{statement['id']}",
            )
        if statement.get("status") == "disputed":
            opened = statement.get("disputed_at")
            deadline = None
            moment = admin_format._as_datetime(opened)
            if moment is not None:
                deadline = moment + timedelta(days=resolve_after)
            due = admin_format.due_phrase(deadline, now=now) if deadline else ""
            add(
                30,
                opened,
                f"Dispute from {creator} about {seller}, {admin_format.waiting_phrase(opened, now=now)}"
                + (f", {due}" if due else "")
                + ".",
                "Review",
                f"/admin/sales/statements/{statement['id']}",
            )
    for correction in load_corrections(status="disagreed"):
        statement = by_statement.get(correction.get("statement_id")) or {}
        seller = directory.seller_name(statement.get("shop_id"))
        creator = statement.get("creator_handle") or directory.creator_name(statement.get("creator_id"))
        add(
            35,
            correction.get("requested_at"),
            f"{seller} asked to correct a payment for {creator}, {admin_format.waiting_phrase(correction.get('requested_at'), now=now)}.",
            "Review",
            f"/admin/sales/corrections/{correction['id']}",
        )
    for failure in failed_signals(directory):
        add(70, failure.get("at"), failure["text"], "Review", "/admin/settings#signals")
    items.sort(key=lambda row: row["score"])
    return items[:50]


def failed_signals(directory: Directory) -> list[dict]:
    found = []
    if db.configured():
        with db.session_scope() as session:
            receipts = session.scalars(select(ShopifyWebhookReceipt).order_by(ShopifyWebhookReceipt.received_at.desc())).all()
            for row in receipts:
                outcome = (row.outcome or "").lower()
                if "fail" in outcome or "error" in outcome:
                    found.append(
                        {
                            "at": row.received_at,
                            "text": f"A Shopify webhook for {row.shop_domain or 'a store'} did not succeed ({admin_format.humanize(row.outcome)}), {admin_format.relative_age(row.received_at)}.",
                        }
                    )
            stripes = session.scalars(select(StripeEvent).order_by(StripeEvent.created_at.desc())).all()
            for row in stripes:
                if "fail" in (row.event_type or "").lower():
                    found.append({"at": row.created_at, "text": f"A Stripe payment event failed, {admin_format.relative_age(row.created_at)}."})
    else:
        for event in db.memory_stripe_events.values():
            if "fail" in str(event.get("event_type") or "").lower():
                found.append({"at": None, "text": "A Stripe payment event failed."})
    return found[:20]


def dashboard_body() -> str:
    directory = Directory()
    summary = admin_data.summary()
    cards = (
        ("Sellers", summary.get("shops", 0), "/admin/sellers"),
        ("Creatrs", summary.get("creators", 0), "/admin/creators"),
        ("Products", summary.get("products", 0), "/admin/products"),
        ("Shortlists", summary.get("shortlists", 0), "/admin/collabs/shortlists"),
        ("Accepted matches", summary.get("accepted_matches", 0), "/admin/collabs/matches"),
        ("Briefs sent", summary.get("briefs_sent", 0), "/admin/collabs/briefs"),
    )
    counts = "".join(
        f"<div class='count'><a href='{href}'><strong>{esc(count)}</strong>{esc(label)}</a></div>"
        for label, count, href in cards
    )
    items = attention_items(directory)
    if items:
        rows = "".join(
            f"<li><p>{esc(item['text'])}</p><a class='button' href='{esc(item['href'])}'>{esc(item['action'])}</a></li>"
            for item in items
        )
        attention = f"<ul class='attention'>{rows}</ul>"
    else:
        attention = "<p class='empty'>Nothing needs attention.</p>"
    tools = test_accounts_card(directory)
    return f"""<section class="hero"><p class="eyebrow">Operator</p><h2>Dashboard</h2></section>
<p>Open a seller or Creatr and use Create login or View as on the Account card.</p>
<div class="counts">{counts}</div>
<section class="card"><h2>Needs attention</h2>{attention}</section>
{tools}"""


def test_accounts_card(directory: Directory) -> str:
    if not admin_format.show_test_tools():
        return ""
    token = esc(auth.issue_form_token("admin"))
    blocks = ["<section class='card'><h2>Test accounts</h2><p>One-click access for the staging test seller and Creatr.</p>"]
    blocks.append(_test_block("seller", os.getenv("TEST_SELLER_SHOP_ID", "").strip(), directory, token))
    blocks.append(_test_block("creator", os.getenv("TEST_CREATOR_ID", "").strip(), directory, token))
    blocks.append("</section>")
    return "".join(blocks)


def _test_block(kind: str, subject_id: str, directory: Directory, token: str) -> str:
    noun = "seller" if kind == "seller" else "Creatr"
    env_name = "TEST_SELLER_SHOP_ID" if kind == "seller" else "TEST_CREATOR_ID"
    if not subject_id:
        return f"<p>No test {noun} is selected. {esc(env_name)} is not set.</p>"
    record = directory.shops.get(subject_id) if kind == "seller" else directory.creators.get(subject_id)
    if record is None:
        return f"<p>The selected test {noun} is not on this list.</p>"
    name = directory.seller_name(subject_id) if kind == "seller" else directory.creator_name(subject_id)
    return f"""<div>
<p><strong>{esc(name)}</strong></p>
<form method="post" action="/admin/test-accounts/{esc(kind)}/view-as">
<input type="hidden" name="csrf_token" value="{token}">
<input type="hidden" name="subject_id" value="{esc(subject_id)}">
<button type="submit">View as</button>
</form>
<form method="post" action="/admin/test-accounts/{esc(kind)}/reset-link">
<input type="hidden" name="csrf_token" value="{token}">
<input type="hidden" name="subject_id" value="{esc(subject_id)}">
<button class="secondary" type="submit">Create one-time reset link</button>
</form>
</div>"""


# ---------------------------------------------------------------- settings


def checklist() -> list[dict]:
    mode = admin_format.stripe_mode()
    if mode == "test":
        stripe = (False, "Stripe is in test mode. Live payments are not on.")
    elif mode == "live":
        stripe = (False, "A live key is present, and this app refuses to use it. Live payments are not on.")
    elif mode == "missing":
        stripe = (False, "Stripe is not configured. Live payments are not on.")
    else:
        stripe = (False, "Stripe is not a recognised test or live key. Live payments are not on.")
    import mailer

    mail = mailer.ResendMailer.from_env()
    email = (True, "Email sending is on.") if mail.ready else (False, "Email is logged only. Nothing is sent.")
    import shopify_sync

    shopify = (
        (True, "The Shopify app is configured.")
        if shopify_sync.app_configured()
        else (False, "The Shopify app is not configured. A seller can still connect store webhooks.")
    )
    legal_name = billing_config.operator_legal_name()
    operator_row = (
        (True, f"Operator legal name is set ({legal_name}).")
        if legal_name
        else (False, "Operator legal name is not set. Applications stay closed until it is.")
    )
    terms_url = os.getenv("TERMS_URL", "").strip()
    terms = (
        (True, "A public terms address is set.")
        if terms_url.startswith("https://")
        else (False, "Platform terms are not hosted on a public page. The privacy page is available.")
    )
    setup_open = bool(os.getenv("ADMIN_SETUP_TOKEN", "").strip())
    exists = admin_data.admin_exists(auth.admin_email())
    if exists and not setup_open:
        setup = (True, "Admin setup is closed.")
    elif exists:
        setup = (False, "The operator account exists, but setup is still open. Remove the setup token.")
    else:
        setup = (False, "Admin setup is not finished.")
    rows = [
        ("Stripe live payments", *stripe),
        ("Email sending", *email),
        ("Shopify app", *shopify),
        ("Operator legal name", *operator_row),
        ("Terms hosted", *terms),
        ("Admin setup closed", *setup),
    ]
    return [{"title": title, "ready": ready, "line": line} for title, ready, line in rows]


def settings_body() -> str:
    checks = []
    for row in checklist():
        state = "ready" if row["ready"] else "not-ready"
        mark = "Ready" if row["ready"] else "Not ready"
        checks.append(
            f"<li class='{state}'><span class='mark'>{mark}</span> <strong>{esc(row['title'])}</strong><p>{esc(row['line'])}</p></li>"
        )
    seller_prices = []
    for spec in billing_config.SELLER_TIERS.values():
        seller_prices.append(
            f"<li>{esc(spec['label'])}: {money(spec['amount_month_cents'] / 100, kind='a month, ex GST')} "
            f"or {money(spec['amount_year_cents'] / 100, kind='a year, ex GST')}</li>"
        )
    creator_prices = []
    for spec in billing_config.CREATOR_TIERS.values():
        creator_prices.append(
            f"<li>{esc(spec['label'])}: {money(spec['amount_month_cents'] / 100, kind='a month, ex GST')} "
            f"or {money(spec['amount_year_cents'] / 100, kind='a year, ex GST')}</li>"
        )
    mode = {"test": "Test", "live": "Live", "missing": "Not configured"}.get(admin_format.stripe_mode(), "Not recognised")
    env = admin_format.humanize(admin_format.deployment_name())
    return f"""<h1>Launch readiness</h1>
<ul class="checks" id="signals">{''.join(checks)}</ul>
<section class="card"><h2>Configuration</h2>
<p>Environment: {esc(env)}</p>
<p>Stripe mode: {esc(mode)}</p>
<p>Catalogue fee warning after {esc(catalogue.catalogue_fee_threshold())} products. Nothing is charged.</p>
<p>Pilot: {when(billing_config.pilot_start())} to {when(billing_config.pilot_end())}.</p>
<h3>Seller plans</h3><ul>{''.join(seller_prices)}</ul>
<h3>Creatr plans</h3><ul>{''.join(creator_prices)}</ul>
</section>"""


def settings_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    return page(request, email, "settings", "Launch readiness", settings_body())


# ---------------------------------------------------------------- search


def search_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    q = (request.query_params.get("q") or "").strip()
    directory = Directory()
    hits = []
    if q:
        needle = q.lower()

        def matches(*parts) -> bool:
            return needle in " ".join(str(part or "") for part in parts).lower()

        for shop in directory.shops.values():
            if matches(shop.get("name"), shop.get("business_name"), shop.get("abn"), shop.get("sign_in_email"), shop.get("id")):
                hits.append(cell_link(f"/admin/sellers/{shop['id']}", directory.seller_name(shop["id"])) + " <span class='muted'>Seller</span>")
        for creator in directory.creators.values():
            profile = creator.get("profile") or {}
            if matches(creator.get("name"), creator.get("handle"), creator.get("sign_in_email"), profile.get("abn"), creator.get("id")):
                hits.append(cell_link(f"/admin/creators/{creator['id']}", directory.creator_name(creator["id"])) + " <span class='muted'>Creatr</span>")
        for product in directory.products.values():
            if matches(product.get("title"), product.get("id")):
                hits.append(cell_link(f"/admin/products/{product['id']}", directory.product_name(product["id"])) + " <span class='muted'>Product</span>")
        for application in load_applications():
            if matches(application.get("name"), application.get("business_name"), application.get("email"), application.get("abn")):
                label = _text(application.get("business_name") or application.get("name"), "Application")
                hits.append(cell_link(f"/admin/applications/{application['id']}", label) + " <span class='muted'>Application</span>")
    if not q:
        body = "<h1>Search</h1><p class='empty'>Type a name, email, handle, ABN, or business name.</p>"
    elif not hits:
        body = f"<h1>Search</h1><p class='empty'>Nothing matches {esc(q)}.</p>"
    else:
        rows = "".join(f"<li>{hit}</li>" for hit in hits[:50])
        body = f"<h1>Search</h1><ul>{rows}</ul>"
    return page(request, email, "dashboard", "Search", body)


# ---------------------------------------------------------------- people


def seller_rows(directory: Directory) -> list[dict]:
    rows = []
    for shop in directory.shops.values():
        sub = str(shop.get("subscription_status") or "").lower()
        if sub == "past_due":
            code = "payment-overdue"
        elif sub == "trialing":
            code = "trial"
        elif sub == "active":
            code = "active"
        elif shop.get("is_pilot"):
            code = "pilot"
        else:
            code = "not-subscribed"
        name = directory.seller_name(shop["id"])
        rows.append(
            {
                "name": name,
                "plan": admin_format.plan_name("seller", shop.get("plan_tier")),
                "status": code,
                "category": admin_format.category_name(shop.get("primary_category")),
                "created": _iso(shop.get("created_at")),
                "blob": " ".join(str(shop.get(key) or "") for key in ("name", "business_name", "abn", "sign_in_email", "id")),
                "href": f"/admin/sellers/{shop['id']}",
                "cells": [
                    cell_link(f"/admin/sellers/{shop['id']}", name),
                    esc(admin_format.plan_name("seller", shop.get("plan_tier"))),
                    badge(sub or shop.get("plan_status") or "unsubscribed"),
                    esc(admin_format.category_name(shop.get("primary_category"))),
                    when(shop.get("created_at")),
                ],
            }
        )
    return rows


def creator_rows(directory: Directory) -> list[dict]:
    rows = []
    for creator in directory.creators.values():
        sub = str(creator.get("subscription_status") or "").lower()
        if sub == "past_due":
            code = "payment-overdue"
        elif sub == "trialing":
            code = "trial"
        elif sub == "active":
            code = "active"
        else:
            code = "not-subscribed"
        label = directory.creator_name(creator["id"])
        platforms = ", ".join(admin_format.humanize(item) for item in (creator.get("platforms") or []) if not isinstance(item, dict)) or "—"
        rows.append(
            {
                "name": label,
                "plan": admin_format.plan_name("creator", creator.get("plan_tier")),
                "status": code,
                "platforms": platforms,
                "created": _iso(creator.get("created_at")),
                "blob": " ".join(
                    [
                        str(creator.get("name") or ""),
                        str(creator.get("handle") or ""),
                        str(creator.get("sign_in_email") or ""),
                        str((creator.get("profile") or {}).get("abn") or ""),
                        str(creator.get("id") or ""),
                    ]
                ),
                "href": f"/admin/creators/{creator['id']}",
                "cells": [
                    cell_link(f"/admin/creators/{creator['id']}", label),
                    esc(admin_format.plan_name("creator", creator.get("plan_tier"))),
                    badge(sub or creator.get("plan_status") or "invited"),
                    esc(platforms),
                    when(creator.get("created_at")),
                ],
            }
        )
    return rows


def _iso(value) -> str:
    moment = admin_format._as_datetime(value)
    return "" if moment is None else moment.isoformat()


SELLER_OPTIONS = (
    ("payment-overdue", "Payment overdue"),
    ("active", "Active"),
    ("trial", "Trial"),
    ("pilot", "Pilot"),
    ("not-subscribed", "Not subscribed"),
)
CREATOR_OPTIONS = (
    ("payment-overdue", "Payment overdue"),
    ("active", "Active"),
    ("trial", "Trial"),
    ("not-subscribed", "Not subscribed"),
)


def sellers_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    return render_list(
        request,
        email,
        active="sellers",
        title="Sellers",
        path="/admin/sellers",
        rows=seller_rows(directory),
        columns=(("name", "Business"), ("plan", "Plan"), ("status", "Status"), ("category", "Category"), ("created", "Joined")),
        search_keys=("blob",),
        status_key="status",
        options=list(SELLER_OPTIONS),
        empty="No sellers yet. Approved applications appear here.",
        default_sort="created",
    )


def creators_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    return render_list(
        request,
        email,
        active="creators",
        title="Creatrs",
        path="/admin/creators",
        rows=creator_rows(Directory()),
        columns=(("name", "Creatr"), ("plan", "Plan"), ("status", "Status"), ("platforms", "Platforms"), ("created", "Joined")),
        search_keys=("blob",),
        status_key="status",
        options=list(CREATOR_OPTIONS),
        empty="No Creatrs yet. Approved applications appear here.",
        default_sort="created",
    )


def seller_page(request: Request, shop_id: str, *, notice: str = "", reset_url: str = "", status: int = 200):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    shop = directory.shops.get(shop_id)
    if shop is None:
        return missing(request, email, "sellers", "/admin/sellers", "Seller")
    return page(request, email, "sellers", directory.seller_name(shop_id), seller_body(directory, shop, reset_url), notice=notice, status=status)


def creator_page(request: Request, creator_id: str, *, notice: str = "", reset_url: str = "", status: int = 200):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    creator = directory.creators.get(creator_id)
    if creator is None:
        return missing(request, email, "creators", "/admin/creators", "Creatr")
    return page(
        request,
        email,
        "creators",
        directory.creator_name(creator_id),
        creator_body(directory, creator, reset_url),
        notice=notice,
        status=status,
    )


def seller_body(directory: Directory, shop: dict, reset_url: str) -> str:
    name = directory.seller_name(shop["id"])
    sub = shop.get("subscription_status") or shop.get("plan_status") or "unsubscribed"
    price = _plan_price("seller", shop.get("plan_tier"))
    products = [row for row in directory.products.values() if row.get("shop_id") == shop["id"]]
    product_rows = "".join(
        "<tr><td>"
        + cell_link(f"/admin/products/{row['id']}", directory.product_name(row["id"]))
        + "</td><td>"
        + badge("complete" if row.get("complete") else "incomplete")
        + "</td><td>"
        + money(row.get("price"), row.get("currency") or "AUD")
        + "</td></tr>"
        for row in products[:20]
    ) or "<tr><td colspan='3'>No products yet. They show up when this seller adds one.</td></tr>"
    accepted = ""
    try:
        people = portal.accepted_for_shop(shop["id"])
    except HTTPException:
        people = []
    if people:
        accepted = "".join(
            f"<li>{person_link(directory, 'creator', row.get('creator_id'))} · {esc(row.get('product_title') or 'Product')}</li>"
            for row in people[:20]
        )
    else:
        accepted = "<li class='empty'>No accepted Creatrs yet.</li>"
    orders = attribution.list_orders(shop_id=shop["id"])[:8]
    order_rows = "".join(_order_line(directory, row) for row in orders) or "<li class='empty'>No sales yet.</li>"
    statements = [row for row in load_statements(shop_id=shop["id"])][:8]
    statement_rows = "".join(_statement_line(directory, row) for row in statements) or (
        "<li class='empty'>No statements yet. Statements appear after a month with tracked sales.</li>"
        if db.configured()
        else "<li class='empty'>Statements are stored with the database. Connect the database to see them here.</li>"
    )
    return f"""<p><a href="/admin/sellers">Sellers</a></p>
<h1>{esc(name)}</h1>
<div class="grid-2">
<section class="card"><h2>Business</h2>
<p>Account name: {esc(_text(shop.get('name'), 'Not set'))}</p>
<p>Business name: {esc(_text(shop.get('business_name'), 'Not set'))}</p>
<p>ABN: {esc(shop.get('abn') or 'Not set')}</p>
<p>Category: {esc(admin_format.category_name(shop.get('primary_category')))}</p>
<p>Website: {external(shop.get('store_url'))}</p>
</section>
<section class="card"><h2>Plan</h2>
<p>{esc(admin_format.plan_name('seller', shop.get('plan_tier')))} · {price}</p>
<p>{badge(sub)}</p>
<p>{'Pilot seller.' if shop.get('is_pilot') else 'Not on the pilot flag.'}</p>
</section>
</div>
<section class="card"><h2>Shopify</h2><p>{esc(shopify_sentence(shop))}</p></section>
<section class="card"><h2>Products</h2>
<div class="table-wrap"><table><thead><tr><th>Product</th><th>State</th><th>Price</th></tr></thead><tbody>{product_rows}</tbody></table></div>
</section>
<section class="card"><h2>Accepted Creatrs</h2><ul>{accepted}</ul></section>
<section class="card"><h2>Recent sales</h2><ul>{order_rows}</ul></section>
<section class="card"><h2>Statements</h2><ul>{statement_rows}</ul></section>
{account_card('seller', shop, name, reset_url)}
{timeline_html({shop['id']}, shop_id=shop['id'])}
{technical([('Seller record', shop['id']), ('Stripe customer', shop.get('stripe_customer_id'))])}"""


def creator_body(directory: Directory, creator: dict, reset_url: str) -> str:
    name = directory.creator_name(creator["id"])
    profile = creator.get("profile") or {}
    platforms = ", ".join(admin_format.humanize(item) for item in (creator.get("platforms") or []) if not isinstance(item, dict)) or "Not set"
    categories = ", ".join(admin_format.category_name(item) for item in (creator.get("active_categories") or [])) or "Not set"
    sub = creator.get("subscription_status") or creator.get("plan_status") or "invited"
    try:
        offers = portal.creator_offers(creator["id"])
    except HTTPException:
        offers = []
    offer_rows = "".join(
        f"<li>{esc(row.get('product_title') or directory.product_name(row.get('product_id')))} · {badge('accepted' if row.get('accepted_at') else 'offered')}</li>"
        for row in offers[:20]
    ) or "<li class='empty'>No offers yet.</li>"
    links = attribution.list_links(shop_id=None, creator_id=creator["id"])[:12]
    link_rows = "".join(
        f"<li>{cell_link(f'/admin/collabs/links/{row['id']}', _text(row.get('discount_code'), 'Link'))} · {person_link(directory, 'seller', row.get('shop_id'))}</li>"
        for row in links
    ) or "<li class='empty'>No tracking links yet.</li>"
    orders = attribution.list_orders(creator_id=creator["id"])[:8]
    order_rows = "".join(_order_line(directory, row) for row in orders) or "<li class='empty'>No sales yet.</li>"
    statements = load_statements(creator_id=creator["id"])[:8]
    statement_rows = "".join(_statement_line(directory, row) for row in statements) or "<li class='empty'>No statements yet.</li>"
    return f"""<p><a href="/admin/creators">Creatrs</a></p>
<h1>{esc(name)}</h1>
<section class="card"><h2>Profile</h2>
<p>Handle: {esc(_text(creator.get('handle'), 'Not set'))}</p>
<p>Name: {esc(_text(creator.get('name'), 'Not set'))}</p>
<p>Platforms: {esc(platforms)}</p>
<p>Categories: {esc(categories)}</p>
<p>Plan: {esc(admin_format.plan_name('creator', creator.get('plan_tier')))} · {_plan_price('creator', creator.get('plan_tier'))}</p>
<p>{badge(sub)}</p>
<p>ABN: {esc(profile.get('abn') or 'Not set')}</p>
</section>
<section class="card"><h2>Offers</h2><ul>{offer_rows}</ul></section>
<section class="card"><h2>Links</h2><ul>{link_rows}</ul></section>
<section class="card"><h2>Recent sales</h2><ul>{order_rows}</ul></section>
<section class="card"><h2>Statements</h2><ul>{statement_rows}</ul></section>
{account_card('creator', creator, _text(creator.get('handle') or creator.get('name'), 'this Creatr'), reset_url)}
{timeline_html({creator['id']})}
{technical([('Creatr record', creator['id']), ('Stripe customer', creator.get('stripe_customer_id'))])}"""


def _plan_price(audience: str, tier: Any) -> str:
    table = billing_config.SELLER_TIERS if audience == "seller" else billing_config.CREATOR_TIERS
    spec = table.get(str(tier or "").lower())
    if spec is None:
        return "Price not set"
    return admin_format.money(spec["amount_month_cents"] / 100, kind="a month, ex GST")


def shopify_sentence(shop: dict) -> str:
    domain = shop.get("shopify_domain") or ""
    if db.configured():
        try:
            import shopify_sync

            raw = shopify_sync.connection_status(shop["id"])
        except HTTPException:
            raw = None
        if raw and raw.get("connected"):
            mode = admin_format.humanize(raw.get("mode"))
            return f"Connected by {mode}" + (f" at {domain}." if domain else ".")
        if raw and raw.get("status"):
            return f"Shopify is {admin_format.humanize(raw.get('status')).lower()}."
    if domain:
        return f"Domain {domain} is saved. Order sync is not connected."
    if shop.get("shopify_connected_at"):
        return "Marked connected."
    return "Not connected."


def _order_line(directory: Directory, row: dict) -> str:
    label = _text(row.get("external_order_id"), "Order")
    gross = admin_format.money(row.get("gmv_gross"), row.get("currency"), kind="gross")
    net = admin_format.money(row.get("net_gmv"), row.get("currency"), kind="net")
    return (
        f"<li>{cell_link(f'/admin/sales/orders/{row['id']}', label)} · {esc(gross)} · {esc(net)} · "
        f"{person_link(directory, 'creator', row.get('creator_id'))}</li>"
    )


def _statement_line(directory: Directory, row: dict) -> str:
    label = f"{row.get('period') or 'Statement'}"
    amount = admin_format.money(row.get("amount_owed"), row.get("currency"), kind="net, ex GST")
    return (
        f"<li>{cell_link(f'/admin/sales/statements/{row['id']}', label)} · {esc(amount)} · "
        f"{badge((row.get('display') or {}).get('code') or row.get('status'))} · "
        f"{person_link(directory, 'creator', row.get('creator_id'))}</li>"
    )


def account_card(kind: str, record: dict, confirm: str, reset_url: str) -> str:
    noun = "seller" if kind == "seller" else "Creatr"
    base = f"/admin/{'sellers' if kind == 'seller' else 'creators'}/{record['id']}"
    token = esc(auth.issue_form_token("admin"))
    email = record.get("sign_in_email") or ""
    link = ""
    if reset_url:
        link = (
            "<div class='result'><p>Copy this link now. It is not shown again on refresh, and it is not emailed.</p>"
            f"<label for='reset-url'>Reset link</label><input id='reset-url' readonly value='{esc(reset_url)}'>"
            f"<button type='button' class='secondary' data-copy='{esc(reset_url)}'>Copy</button></div>"
        )
    confirm_fields = (
        f"<label for='confirm-{kind}'>Type {esc(confirm)} to confirm</label>"
        f"<input id='confirm-{kind}' name='confirm_name' autocomplete='off' required>"
    )
    if email:
        identity = f"<p>Sign-in email <strong>{esc(email)}</strong></p>"
        create = ""
        reset = f"""<form method="post" action="{esc(base)}/password">
<input type="hidden" name="csrf_token" value="{token}">
{confirm_fields}
<label for="new-password">New password</label>
<input id="new-password" name="password" type="password" autocomplete="new-password" minlength="12" required>
<label for="new-password-2">Confirm password</label>
<input id="new-password-2" name="password_confirm" type="password" autocomplete="new-password" minlength="12" required>
<button type="submit">Reset password</button>
<p class="muted">Pilot fallback. The password is not shown after you save it.</p>
</form>"""
        email_field = ""
    else:
        identity = "<p>No sign-in yet.</p>"
        create = f"""<form method="post" action="{esc(base)}/login">
<input type="hidden" name="csrf_token" value="{token}">
<label for="login-email">Sign-in email</label>
<input id="login-email" name="email" type="email" autocomplete="username" required>
<label for="login-password">Password</label>
<input id="login-password" name="password" type="password" autocomplete="new-password" minlength="12" required>
<label for="login-password-2">Confirm password</label>
<input id="login-password-2" name="password_confirm" type="password" autocomplete="new-password" minlength="12" required>
{confirm_fields}
<button type="submit">Create login for this {noun}</button>
<p class="muted">Pilot fallback. The password is not shown after you save it.</p>
</form>"""
        reset = ""
        email_field = """<label for="link-email">Sign-in email</label>
<input id="link-email" name="email" type="email" autocomplete="username" required>"""
    return f"""<section class="card"><h2>Account</h2>
{identity}{link}{create}{reset}
<form method="post" action="{esc(base)}/reset-link">
<input type="hidden" name="csrf_token" value="{token}">
{email_field}
<label for="link-confirm">Type {esc(confirm)} to confirm</label>
<input id="link-confirm" name="confirm_name" autocomplete="off" required>
<button class="secondary" type="submit">Create one-time reset link</button>
</form>
<form method="post" action="{esc(base)}/view-as">
<input type="hidden" name="csrf_token" value="{token}">
<label for="view-reason">Reason</label>
<textarea id="view-reason" name="reason" required minlength="8"></textarea>
<label for="view-confirm">Type {esc(confirm)} to confirm</label>
<input id="view-confirm" name="confirm_name" autocomplete="off" required>
<button type="submit">View as this {noun}</button>
</form>
</section>"""


def timeline_html(ids: set, shop_id: Optional[str] = None) -> str:
    rows = []
    for event in admin_data._events():
        if event.get("event_name") in SKIP_EVENTS:
            continue
        if not _mentions(event, ids):
            continue
        rows.append((event.get("occurred_at"), admin_format.event_sentence(event.get("event_name"))))
    if shop_id and shop_id in ids:
        for entry in load_audit(shop_id):
            text = admin_format.event_sentence(entry.get("action"))
            reason = entry.get("reason")
            if isinstance(reason, str) and reason and len(reason) <= 180 and "password" not in reason.lower():
                text = f"{text}. {reason}"
            rows.append((entry.get("at"), text))
    rows.sort(key=lambda item: _iso(item[0]), reverse=True)
    if not rows:
        inner = "<p class='empty'>No activity yet.</p>"
    else:
        inner = "<ul>" + "".join(f"<li>{when(moment)} — {esc(text)}</li>" for moment, text in rows[:30]) + "</ul>"
    return f"<section class='card'><h2>Activity</h2>{inner}</section>"


def _mentions(event: dict, ids: set) -> bool:
    if event.get("entity_id") in ids or event.get("shop_id") in ids:
        return True
    props = event.get("properties") or {}
    if isinstance(props, dict):
        return any(isinstance(value, str) and value in ids for value in props.values())
    return False


# ---------------------------------------------------------------- catalogues and money


def products_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    rows = []
    for product in directory.products.values():
        code = "complete" if product.get("complete") else "incomplete"
        title = directory.product_name(product["id"])
        rows.append(
            {
                "name": title,
                "seller": directory.seller_name(product.get("shop_id")),
                "status": code,
                "created": _iso(product.get("created_at")),
                "blob": f"{title} {directory.seller_name(product.get('shop_id'))} {product.get('id')}",
                "href": f"/admin/products/{product['id']}",
                "cells": [
                    cell_link(f"/admin/products/{product['id']}", title),
                    person_link(directory, "seller", product.get("shop_id")),
                    badge(code),
                    money(product.get("price"), product.get("currency") or "AUD"),
                    when(product.get("created_at")),
                ],
            }
        )
    return render_list(
        request,
        email,
        active="products",
        title="Products",
        path="/admin/products",
        rows=rows,
        columns=(("name", "Product"), ("seller", "Seller"), ("status", "State"), ("created", "Added")),
        search_keys=("blob",),
        status_key="status",
        options=[("complete", "Complete"), ("incomplete", "Incomplete")],
        empty="No products yet. They show up when a seller adds one.",
        default_sort="created",
    )


def product_page(request: Request, product_id: str):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    product = directory.products.get(product_id)
    if product is None:
        return missing(request, email, "products", "/admin/products", "Product")
    title = directory.product_name(product_id)
    rate = product.get("commission_rate")
    commission = "Not set" if rate is None else f"{rate:g}%"
    body = f"""<p><a href="/admin/products">Products</a></p>
<h1>{esc(title)}</h1>
<section class="card">
<p>Seller: {person_link(directory, 'seller', product.get('shop_id'))}</p>
<p>{badge('complete' if product.get('complete') else 'incomplete')}</p>
<p>Price: {money(product.get('price'), product.get('currency') or 'AUD')}</p>
<p>Commission: {esc(commission)}</p>
<p>Category: {esc(admin_format.category_name(product.get('category')))}</p>
<p>Source: {esc(admin_format.humanize(product.get('source')))}</p>
<p>Why it was chosen: {esc(product.get('why_chosen') or 'Not set')}</p>
<p>Value for Creatrs: {esc(product.get('value_to_creators') or 'Not set')}</p>
<p>URL: {external(product.get('url'))}</p>
</section>
{technical([('Product record', product_id), ('Seller record', product.get('shop_id'))])}"""
    return page(request, email, "products", title, body)


def collabs_home(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    body = """<h1>Collabs</h1>
<ul>
<li><a href="/admin/collabs/shortlists">Shortlists</a></li>
<li><a href="/admin/collabs/matches">Matches</a></li>
<li><a href="/admin/collabs/briefs">Briefs</a></li>
<li><a href="/admin/collabs/links">Links</a></li>
</ul>"""
    return page(request, email, "collabs", "Collabs", body)


def sales_home(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    body = """<h1>Sales and commission</h1>
<ul>
<li><a href="/admin/sales/orders">Orders</a></li>
<li><a href="/admin/sales/statements">Statements</a></li>
<li><a href="/admin/sales/corrections">Corrections</a></li>
<li><a href="/admin/sales/disputes">Disputes</a></li>
</ul>"""
    return page(request, email, "sales", "Sales and commission", body)


def shortlists_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    rows = []
    for row in admin_data._shortlists():
        label = directory.product_name(row.get("product_id"))
        rows.append(
            {
                "name": label,
                "seller": directory.seller_name(row.get("shop_id")),
                "created": _iso(row.get("created_at")),
                "status": "open",
                "blob": f"{label} {directory.seller_name(row.get('shop_id'))}",
                "href": f"/admin/collabs/shortlists/{row['id']}",
                "cells": [
                    cell_link(f"/admin/collabs/shortlists/{row['id']}", label),
                    person_link(directory, "seller", row.get("shop_id")),
                    esc(admin_format.humanize(row.get("match_version"))),
                    esc(admin_format.humanize(row.get("optimise_for"))),
                    when(row.get("created_at")),
                ],
            }
        )
    return render_list(
        request,
        email,
        active="collabs",
        title="Shortlists",
        path="/admin/collabs/shortlists",
        rows=rows,
        columns=(("name", "Product"), ("seller", "Seller"), ("created", "Created")),
        search_keys=("blob",),
        status_key="status",
        options=[],
        empty="No shortlists yet. A shortlist appears when a seller runs a match.",
        default_sort="created",
    )


def shortlist_page(request: Request, shortlist_id: str):
    email = operator(request)
    if not email:
        return signed_out()
    detail = admin_data.shortlist_detail(shortlist_id)
    if detail is None:
        return missing(request, email, "collabs", "/admin/collabs/shortlists", "Shortlist")
    directory = Directory()
    title = directory.product_name(detail.get("product_id"))
    items = detail.get("items") or []
    rows = "".join(
        "<li>"
        + person_link(directory, "creator", item.get("creator_id"))
        + " · "
        + badge("accepted" if item.get("accepted_at") else "declined" if item.get("declined_at") else "waiting")
        + f" · score {esc(item.get('score'))}</li>"
        for item in items
    ) or "<li class='empty'>No Creatrs on this shortlist.</li>"
    ids = {shortlist_id, detail.get("product_id")} | {item.get("id") for item in items}
    body = f"""<p><a href="/admin/collabs/shortlists">Shortlists</a></p>
<h1>{esc(title)}</h1>
<section class="card">
<p>Seller: {person_link(directory, 'seller', detail.get('shop_id'))}</p>
<p>Product: {person_link(directory, 'product', detail.get('product_id'))}</p>
<p>Rules: {esc(admin_format.humanize(detail.get('match_version')))}</p>
<p>Optimised for: {esc(admin_format.humanize(detail.get('optimise_for')))}</p>
<p>Created: {when(detail.get('created_at'))}</p>
<ul>{rows}</ul>
</section>
{timeline_html({item for item in ids if item})}
{technical([('Shortlist record', shortlist_id)])}"""
    return page(request, email, "collabs", title, body)


def matches_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    shortlists = {row["id"]: row for row in admin_data._shortlists()}
    rows = []
    for item in admin_data._items():
        shortlist = shortlists.get(item.get("shortlist_id")) or {}
        if item.get("accepted_at"):
            code = "accepted"
        elif item.get("declined_at"):
            code = "declined"
        else:
            code = "waiting"
        label = directory.creator_name(item.get("creator_id"))
        rows.append(
            {
                "name": label,
                "status": code,
                "created": _iso(item.get("accepted_at") or item.get("declined_at")),
                "blob": label,
                "href": f"/admin/collabs/matches/{item['id']}",
                "cells": [
                    cell_link(f"/admin/collabs/matches/{item['id']}", label),
                    person_link(directory, "product", shortlist.get("product_id")),
                    person_link(directory, "seller", shortlist.get("shop_id")),
                    badge(code),
                    esc(item.get("score")),
                ],
            }
        )
    return render_list(
        request,
        email,
        active="collabs",
        title="Matches",
        path="/admin/collabs/matches",
        rows=rows,
        columns=(("name", "Creatr"), ("status", "Status"), ("created", "Updated")),
        search_keys=("blob",),
        status_key="status",
        options=[("accepted", "Accepted"), ("declined", "Declined"), ("waiting", "Waiting")],
        empty="No matches yet.",
        default_sort="created",
    )


def match_page(request: Request, item_id: str):
    email = operator(request)
    if not email:
        return signed_out()
    item = next((row for row in admin_data._items() if row["id"] == item_id), None)
    if item is None:
        return missing(request, email, "collabs", "/admin/collabs/matches", "Match")
    shortlist = next((row for row in admin_data._shortlists() if row["id"] == item.get("shortlist_id")), {}) or {}
    directory = Directory()
    title = directory.creator_name(item.get("creator_id"))
    code = "accepted" if item.get("accepted_at") else "declined" if item.get("declined_at") else "waiting"
    body = f"""<p><a href="/admin/collabs/matches">Matches</a></p>
<h1>{esc(title)}</h1>
<section class="card">
<p>{badge(code)}</p>
<p>Creatr: {person_link(directory, 'creator', item.get('creator_id'))}</p>
<p>Product: {person_link(directory, 'product', shortlist.get('product_id'))}</p>
<p>Seller: {person_link(directory, 'seller', shortlist.get('shop_id'))}</p>
<p>Shortlist: {cell_link(f"/admin/collabs/shortlists/{esc(shortlist.get('id'))}", directory.product_name(shortlist.get('product_id')))}</p>
<p>Score: {esc(item.get('score'))}</p>
</section>
{technical([('Match record', item_id)])}"""
    return page(request, email, "collabs", title, body)


def briefs_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    rows = []
    for brief in load_briefs():
        code = "sent" if brief.get("sent_at") else "not_sent"
        label = directory.product_name(brief.get("product_id"))
        rows.append(
            {
                "name": label,
                "status": code,
                "created": _iso(brief.get("created_at")),
                "blob": f"{label} {directory.creator_name(brief.get('creator_id'))}",
                "href": f"/admin/collabs/briefs/{brief['id']}",
                "cells": [
                    cell_link(f"/admin/collabs/briefs/{brief['id']}", label),
                    person_link(directory, "creator", brief.get("creator_id")),
                    person_link(directory, "seller", brief.get("shop_id")),
                    badge(code),
                    when(brief.get("sent_at") or brief.get("created_at")),
                ],
            }
        )
    return render_list(
        request,
        email,
        active="collabs",
        title="Briefs",
        path="/admin/collabs/briefs",
        rows=rows,
        columns=(("name", "Product"), ("status", "Status"), ("created", "Created")),
        search_keys=("blob",),
        status_key="status",
        options=[("sent", "Sent"), ("not_sent", "Not sent")],
        empty="No briefs yet.",
        default_sort="created",
    )


def brief_page(request: Request, brief_id: str):
    email = operator(request)
    if not email:
        return signed_out()
    brief = next((row for row in load_briefs() if row["id"] == brief_id), None)
    if brief is None:
        return missing(request, email, "collabs", "/admin/collabs/briefs", "Brief")
    directory = Directory()
    title = directory.product_name(brief.get("product_id"))
    body = f"""<p><a href="/admin/collabs/briefs">Briefs</a></p>
<h1>{esc(title)}</h1>
<section class="card">
<p>{badge('sent' if brief.get('sent_at') else 'not_sent')}</p>
<p>Seller: {person_link(directory, 'seller', brief.get('shop_id'))}</p>
<p>Creatr: {person_link(directory, 'creator', brief.get('creator_id'))}</p>
<p>Source: {esc(admin_format.humanize(brief.get('source')))}</p>
<p>Sent: {when(brief.get('sent_at'))}</p>
</section>
{timeline_html({brief_id, brief.get('product_id')})}
{technical([('Brief record', brief_id)])}"""
    return page(request, email, "collabs", title, body)


def links_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    rows = []
    for link in attribution.list_links(shop_id=None, creator_id=None):
        label = _text(link.get("discount_code"), "Link")
        rows.append(
            {
                "name": label,
                "status": "active",
                "created": _iso(link.get("created_at")),
                "blob": f"{label} {directory.creator_name(link.get('creator_id'))} {directory.product_name(link.get('product_id'))}",
                "href": f"/admin/collabs/links/{link['id']}",
                "cells": [
                    cell_link(f"/admin/collabs/links/{link['id']}", label),
                    person_link(directory, "creator", link.get("creator_id")),
                    person_link(directory, "product", link.get("product_id")),
                    esc(admin_format.humanize(link.get("source_platform") or link.get("platform"))),
                    when(link.get("created_at")),
                ],
            }
        )
    return render_list(
        request,
        email,
        active="collabs",
        title="Links",
        path="/admin/collabs/links",
        rows=rows,
        columns=(("name", "Code"), ("created", "Created")),
        search_keys=("blob",),
        status_key="status",
        options=[],
        empty="No tracking links yet.",
        default_sort="created",
    )


def link_page(request: Request, link_id: str):
    email = operator(request)
    if not email:
        return signed_out()
    link = attribution.link_detail(link_id)
    if link is None:
        return missing(request, email, "collabs", "/admin/collabs/links", "Link")
    directory = Directory()
    label = _text(link.get("discount_code"), "Link")
    body = f"""<p><a href="/admin/collabs/links">Links</a></p>
<h1>{esc(label)}</h1>
<section class="card">
<p>Creatr: {person_link(directory, 'creator', link.get('creator_id'))}</p>
<p>Product: {person_link(directory, 'product', link.get('product_id'))}</p>
<p>Seller: {person_link(directory, 'seller', link.get('shop_id'))}</p>
<p>Destination: {external(link.get('link_url') or link.get('direct_url'))}</p>
</section>
{technical([('Link record', link_id)])}"""
    return page(request, email, "collabs", label, body)


def orders_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    rows = []
    for order in attribution.list_orders():
        label = _text(order.get("external_order_id"), "Order")
        rows.append(
            {
                "name": label,
                "status": admin_format.humanize(order.get("source_platform")),
                "created": _iso(order.get("created_at")),
                "blob": f"{label} {directory.seller_name(order.get('shop_id'))} {directory.creator_name(order.get('creator_id'))}",
                "href": f"/admin/sales/orders/{order['id']}",
                "cells": [
                    cell_link(f"/admin/sales/orders/{order['id']}", label),
                    person_link(directory, "seller", order.get("shop_id")),
                    person_link(directory, "creator", order.get("creator_id")),
                    money(order.get("gmv_gross"), order.get("currency"), "gross"),
                    money(order.get("net_gmv"), order.get("currency"), "net"),
                    when(order.get("created_at")),
                ],
            }
        )
    return render_list(
        request,
        email,
        active="sales",
        title="Orders",
        path="/admin/sales/orders",
        rows=rows,
        columns=(("name", "Order"), ("created", "Date")),
        search_keys=("blob",),
        status_key="status",
        options=[],
        empty="No sales yet.",
        default_sort="created",
    )


def order_page(request: Request, order_id: str):
    email = operator(request)
    if not email:
        return signed_out()
    order = attribution.order_detail(order_id)
    if order is None:
        return missing(request, email, "sales", "/admin/sales/orders", "Order")
    directory = Directory()
    label = _text(order.get("external_order_id"), "Order")
    kind = "ex GST" if order.get("ingest_source") == "shopify" else ""
    gross_kind = "gross" if not kind else "gross, ex GST"
    net_kind = "net" if not kind else "net, ex GST"
    body = f"""<p><a href="/admin/sales/orders">Orders</a></p>
<h1>{esc(label)}</h1>
<section class="card">
<p>Seller: {person_link(directory, 'seller', order.get('shop_id'))}</p>
<p>Creatr: {person_link(directory, 'creator', order.get('creator_id'))}</p>
<p>Product: {person_link(directory, 'product', order.get('product_id'))}</p>
<p>Gross: {money(order.get('gmv_gross'), order.get('currency'), gross_kind)}</p>
<p>Net: {money(order.get('net_gmv'), order.get('currency'), net_kind)}</p>
<p>Source: {esc(admin_format.humanize(order.get('source_platform')))}</p>
<p>Channel: {esc(admin_format.humanize(order.get('channel_kind')))}</p>
<p>Date: {when(order.get('created_at'))}</p>
</section>
{technical([('Order record', order_id)])}"""
    return page(request, email, "sales", label, body)


def _statement_rows(directory: Directory, only_disputes: bool = False) -> list[dict]:
    rows = []
    for statement in load_statements():
        if only_disputes and statement.get("status") != "disputed":
            continue
        code = (statement.get("display") or {}).get("code") or statement.get("status") or "open"
        label = f"{directory.seller_name(statement.get('shop_id'))} · {statement.get('period') or 'Statement'}"
        rows.append(
            {
                "name": label,
                "status": {"past_due": "payment-overdue"}.get(code, code if code in {"open", "disputed", "confirmed", "resolved"} else "open"),
                "created": _iso(statement.get("overdue_on") or statement.get("disputed_at")),
                "blob": label,
                "href": f"/admin/sales/statements/{statement['id']}",
                "cells": [
                    cell_link(f"/admin/sales/statements/{statement['id']}", label),
                    person_link(directory, "creator", statement.get("creator_id")),
                    money(statement.get("amount_owed"), statement.get("currency"), "net, ex GST"),
                    badge(code),
                    when(statement.get("overdue_on")),
                ],
            }
        )
    return rows


def statements_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    empty = (
        "No statements yet. Statements appear after a month with tracked sales."
        if db.configured()
        else "Statements are stored with the database. Connect the database to see them here."
    )
    return render_list(
        request,
        email,
        active="sales",
        title="Statements",
        path="/admin/sales/statements",
        rows=_statement_rows(Directory()),
        columns=(("name", "Statement"), ("status", "Status"), ("created", "Due")),
        search_keys=("blob",),
        status_key="status",
        options=[("open", "Open"), ("disputed", "Disputed"), ("confirmed", "Payment confirmed"), ("resolved", "Resolved")],
        empty=empty,
        default_sort="created",
    )


def statement_page(request: Request, statement_id: str):
    email = operator(request)
    if not email:
        return signed_out()
    row = next((item for item in load_statements() if item.get("id") == statement_id), None)
    if row is None:
        return missing(request, email, "sales", "/admin/sales/statements", "Statement")
    directory = Directory()
    title = f"{directory.seller_name(row.get('shop_id'))} · {row.get('period') or 'Statement'}"
    code = (row.get("display") or {}).get("code") or row.get("status")
    due = admin_format.due_phrase(row.get("overdue_on"))
    body = f"""<p><a href="/admin/sales/statements">Statements</a></p>
<h1>{esc(title)}</h1>
<section class="card">
<p>{badge(code)}</p>
<p>Seller: {person_link(directory, 'seller', row.get('shop_id'))}</p>
<p>Creatr: {person_link(directory, 'creator', row.get('creator_id'))}</p>
<p>Net sales: {money(row.get('net_sales'), row.get('currency'), 'net, ex GST')}</p>
<p>Amount owed: {money(row.get('amount_owed'), row.get('currency'), 'net, ex GST')}</p>
<p>Due: {when(row.get('overdue_on'))}{(' · ' + esc(due)) if due else ''}</p>
<p>{esc((row.get('display') or {}).get('label') or '')}</p>
</section>
{technical([('Statement record', statement_id)])}"""
    return page(request, email, "sales", title, body)


def corrections_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    statements = {row.get("id"): row for row in load_statements()}
    rows = []
    for correction in load_corrections():
        statement = statements.get(correction.get("statement_id")) or {}
        label = directory.seller_name(statement.get("shop_id"))
        code = str(correction.get("status") or "open")
        rows.append(
            {
                "name": label,
                "status": code,
                "created": _iso(correction.get("requested_at")),
                "blob": label,
                "href": f"/admin/sales/corrections/{correction['id']}",
                "cells": [
                    cell_link(f"/admin/sales/corrections/{correction['id']}", label or "Correction"),
                    person_link(directory, "creator", statement.get("creator_id")),
                    badge(code),
                    when(correction.get("requested_at")),
                ],
            }
        )
    empty = "No corrections yet." if db.configured() else "Corrections are stored with the database. Connect the database to see them here."
    return render_list(
        request,
        email,
        active="sales",
        title="Corrections",
        path="/admin/sales/corrections",
        rows=rows,
        columns=(("name", "Seller"), ("status", "Status"), ("created", "Requested")),
        search_keys=("blob",),
        status_key="status",
        options=[("disagreed", "Creatr disagreed"), ("pending_creator", "Waiting on the Creatr"), ("admin_corrected", "Corrected by Plantr"), ("admin_kept", "Kept as recorded")],
        empty=empty,
        default_sort="created",
    )


def correction_page(request: Request, correction_id: str):
    email = operator(request)
    if not email:
        return signed_out()
    correction = next((row for row in load_corrections() if row.get("id") == correction_id), None)
    if correction is None:
        return missing(request, email, "sales", "/admin/sales/corrections", "Correction")
    statement = next((row for row in load_statements() if row.get("id") == correction.get("statement_id")), {}) or {}
    directory = Directory()
    title = directory.seller_name(statement.get("shop_id"))
    body = f"""<p><a href="/admin/sales/corrections">Corrections</a></p>
<h1>{esc(title)}</h1>
<section class="card">
<p>{badge(correction.get('status'))}</p>
<p>Creatr: {person_link(directory, 'creator', statement.get('creator_id'))}</p>
<p>Statement: {cell_link(f"/admin/sales/statements/{esc(statement.get('id'))}", str(statement.get('period') or 'Statement'))}</p>
<p>Asked: {when(correction.get('requested_at'))}</p>
<p>Reason: {esc(correction.get('reason') or 'Not set')}</p>
</section>
{technical([('Correction record', correction_id)])}"""
    return page(request, email, "sales", title, body)


def disputes_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    empty = "No open disputes." if db.configured() else "Disputes are stored with the database. Connect the database to see them here."
    return render_list(
        request,
        email,
        active="sales",
        title="Disputes",
        path="/admin/sales/disputes",
        rows=_statement_rows(Directory(), only_disputes=True),
        columns=(("name", "Dispute"), ("created", "Opened")),
        search_keys=("blob",),
        status_key="status",
        options=[],
        empty=empty,
        default_sort="created",
    )


# ---------------------------------------------------------------- applications


def applications_page(request: Request):
    email = operator(request)
    if not email:
        return signed_out()
    rows = []
    for application in load_applications():
        label = _text(application.get("business_name") or application.get("name"), "Application")
        rows.append(
            {
                "name": label,
                "status": application.get("status") or "pending",
                "created": _iso(application.get("created_at")),
                "blob": " ".join(str(application.get(key) or "") for key in ("name", "business_name", "email", "abn")),
                "href": f"/admin/applications/{application['id']}",
                "cells": [
                    cell_link(f"/admin/applications/{application['id']}", label),
                    esc(admin_format.humanize(application.get("role"))),
                    badge(application.get("status")),
                    when(application.get("created_at")),
                ],
            }
        )
    cohorts = ""
    try:
        import pilots

        board = pilots.dashboard()
        lines = []
        for cohort in board.get("cohorts") or []:
            spec = billing_config.PILOT_COHORTS.get(cohort.get("slug") or "", {})
            lines.append(
                f"<li>{esc(spec.get('name') or admin_format.humanize(cohort.get('slug')))}: "
                f"{esc(cohort.get('seats_used'))} of {esc(cohort.get('seat_cap'))} seats, "
                f"{when(cohort.get('starts_at'))} to {when(cohort.get('ends_at'))}.</li>"
            )
        if lines:
            cohorts = "<section class='card'><h2>Pilot cohorts</h2><ul>" + "".join(lines) + "</ul>"
            cohorts += "<p><a href='/admin/pilots.csv'>Download the funnel CSV</a></p></section>"
    except Exception:
        cohorts = ""
    response = render_list(
        request,
        email,
        active="applications",
        title="Applications",
        path="/admin/applications",
        rows=rows,
        columns=(("name", "Applicant"), ("status", "Status"), ("created", "Submitted")),
        search_keys=("blob",),
        status_key="status",
        options=[("pending", "Waiting for review"), ("approved", "Approved"), ("rejected", "Declined"), ("waitlisted", "Waitlisted")],
        empty="No applications yet.",
        default_sort="created",
    )
    if cohorts and response.status_code == 200:
        text = response.body.decode()
        text = text.replace("<h1>Applications</h1>", "<h1>Applications</h1>" + cohorts, 1)
        return HTMLResponse(text, status_code=200)
    return response


def application_page(request: Request, application_id: str, *, notice: str = "", status: int = 200):
    email = operator(request)
    if not email:
        return signed_out()
    application = next((row for row in load_applications() if row["id"] == application_id), None)
    if application is None:
        return missing(request, email, "applications", "/admin/applications", "Application")
    label = _text(application.get("business_name") or application.get("name"), "Application")
    token = esc(auth.issue_form_token("admin"))
    actions = ""
    if application.get("status") in {"pending", "waitlisted"}:
        actions = f"""<form method="post" action="/admin/applications/{esc(application_id)}/approve">
<input type="hidden" name="csrf_token" value="{token}"><button type="submit">Approve</button></form>
<form method="post" action="/admin/applications/{esc(application_id)}/reject">
<input type="hidden" name="csrf_token" value="{token}"><button class="secondary" type="submit">Decline</button></form>"""
    spec = billing_config.PILOT_COHORTS.get(application.get("cohort") or "", {})
    body = f"""<p><a href="/admin/applications">Applications</a></p>
<h1>{esc(label)}</h1>
<section class="card">
<p>{badge(application.get('status'))}</p>
<p>Role: {esc(admin_format.humanize(application.get('role')))}</p>
<p>Email: {esc(application.get('email') or 'Not set')}</p>
<p>Cohort: {esc(spec.get('name') or admin_format.humanize(application.get('cohort')))}</p>
<p>ABN: {esc(application.get('abn') or 'Not set')}</p>
<p>Submitted: {when(application.get('created_at'))} · {esc(admin_format.waiting_phrase(application.get('created_at')))}</p>
{actions}
</section>
{technical([('Application record', application_id)])}"""
    return page(request, email, "applications", label, body, notice=notice, status=status)


# ---------------------------------------------------------------- actions


async def _form(request: Request):
    form = await request.form()
    try:
        portal.check_login_csrf(str(form.get("csrf_token") or ""), "admin")
    except HTTPException:
        return form, NOTICES["csrf"]
    return form, ""


async def account_post(request: Request, kind: str, subject_id: str, action: str):
    email = operator(request)
    if not email:
        return signed_out()
    directory = Directory()
    record = directory.shops.get(subject_id) if kind == "seller" else directory.creators.get(subject_id)
    render = seller_page if kind == "seller" else creator_page
    if record is None:
        return render(request, subject_id)
    confirm = directory.seller_name(subject_id) if kind == "seller" else _text(
        record.get("handle") or record.get("name"), "this Creatr"
    )
    form, csrf_error = await _form(request)
    if csrf_error:
        return render(request, subject_id, notice=csrf_error, status=403)
    if not names_match(str(form.get("confirm_name") or ""), confirm):
        return render(request, subject_id, notice=NOTICES["mismatch"], status=422)
    role = "seller" if kind == "seller" else "creator"
    try:
        if action == "view":
            reason = str(form.get("reason") or "").strip()
            if len(" ".join(reason.split())) < 8:
                return render(request, subject_id, notice=NOTICES["reason"], status=422)
            target = "/seller" if role == "seller" else "/creator"
            response = RedirectResponse(target, status_code=303)
            portal.begin_impersonation(response, role, email, subject_id, confirm, reason=reason)
            return response
        if action == "link":
            sign_in = record.get("sign_in_email") or str(form.get("email") or "")
            token = password_reset.issue(
                role=role,
                email=sign_in,
                shop_id=subject_id if role == "seller" else None,
                creator_id=subject_id if role == "creator" else None,
                created_by=email,
            )
            events.record_admin("reset_link_issued", role=role, subject_id=subject_id, operator=email)
            url = billing_config.public_base_url(request).rstrip("/") + "/account/reset/" + token
            return render(request, subject_id, notice=NOTICES["link"], reset_url=url)
        sign_in = record.get("sign_in_email") or str(form.get("email") or "")
        result = portal.create_login(
            role=role,
            email=sign_in,
            password=str(form.get("password") or ""),
            password_confirm=str(form.get("password_confirm") or ""),
            shop_id=subject_id if role == "seller" else None,
            creator_id=subject_id if role == "creator" else None,
        )
    except HTTPException as exc:
        return render(request, subject_id, notice=plain_error(exc), status=exc.status_code)
    events.record_admin(
        "sign_in_reset" if result.get("updated") else "login_created",
        role=role,
        subject_id=subject_id,
        operator=email,
    )
    code = "reset" if result.get("updated") else "created"
    return RedirectResponse(f"/admin/{'sellers' if role == 'seller' else 'creators'}/{subject_id}?notice={code}", status_code=303)


async def test_post(request: Request, kind: str, action: str):
    if not admin_format.show_test_tools():
        raise HTTPException(status_code=404, detail="Not found")
    email = operator(request)
    if not email:
        return signed_out()
    form, csrf_error = await _form(request)
    if csrf_error:
        return page(request, email, "dashboard", "Dashboard", dashboard_body(), notice=csrf_error, status=403)
    subject_id = str(form.get("subject_id") or "")
    expected = os.getenv("TEST_SELLER_SHOP_ID" if kind == "seller" else "TEST_CREATOR_ID", "").strip()
    if not expected or subject_id != expected:
        return page(request, email, "dashboard", "Dashboard", dashboard_body(), notice=NOTICES["unavailable"], status=404)
    directory = Directory()
    record = directory.shops.get(subject_id) if kind == "seller" else directory.creators.get(subject_id)
    if record is None:
        return page(request, email, "dashboard", "Dashboard", dashboard_body(), notice=NOTICES["unavailable"], status=404)
    role = "seller" if kind == "seller" else "creator"
    name = directory.seller_name(subject_id) if role == "seller" else directory.creator_name(subject_id)
    if action == "view":
        target = "/seller" if role == "seller" else "/creator"
        response = RedirectResponse(target, status_code=303)
        portal.begin_impersonation(response, role, email, subject_id, name, reason="Staging test account")
        return response
    sign_in = record.get("sign_in_email") or ""
    if not sign_in:
        return page(
            request,
            email,
            "dashboard",
            "Dashboard",
            dashboard_body(),
            notice=f"This test {'seller' if role == 'seller' else 'Creatr'} has no sign-in email yet. Open the account and create a login.",
            status=422,
        )
    try:
        token = password_reset.issue(
            role=role,
            email=sign_in,
            shop_id=subject_id if role == "seller" else None,
            creator_id=subject_id if role == "creator" else None,
            created_by=email,
        )
    except HTTPException as exc:
        return page(request, email, "dashboard", "Dashboard", dashboard_body(), notice=plain_error(exc), status=exc.status_code)
    events.record_admin("reset_link_issued", role=role, subject_id=subject_id, operator=email)
    url = billing_config.public_base_url(request).rstrip("/") + "/account/reset/" + token
    note = NOTICES["link"]
    box = (
        "<section class='card'><h2>Reset link</h2><p>Copy this link now. It is not emailed.</p>"
        f"<input readonly value='{esc(url)}'> <button type='button' class='secondary' data-copy='{esc(url)}'>Copy</button></section>"
    )
    return page(request, email, "dashboard", "Dashboard", box + dashboard_body(), notice=note)


async def decide_application(request: Request, application_id: str, approve: bool):
    email = operator(request)
    if not email:
        return signed_out()
    form, csrf_error = await _form(request)
    if csrf_error:
        return application_page(request, application_id, notice=csrf_error, status=403)
    import pilots

    try:
        if approve:
            pilots.approve(application_id)
        else:
            pilots.reject(application_id)
    except HTTPException as exc:
        return application_page(request, application_id, notice=plain_error(exc), status=exc.status_code)
    code = "approved" if approve else "rejected"
    return RedirectResponse(f"/admin/applications/{application_id}?notice={code}", status_code=303)


def reset_get(token: str):
    row = password_reset.peek(token)
    if row is None:
        return HTMLResponse(_public("This link has expired or was already used. Ask Plantr for a new one."), status_code=410)
    form_token = esc(auth.issue_form_token("password-reset"))
    who = "seller" if row.role == "seller" else "Creatr"
    body = f"""<h1>Set a new password</h1>
<p>This sets the password for {esc(row.email)}. It is not shown again.</p>
<form method="post" action="/account/reset/{esc(token)}">
<input type="hidden" name="csrf_token" value="{form_token}">
<label for="password">New password</label>
<input id="password" name="password" type="password" autocomplete="new-password" minlength="12" required>
<label for="password_confirm">Confirm password</label>
<input id="password_confirm" name="password_confirm" type="password" autocomplete="new-password" minlength="12" required>
<button type="submit">Save password</button>
</form>
<p class="muted">Then sign in on the {esc(who)} page.</p>"""
    return HTMLResponse(_public(body))


async def reset_post(token: str, request: Request):
    import main

    main.rate_limit_public(request)
    row = password_reset.peek(token)
    if row is None:
        return HTMLResponse(_public("This link has expired or was already used. Ask Plantr for a new one."), status_code=410)
    form = await request.form()
    try:
        portal.check_login_csrf(str(form.get("csrf_token") or ""), "password-reset")
        password_reset.redeem(token, str(form.get("password") or ""), str(form.get("password_confirm") or ""))
    except HTTPException as exc:
        return HTMLResponse(_public(esc(plain_error(exc))), status_code=exc.status_code)
    events.record_admin("reset_link_used", role=row.role, subject_id=row.shop_id or row.creator_id or "")
    noun = "seller" if row.role == "seller" else "creator"
    return HTMLResponse(_public(f"<h1>Password saved</h1><p>It is not shown here. <a href='/{esc(noun)}'>Sign in</a>.</p>"))


def _public(body: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plantr Collab</title><link rel="stylesheet" href="/admin/assets/admin.css"></head>
<body><main class="login card">{body}</main></body></html>"""


def register(app) -> None:
    @app.get("/assets/plantr.css")
    def plantr_css():
        return FileResponse(ROOT / "static" / "plantr.css", media_type="text/css")

    @app.get("/admin/assets/admin.css")
    def admin_css():
        return FileResponse(ROOT / "static" / "plantr.css", media_type="text/css")

    @app.get("/admin/search")
    def search(request: Request):
        return search_page(request)

    @app.get("/admin/sellers")
    def sellers(request: Request):
        return sellers_page(request)

    @app.get("/admin/sellers/{shop_id}")
    def seller(shop_id: str, request: Request):
        return seller_page(request, shop_id)

    @app.post("/admin/sellers/{shop_id}/login")
    async def seller_login(shop_id: str, request: Request):
        return await account_post(request, "seller", shop_id, "login")

    @app.post("/admin/sellers/{shop_id}/password")
    async def seller_password(shop_id: str, request: Request):
        return await account_post(request, "seller", shop_id, "password")

    @app.post("/admin/sellers/{shop_id}/reset-link")
    async def seller_link(shop_id: str, request: Request):
        return await account_post(request, "seller", shop_id, "link")

    @app.post("/admin/sellers/{shop_id}/view-as")
    async def seller_view(shop_id: str, request: Request):
        return await account_post(request, "seller", shop_id, "view")

    @app.get("/admin/creators")
    def creators(request: Request):
        return creators_page(request)

    @app.get("/admin/creators/{creator_id}")
    def creator(creator_id: str, request: Request):
        return creator_page(request, creator_id)

    @app.post("/admin/creators/{creator_id}/login")
    async def creator_login(creator_id: str, request: Request):
        return await account_post(request, "creator", creator_id, "login")

    @app.post("/admin/creators/{creator_id}/password")
    async def creator_password(creator_id: str, request: Request):
        return await account_post(request, "creator", creator_id, "password")

    @app.post("/admin/creators/{creator_id}/reset-link")
    async def creator_link(creator_id: str, request: Request):
        return await account_post(request, "creator", creator_id, "link")

    @app.post("/admin/creators/{creator_id}/view-as")
    async def creator_view(creator_id: str, request: Request):
        return await account_post(request, "creator", creator_id, "view")

    @app.get("/admin/products")
    def products(request: Request):
        return products_page(request)

    @app.get("/admin/products/{product_id}")
    def product(product_id: str, request: Request):
        return product_page(request, product_id)

    @app.get("/admin/collabs")
    def collabs(request: Request):
        return collabs_home(request)

    @app.get("/admin/collabs/shortlists")
    def shortlists(request: Request):
        return shortlists_page(request)

    @app.get("/admin/collabs/shortlists/{shortlist_id}")
    def shortlist(shortlist_id: str, request: Request):
        return shortlist_page(request, shortlist_id)

    @app.get("/admin/collabs/matches")
    def matches(request: Request):
        return matches_page(request)

    @app.get("/admin/collabs/matches/{item_id}")
    def match(item_id: str, request: Request):
        return match_page(request, item_id)

    @app.get("/admin/collabs/briefs")
    def briefs(request: Request):
        return briefs_page(request)

    @app.get("/admin/collabs/briefs/{brief_id}")
    def brief(brief_id: str, request: Request):
        return brief_page(request, brief_id)

    @app.get("/admin/collabs/links")
    def links(request: Request):
        return links_page(request)

    @app.get("/admin/collabs/links/{link_id}")
    def link(link_id: str, request: Request):
        return link_page(request, link_id)

    @app.get("/admin/sales")
    def sales(request: Request):
        return sales_home(request)

    @app.get("/admin/sales/orders")
    def orders(request: Request):
        return orders_page(request)

    @app.get("/admin/sales/orders/{order_id}")
    def order(order_id: str, request: Request):
        return order_page(request, order_id)

    @app.get("/admin/sales/statements")
    def statements(request: Request):
        return statements_page(request)

    @app.get("/admin/sales/statements/{statement_id}")
    def statement(statement_id: str, request: Request):
        return statement_page(request, statement_id)

    @app.get("/admin/sales/corrections")
    def corrections(request: Request):
        return corrections_page(request)

    @app.get("/admin/sales/corrections/{correction_id}")
    def correction(correction_id: str, request: Request):
        return correction_page(request, correction_id)

    @app.get("/admin/sales/disputes")
    def disputes(request: Request):
        return disputes_page(request)

    @app.get("/admin/applications")
    def applications(request: Request):
        return applications_page(request)

    @app.get("/admin/applications/{application_id}")
    def application(application_id: str, request: Request):
        return application_page(request, application_id)

    @app.post("/admin/applications/{application_id}/approve")
    async def approve(application_id: str, request: Request):
        return await decide_application(request, application_id, True)

    @app.post("/admin/applications/{application_id}/reject")
    async def reject(application_id: str, request: Request):
        return await decide_application(request, application_id, False)

    @app.get("/admin/settings")
    def settings(request: Request):
        return settings_page(request)

    @app.post("/admin/test-accounts/{kind}/view-as")
    async def test_view(kind: str, request: Request):
        if kind not in {"seller", "creator"}:
            raise HTTPException(status_code=404, detail="Not found")
        return await test_post(request, kind, "view")

    @app.post("/admin/test-accounts/{kind}/reset-link")
    async def test_link(kind: str, request: Request):
        if kind not in {"seller", "creator"}:
            raise HTTPException(status_code=404, detail="Not found")
        return await test_post(request, kind, "link")

    @app.get("/account/reset/{token}")
    def account_reset(token: str):
        return reset_get(token)

    @app.post("/account/reset/{token}")
    async def account_reset_post(token: str, request: Request):
        return await reset_post(token, request)
