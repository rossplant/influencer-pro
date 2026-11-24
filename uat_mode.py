"""UAT mode is staging only. Production never turns it on."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

import db

logger = logging.getLogger("plantr.uat")

STEPS = (
    "supplier_invite",
    "supplier_password",
    "supplier_onboarding",
    "supplier_plan_sandbox",
    "catalogue_csv",
    "catalogue_shopify",
    "listing_studio",
    "sample_received",
    "sample_approved",
    "supplier_statement",
    "mark_paid",
    "creatr_invite",
    "creatr_password_onboarding",
    "creatr_age_check",
    "creatr_plan",
    "browse_match",
    "sample_request",
    "post_with_ad",
    "content_kit",
    "creatr_statement",
    "confirm_payment",
)


def enabled() -> bool:
    env = os.getenv("ENV", "").strip().lower()
    railway = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip().lower()
    if env == "production" or railway == "production":
        return False
    return os.getenv("UAT_MODE", "").strip() == "1"


def _feedback():
    if not hasattr(db, "memory_uat_feedback"):
        db.memory_uat_feedback = []
    return db.memory_uat_feedback


def record(step: str, result: str, comment: str = "") -> dict:
    if step not in STEPS:
        raise ValueError("Unknown checklist step")
    if result not in {"pass", "fail", ""}:
        raise ValueError("Result must be pass or fail")
    row = {
        "id": str(uuid.uuid4()),
        "step": step,
        "result": result,
        "comment": (comment or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if db.configured():
        from models import UatFeedback, utcnow

        with db.session_scope() as session:
            session.add(UatFeedback(id=row["id"], step=row["step"], result=row["result"], comment=row["comment"], created_at=utcnow()))
    else:
        _feedback().append(row)
    return row


def feedback_rows() -> list[dict]:
    if db.configured():
        from sqlalchemy import select

        from models import UatFeedback

        with db.session_scope() as session:
            rows = session.scalars(select(UatFeedback).order_by(UatFeedback.created_at.asc())).all()
            return [
                {"id": row.id, "step": row.step, "result": row.result, "comment": row.comment, "created_at": row.created_at.isoformat()}
                for row in rows
            ]
    return list(_feedback())


def feedback_csv() -> str:
    lines = ["step,result,comment,created_at"]
    for row in feedback_rows():
        comment = str(row["comment"]).replace('"', "'")
        lines.append(f"{row['step']},{row['result']},\"{comment}\",{row['created_at']}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    ids = getattr(db, "memory_uat_ids", {}) or {}
    emails = {ids.get("email")}
    if ids.get("email") and "@" in ids["email"]:
        local, _, domain = ids["email"].partition("@")
        emails.add(f"{local}+creatr@{domain}")
    if hasattr(db, "memory_portal_accounts"):
        db.memory_portal_accounts = [row for row in db.memory_portal_accounts if getattr(row, "email", None) not in emails]
    for product in ids.get("products") or []:
        db.memory_products.pop(product["id"], None)
        if hasattr(db, "memory_listing"):
            db.memory_listing.pop(product["id"], None)
    shop_id = ids.get("shop_id")
    if shop_id:
        db.memory_shops.pop(shop_id, None)
        db.memory_shortlists = [row for row in db.memory_shortlists if getattr(row, "shop_id", None) != shop_id]
        kept = []
        for item in db.memory_shortlist_items:
            shortlist = next((row for row in db.memory_shortlists if row.id == getattr(item, "shortlist_id", None)), None)
            if shortlist is not None:
                kept.append(item)
        db.memory_shortlist_items = kept
    creator_id = ids.get("creator_id")
    if creator_id:
        db.memory_creators.pop(creator_id, None)
        db.memory_creator_objects.pop(creator_id, None)
    if db.configured():
        from sqlalchemy import delete, select

        from models import PortalAccount, UatFeedback, UatInvite, UatSet

        with db.session_scope() as session:
            session.execute(delete(UatFeedback))
            row = session.scalar(select(UatSet))
            if row is not None:
                session.execute(delete(PortalAccount).where(PortalAccount.email == row.email))
            session.execute(delete(UatInvite))
            session.execute(delete(UatSet))
    db.memory_uat_feedback = []
    db.memory_uat_ids = {}
    db.memory_uat_by_email = {}
    db.memory_uat_invites = []
    db.memory_uat_sales = []


def _sync(row: dict) -> dict:
    db.memory_uat_ids = row
    return row


def current_set(email: str | None = None) -> dict | None:
    wanted = (email or "").strip().lower()
    if db.configured():
        from sqlalchemy import select

        from models import UatSet

        with db.session_scope() as session:
            query = select(UatSet).order_by(UatSet.created_at.desc())
            if wanted:
                query = query.where((UatSet.email == wanted) | (UatSet.creatr_email == wanted))
            row = session.scalar(query)
            if row is None:
                if not wanted:
                    db.memory_uat_ids = {}
                return None
            found = {
                "id": row.id,
                "email": row.email,
                "shop_id": row.shop_id,
                "creator_id": row.creator_id,
                "audience": row.audience,
                "first_name": row.first_name or "",
                "brand_name": row.brand_name or "",
                "creatr_email": row.creatr_email or row.email,
                "creatr_name": _creator_name(row.creator_id),
                "invite_sent_at": None if row.invite_sent_at is None else row.invite_sent_at.isoformat(),
                "invite_provider_id": row.invite_provider_id or "",
                "last_press_id": row.last_press_id or "",
                "billing": "sandbox",
            }
        return _sync(found)
    store = getattr(db, "memory_uat_by_email", None) or {}
    if wanted:
        found = store.get(wanted)
        if found:
            if not found.get("creatr_name"):
                found["creatr_name"] = _creator_name(found.get("creator_id") or "")
            return found
        for row in store.values():
            if row.get("creatr_email") == wanted:
                if not row.get("creatr_name"):
                    row["creatr_name"] = _creator_name(row.get("creator_id") or "")
                return row
        return None
    found = getattr(db, "memory_uat_ids", None) or {}
    if not found.get("shop_id"):
        return None
    return found


def _remember(row: dict) -> None:
    if db.configured():
        from models import UatSet, utcnow

        with db.session_scope() as session:
            session.add(
                UatSet(
                    id=row["id"],
                    email=row["email"],
                    shop_id=row["shop_id"],
                    creator_id=row["creator_id"],
                    audience=row["audience"],
                    first_name=row.get("first_name") or None,
                    brand_name=row.get("brand_name") or None,
                    creatr_email=row.get("creatr_email") or None,
                    invite_sent_at=None,
                    invite_provider_id=None,
                    last_press_id=None,
                    created_at=utcnow(),
                )
            )
    _sync(row)
    if not hasattr(db, "memory_uat_by_email"):
        db.memory_uat_by_email = {}
    db.memory_uat_by_email[row["email"]] = row
    creatr_email = row.get("creatr_email") or ""
    if creatr_email and creatr_email != row["email"]:
        db.memory_uat_by_email[creatr_email] = row


def _identity(first_name: str, brand_name: str, creatr_email: str, supplier_email: str) -> tuple[str, str, str]:
    first = (first_name or "").strip()[:80]
    brand = (brand_name or "").strip()[:120] or "Homeware supplier"
    creatr = (creatr_email or "").strip().lower()
    if creatr and "@" not in creatr:
        raise ValueError("Enter a valid Creatr email, or leave it blank.")
    return first, brand, creatr or supplier_email


def _creator_name(creator_id: str) -> str:
    if not creator_id:
        return ""
    if db.configured():
        from models import Creator

        with db.session_scope() as session:
            row = session.get(Creator, creator_id)
            if row is None:
                return ""
            return str(row.name or (row.profile or {}).get("name") or "")
    record = db.memory_creators.get(creator_id)
    if isinstance(record, dict):
        return str(record.get("name") or "")
    return ""


def _display_name(creatr_name: str, first: str) -> str:
    return (creatr_name or "").strip()[:120] or first or "UAT Creatr"


def _rename_shop(shop_id: str, brand: str) -> None:
    shop = db.memory_shops.get(shop_id)
    if shop is not None:
        shop.name = brand
        if hasattr(shop, "business_name"):
            shop.business_name = brand
    if not db.configured():
        return
    from models import Shop

    with db.session_scope() as session:
        row = session.get(Shop, shop_id)
        if row is not None:
            row.name = brand
            row.business_name = brand


def _rename_creator(creator_id: str, name: str) -> None:
    stored = db.memory_creators.get(creator_id)
    if isinstance(stored, dict):
        stored["name"] = name
    obj = db.memory_creator_objects.get(creator_id)
    if obj is not None:
        profile = dict(getattr(obj, "profile", None) or {})
        profile["name"] = name
        obj.profile = profile
        obj.name = name
    if not db.configured():
        return
    from sqlalchemy.orm.attributes import flag_modified

    from models import Creator

    with db.session_scope() as session:
        row = session.get(Creator, creator_id)
        if row is None:
            return
        row.name = name
        profile = dict(row.profile or {})
        profile["name"] = name
        row.profile = profile
        flag_modified(row, "profile")


def _save_identity(row: dict) -> None:
    if not db.configured():
        _index_set(row)
        return
    from models import UatSet

    with db.session_scope() as session:
        stored = session.get(UatSet, row["id"])
        if stored is not None:
            stored.first_name = row.get("first_name") or None
            stored.brand_name = row.get("brand_name") or None
            stored.creatr_email = row.get("creatr_email") or None
    _index_set(row)


def _index_set(row: dict) -> None:
    if not hasattr(db, "memory_uat_by_email"):
        db.memory_uat_by_email = {}
    db.memory_uat_by_email[row["email"]] = row
    creatr_email = row.get("creatr_email") or ""
    if creatr_email and creatr_email != row["email"]:
        db.memory_uat_by_email[creatr_email] = row
    _sync(row)


def create_set(notify_email: str, *, first_name: str = "", brand_name: str = "", creatr_email: str = "", creatr_name: str = "") -> dict:
    """Create the supplier and Creatr. The same supplier email is reused. A different email is a new set."""
    if not enabled():
        raise RuntimeError("UAT mode is off")
    if os.getenv("STRIPE_SECRET_KEY", "").startswith("sk_live"):
        raise RuntimeError("UAT refuses a live Stripe key")
    email = (notify_email or "").strip().lower()
    if "@" not in email:
        raise ValueError("Enter an email address for the password link")
    first, brand, creatr_address = _identity(first_name, brand_name, creatr_email, email)
    display = _display_name(creatr_name, first)
    existing = current_set(email)
    if existing and existing.get("email") == email:
        existing["reused"] = True
        existing["first_name"] = first
        existing["brand_name"] = brand
        existing["creatr_email"] = creatr_address
        existing["creatr_name"] = display
        _rename_shop(existing["shop_id"], brand)
        _rename_creator(existing["creator_id"], display)
        _save_identity(existing)
        _issue_invites(existing)
        return existing
    import secrets

    import portal
    import services

    secret = secrets.token_urlsafe(24)
    creatr_secret = secrets.token_urlsafe(24)
    domain = f"uat-{uuid.uuid4().hex[:8]}.myshopify.com"
    shop = services.create_shop(
        name=brand,
        shopify_domain=domain,
        plan_tier="starter",
        plan_status="free",
        is_pilot=True,
        primary_category="home",
        business_name=brand,
    )
    shop_id = shop["id"]
    if shop_id not in db.memory_shops:
        from types import SimpleNamespace

        db.memory_shops[shop_id] = SimpleNamespace(
            id=shop_id,
            name=brand,
            business_name=brand,
            is_pilot=True,
            shopify_domain=domain,
            primary_category="home",
        )
    creator_id = str(uuid.uuid4())
    services.persist_creator(
        {
            "influencer_id": creator_id,
            "handle": f"@uat{creator_id.replace('-', '')[:8]}",
            "name": display,
            "areas_of_interest": ["home"],
            "preferred_promotion_styles": ["educational"],
            "active_categories": ["home"],
            "platforms": ["instagram"],
            "availability": "open",
            "metrics_source": "self_reported",
            "avg_views": 45000,
            "audience": 45000,
        }
    )
    if creator_id not in db.memory_creators:
        db.memory_creators[creator_id] = {"id": creator_id, "handle": f"@uat{creator_id.replace('-', '')[:8]}", "audience": 45000}
    portal.create_login(role="seller", email=email, password=secret, password_confirm=secret, shop_id=shop_id)
    portal.create_login(role="creator", email=creatr_address, password=creatr_secret, password_confirm=creatr_secret, creator_id=creator_id)
    row = {
        "id": str(uuid.uuid4()),
        "email": email,
        "creatr_email": creatr_address,
        "first_name": first,
        "brand_name": brand,
        "creatr_name": display,
        "shop_id": shop_id,
        "creator_id": creator_id,
        "audience": 45000,
        "products": [],
        "billing": "sandbox",
        "invite_sent_at": None,
        "last_press_id": "",
        "reused": False,
    }
    _remember(row)
    _issue_invites(row)
    return row


def _brand_name(ids: dict) -> str:
    import services

    shop = services.fetch_shop(str(ids.get("shop_id") or ""))
    if isinstance(shop, dict) and shop.get("name"):
        return str(shop["name"])
    return "your shop"


def https_base(base_url: str = "") -> str:
    base = (os.getenv("PUBLIC_BASE_URL", "") or base_url or "").strip().rstrip("/")
    if not base.startswith("https://") or " " in base:
        raise ValueError("The site address must be an absolute https URL")
    return base


def _invite_rows() -> list:
    if not hasattr(db, "memory_uat_invites"):
        db.memory_uat_invites = []
    return db.memory_uat_invites


def _find_invite(set_id: str, role: str) -> dict | None:
    now = datetime.now(timezone.utc)
    if db.configured():
        from sqlalchemy import select

        from models import UatInvite

        with db.session_scope() as session:
            row = session.scalar(
                select(UatInvite).where(UatInvite.set_id == set_id, UatInvite.role == role).order_by(UatInvite.expires_at.desc())
            )
            if row is None:
                return None
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < now:
                return None
            return _invite_view(row)
    for row in reversed(_invite_rows()):
        if row["set_id"] == set_id and row["role"] == role and row["expires_at"] >= now:
            return row
    return None


def invite_by_code(code: str, role: str) -> dict | None:
    now = datetime.now(timezone.utc)
    if db.configured():
        from models import UatInvite

        with db.session_scope() as session:
            row = session.get(UatInvite, code)
            if row is None or row.role != role:
                return None
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < now:
                return None
            return _invite_view(row)
    for row in _invite_rows():
        if row["id"] == code and row["role"] == role and row["expires_at"] >= now:
            return row
    return None


def _issue_invites(row: dict) -> dict:
    import secrets
    from datetime import timedelta

    paths = {}
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    for role, slug in (("seller", "s"), ("creator", "c")):
        role_email = row["email"] if role == "seller" else (row.get("creatr_email") or row["email"])
        found = _find_invite(row["id"], role)
        if found is not None and found.get("status") != "sent" and found.get("email") != role_email:
            found["email"] = role_email
            if db.configured():
                from models import UatInvite

                with db.session_scope() as session:
                    stored = session.get(UatInvite, found["id"])
                    if stored is not None and stored.status != "sent":
                        stored.email = role_email
        if found is None:
            code = secrets.token_urlsafe(6)
            record = {
                "id": code,
                "set_id": row["id"],
                "role": role,
                "email": role_email,
                "expires_at": expires,
                "provider_id": "",
                "sent_at": None,
                "status": "",
                "last_error": "",
                "last_press_id": "",
            }
            if db.configured():
                from models import UatInvite

                with db.session_scope() as session:
                    session.add(UatInvite(id=code, set_id=row["id"], role=role, email=role_email, expires_at=expires))
            _invite_rows().append(record)
            found = record
        paths[role] = f"/uat/invite/{slug}/{found['id']}"
    row["supplier_path"] = paths["seller"]
    row["creatr_path"] = paths["creator"]
    return paths


def _invite_view(row) -> dict:
    sent_at = getattr(row, "sent_at", None)
    if sent_at is not None and not isinstance(sent_at, str):
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        sent_at = sent_at.isoformat()
    expires = row.expires_at if not isinstance(row, dict) else row["expires_at"]
    if not isinstance(row, dict) and expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    source = row if isinstance(row, dict) else row
    def pick(name, default=""):
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)
    return {
        "id": pick("id"),
        "set_id": pick("set_id"),
        "role": pick("role"),
        "email": pick("email"),
        "expires_at": expires,
        "provider_id": pick("provider_id") or "",
        "sent_at": sent_at if not isinstance(row, dict) else row.get("sent_at"),
        "status": pick("status") or "",
        "last_error": pick("last_error") or "",
        "last_press_id": pick("last_press_id") or "",
    }


def invite_states(set_id: str) -> dict:
    return {role: found for role in ("seller", "creator") if (found := _find_invite(set_id, role))}


def _save_invite_result(code: str, *, status: str, provider_id: str, last_error: str, press: str) -> dict:
    when = datetime.now(timezone.utc) if status == "sent" else None
    if db.configured():
        from models import UatInvite

        with db.session_scope() as session:
            row = session.get(UatInvite, code)
            if row is not None:
                row.status = status
                row.provider_id = (provider_id or "")[:64] or None
                row.sent_at = when
                row.last_error = (last_error or "")[:160] or None
                row.last_press_id = press or None
    saved = {
        "status": status,
        "provider_id": provider_id,
        "sent_at": when.isoformat() if when else None,
        "last_error": last_error,
        "last_press_id": press,
    }
    for record in _invite_rows():
        if record["id"] == code:
            record.update(saved)
    return saved


def send_invite(press_id: str = "", base_url: str = "", email: str = "", role: str = "") -> dict:
    """Send the supplier invite and the Creatr invite. Pass role to retry one of them."""
    if not enabled():
        raise RuntimeError("UAT mode is off")
    ids = current_set(email) if (email or "").strip() else current_set()
    if not ids or "@" not in str(ids.get("email") or ""):
        raise ValueError("Create a UAT set before sending the invite.")
    press = (press_id or "").strip()
    if press and press == (ids.get("last_press_id") or ""):
        return {"sent": False, "duplicate": True, "invite_sent_at": ids.get("invite_sent_at"), "email": ids["email"], "roles": {}}
    wanted = [role] if role in {"seller", "creator"} else ["seller", "creator"]
    import emailer

    try:
        base = https_base(base_url)
    except ValueError as exc:
        return {"sent": False, "duplicate": False, "reason": str(exc), "invite_sent_at": None, "email": ids["email"], "roles": {}}
    paths = _issue_invites(ids)
    templates = {"seller": "supplier_invite", "creator": "creatr_invite"}
    brand_name = ids.get("brand_name") or _brand_name(ids)
    roles: dict = {}
    attempted = False
    for name in wanted:
        current = _find_invite(ids["id"], name) or {}
        if not current.get("id"):
            roles[name] = {"sent": False, "reason": "That invite could not be prepared", "status": "failed"}
            continue
        if current.get("status") == "sent" and current.get("provider_id"):
            roles[name] = {"sent": True, "skipped": True, "provider_id": current["provider_id"], "sent_at": current.get("sent_at"), "status": "sent"}
            continue
        recipient = ids["email"] if name == "seller" else (ids.get("creatr_email") or ids["email"])
        if emailer.invite_opted_out(recipient):
            roles[name] = {"sent": False, "reason": "This address has opted out of invites.", "status": "failed"}
            continue
        attempted = True
        result = emailer.send(
            templates[name],
            recipient,
            url=f"{base}{paths[name]}",
            first_name=str(ids.get("first_name") or ""),
            brand_name=brand_name,
            unsubscribe_url=emailer.invite_opt_out_url(base, recipient),
        )
        provider_id = str(result.get("provider_id") or "").strip()
        if provider_id:
            saved = _save_invite_result(current["id"], status="sent", provider_id=provider_id[:64], last_error="", press=press)
            logger.info("uat_invite role=%s resend_id=%s", name, provider_id)
            roles[name] = {"sent": True, "provider_id": provider_id, "sent_at": saved["sent_at"], "status": "sent"}
        else:
            reason = str(result.get("reason") or "the mail service did not accept it")[:160]
            _save_invite_result(current["id"], status="failed", provider_id="", last_error=reason, press=press)
            logger.info("uat_invite role=%s failed", name)
            roles[name] = {"sent": False, "reason": reason, "status": "failed"}
    both = invite_states(ids["id"])
    both_sent = both.get("seller", {}).get("status") == "sent" and both.get("creator", {}).get("status") == "sent"
    sent_at = None
    if both_sent:
        sent_at = both["creator"].get("sent_at") or both["seller"].get("sent_at")
    if attempted or both_sent:
        ids["last_press_id"] = press
        ids["invite_sent_at"] = sent_at
        if db.configured():
            from models import UatSet

            with db.session_scope() as session:
                row = session.get(UatSet, ids["id"])
                if row is not None:
                    row.last_press_id = press or None
                    if both_sent and row.invite_sent_at is None:
                        row.invite_sent_at = datetime.now(timezone.utc)
                        ids["invite_sent_at"] = row.invite_sent_at.isoformat()
        _sync(ids)
    failures = [row.get("reason") for row in roles.values() if not row.get("sent")]
    distinct = list(dict.fromkeys(reason for reason in failures if reason))
    sent_flag = all(row.get("sent") for row in roles.values()) and bool(roles)
    return {
        "sent": sent_flag,
        "duplicate": False,
        "already": not attempted and sent_flag,
        "reason": distinct[0] if len(distinct) == 1 else "; ".join(distinct),
        "invite_sent_at": ids.get("invite_sent_at"),
        "email": ids["email"],
        "roles": roles,
    }


def _product_shop(product) -> str:
    if isinstance(product, dict):
        return str(product.get("shop_id") or "")
    return str(getattr(product, "shop_id", "") or "")


def _purge_set(row: dict) -> None:
    """Remove one UAT set, its shop products, and its Creatr. Other sets stay."""
    shop_id = row.get("shop_id")
    creator_id = row.get("creator_id")
    emails = {row.get("email"), row.get("creatr_email")}
    emails.discard(None)
    emails.discard("")
    if hasattr(db, "memory_portal_accounts"):
        db.memory_portal_accounts = [item for item in db.memory_portal_accounts if getattr(item, "email", None) not in emails]
    db.memory_products = {key: item for key, item in db.memory_products.items() if _product_shop(item) != shop_id}
    if shop_id:
        db.memory_shops.pop(shop_id, None)
        db.memory_shortlists = [item for item in db.memory_shortlists if getattr(item, "shop_id", None) != shop_id]
        kept = []
        for item in db.memory_shortlist_items:
            shortlist = next((entry for entry in db.memory_shortlists if entry.id == getattr(item, "shortlist_id", None)), None)
            if shortlist is not None:
                kept.append(item)
        db.memory_shortlist_items = kept
    if creator_id:
        db.memory_creators.pop(creator_id, None)
        db.memory_creator_objects.pop(creator_id, None)
    db.memory_uat_invites = [item for item in _invite_rows() if item.get("set_id") != row.get("id")]
    store = getattr(db, "memory_uat_by_email", None) or {}
    for key, item in list(store.items()):
        if item.get("id") == row.get("id"):
            store.pop(key, None)
    if getattr(db, "memory_uat_ids", {}).get("id") == row.get("id"):
        db.memory_uat_ids = {}
    if not db.configured():
        return
    from sqlalchemy import delete, select
    from sqlalchemy.exc import IntegrityError

    from models import Creator, PortalAccount, Product, Shop, Shortlist, ShortlistExclusion, ShortlistItem, UatInvite, UatSet

    with db.session_scope() as session:
        session.execute(delete(UatInvite).where(UatInvite.set_id == row["id"]))
        if shop_id:
            shortlist_ids = list(session.scalars(select(Shortlist.id).where(Shortlist.shop_id == shop_id)))
            if shortlist_ids:
                session.execute(delete(ShortlistItem).where(ShortlistItem.shortlist_id.in_(shortlist_ids)))
                session.execute(delete(ShortlistExclusion).where(ShortlistExclusion.shortlist_id.in_(shortlist_ids)))
            session.execute(delete(Shortlist).where(Shortlist.shop_id == shop_id))
            session.execute(delete(Product).where(Product.shop_id == shop_id))
            session.execute(delete(PortalAccount).where(PortalAccount.shop_id == shop_id))
        if creator_id:
            session.execute(delete(PortalAccount).where(PortalAccount.creator_id == creator_id))
        session.execute(delete(UatSet).where(UatSet.id == row["id"]))
    try:
        with db.session_scope() as session:
            if creator_id:
                session.execute(delete(Creator).where(Creator.id == creator_id))
            if shop_id:
                session.execute(delete(Shop).where(Shop.id == shop_id))
    except IntegrityError:
        return


def reset_set(notify_email: str, *, first_name: str = "", brand_name: str = "", creatr_email: str = "", creatr_name: str = "") -> dict:
    """Delete one email's UAT set and create a fresh one. Nothing is emailed."""
    email = (notify_email or "").strip().lower()
    existing = current_set(email)
    if existing and existing.get("email") == email:
        _purge_set(existing)
    created = create_set(email, first_name=first_name, brand_name=brand_name, creatr_email=creatr_email, creatr_name=creatr_name)
    created["reset"] = True
    created["reused"] = False
    return created


def create_and_send(email: str, press_id: str, base_url: str = "", *, first_name: str = "", brand_name: str = "", creatr_email: str = "", creatr_name: str = "") -> dict:
    created = create_set(email, first_name=first_name, brand_name=brand_name, creatr_email=creatr_email, creatr_name=creatr_name)
    sent = send_invite(press_id, base_url, email=created["email"])
    return {**created, **sent}


def open_invite(token: str) -> dict:
    import auth

    payload = auth._read_signed(token)
    purpose = str((payload or {}).get("purpose") or "")
    if not purpose.startswith("uat-set:"):
        raise ValueError("That invite link is not valid")
    _, role, email = purpose.split(":", 2)
    if role not in {"seller", "creator"}:
        raise ValueError("That invite link is not valid")
    return {"role": role, "email": email, "token": token}


def activate_invite(token: str, *, born, attested: bool, password: str, role: str = "") -> dict:
    import creatr_age
    import portal

    blocked = creatr_age.check_signup(born=born, attested=attested)
    if blocked:
        raise ValueError(blocked)
    if role:
        record = invite_by_code(token, role)
        if record is None:
            raise ValueError("That invite link is not valid or has expired.")
        ids = current_set(record["email"]) or {}
        opened_role = role
        opened_email = record["email"]
    else:
        opened = open_invite(token)
        ids = current_set(opened["email"]) or current_set() or {}
        opened_role = opened["role"]
        opened_email = opened["email"]
    allowed = {ids.get("email"), ids.get("creatr_email") or ids.get("email")}
    if opened_email not in allowed:
        raise ValueError("That invite link is not valid or has expired.")
    subject = ids["shop_id"] if opened_role == "seller" else ids["creator_id"]
    if opened_role == "seller" and ids.get("shop_id") not in db.memory_shops and not db.configured():
        from types import SimpleNamespace

        db.memory_shops[ids["shop_id"]] = SimpleNamespace(id=ids["shop_id"], name=ids.get("brand_name") or "Homeware supplier", is_pilot=True)
    if opened_role == "creator" and ids.get("creator_id") not in db.memory_creators and not db.configured():
        db.memory_creators[ids["creator_id"]] = {"id": ids["creator_id"], "handle": "@uat", "audience": 45000}
    portal.create_login(
        role=opened_role,
        email=opened_email,
        password=password,
        password_confirm=password,
        shop_id=ids["shop_id"] if opened_role == "seller" else None,
        creator_id=ids["creator_id"] if opened_role == "creator" else None,
    )
    return {"role": opened_role, "email": opened_email, "subject_id": subject}


def simulate_sale(code: str = "UAT") -> dict:
    if not enabled():
        raise RuntimeError("UAT mode is off")
    if not hasattr(db, "memory_uat_sales"):
        db.memory_uat_sales = []
    row = {"id": str(uuid.uuid4()), "code": (code or "UAT").strip(), "source": "uat"}
    db.memory_uat_sales.append(row)
    return row
