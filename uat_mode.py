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

        from models import PortalAccount, UatFeedback, UatSet

        with db.session_scope() as session:
            session.execute(delete(UatFeedback))
            row = session.scalar(select(UatSet))
            if row is not None:
                session.execute(delete(PortalAccount).where(PortalAccount.email == row.email))
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
                query = query.where(UatSet.email == wanted)
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
                "invite_sent_at": None if row.invite_sent_at is None else row.invite_sent_at.isoformat(),
                "invite_provider_id": row.invite_provider_id or "",
                "last_press_id": row.last_press_id or "",
                "billing": "sandbox",
            }
        return _sync(found)
    store = getattr(db, "memory_uat_by_email", None) or {}
    if wanted:
        return store.get(wanted)
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


def create_set(notify_email: str) -> dict:
    """Create the supplier and Creatr for this email. The same email is reused. A different email is a new set."""
    if not enabled():
        raise RuntimeError("UAT mode is off")
    if os.getenv("STRIPE_SECRET_KEY", "").startswith("sk_live"):
        raise RuntimeError("UAT refuses a live Stripe key")
    email = (notify_email or "").strip().lower()
    if "@" not in email:
        raise ValueError("Enter an email address for the password link")
    existing = current_set(email)
    if existing:
        existing["reused"] = True
        _issue_invites(existing)
        return existing
    import secrets

    import portal
    import services

    secret = secrets.token_urlsafe(24)
    creatr_secret = secrets.token_urlsafe(24)
    domain = f"uat-{uuid.uuid4().hex[:8]}.myshopify.com"
    shop = services.create_shop(
        name="Homeware supplier",
        shopify_domain=domain,
        plan_tier="starter",
        plan_status="free",
        is_pilot=True,
        primary_category="home",
        business_name="Homeware supplier",
    )
    shop_id = shop["id"]
    if shop_id not in db.memory_shops:
        from types import SimpleNamespace

        db.memory_shops[shop_id] = SimpleNamespace(
            id=shop_id,
            name="Homeware supplier",
            business_name="Homeware supplier",
            is_pilot=True,
            shopify_domain=domain,
            primary_category="home",
        )
    creator_id = str(uuid.uuid4())
    services.persist_creator(
        {
            "influencer_id": creator_id,
            "handle": f"@uat{creator_id.replace('-', '')[:8]}",
            "name": "UAT Creatr",
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
    portal.create_login(role="creator", email=email, password=creatr_secret, password_confirm=creatr_secret, creator_id=creator_id)
    row = {
        "id": str(uuid.uuid4()),
        "email": email,
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
            return {"id": row.id, "set_id": row.set_id, "role": row.role, "email": row.email, "expires_at": expires}
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
            return {"id": row.id, "set_id": row.set_id, "role": row.role, "email": row.email}
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
        found = _find_invite(row["id"], role)
        if found is None:
            code = secrets.token_urlsafe(6)
            record = {"id": code, "set_id": row["id"], "role": role, "email": row["email"], "expires_at": expires}
            if db.configured():
                from models import UatInvite

                with db.session_scope() as session:
                    session.add(UatInvite(id=code, set_id=row["id"], role=role, email=row["email"], expires_at=expires))
            _invite_rows().append(record)
            found = record
        paths[role] = f"/uat/invite/{slug}/{found['id']}"
    row["supplier_path"] = paths["seller"]
    row["creatr_path"] = paths["creator"]
    return paths


def send_invite(press_id: str = "", base_url: str = "", email: str = "") -> dict:
    """Email the two labelled links once for this press. No password is included."""
    if not enabled():
        raise RuntimeError("UAT mode is off")
    ids = current_set(email) or current_set()
    if not ids or "@" not in str(ids.get("email") or ""):
        raise ValueError("Create a UAT set before sending the invite.")
    press = (press_id or "").strip()
    if press and press == (ids.get("last_press_id") or ""):
        return {"sent": False, "duplicate": True, "invite_sent_at": ids.get("invite_sent_at"), "email": ids["email"]}
    import emailer

    try:
        base = https_base(base_url)
    except ValueError as exc:
        return {"sent": False, "duplicate": False, "reason": str(exc), "invite_sent_at": None, "email": ids["email"]}
    paths = _issue_invites(ids)
    result = emailer.send(
        "uat_invite",
        ids["email"],
        supplier_url=f"{base}{paths['seller']}",
        creatr_url=f"{base}{paths['creator']}",
    )
    provider_id = str(result.get("provider_id") or "").strip()
    if not provider_id:
        reason = str(result.get("reason") or "the mail service did not accept it")
        return {"sent": False, "duplicate": False, "reason": reason, "invite_sent_at": None, "email": email}
    sent_at = datetime.now(timezone.utc).isoformat()
    logger.info("uat_invite resend_id=%s", provider_id)
    if db.configured():
        from models import UatSet

        with db.session_scope() as session:
            row = session.get(UatSet, ids["id"])
            if row is not None:
                row.invite_sent_at = datetime.now(timezone.utc)
                row.invite_provider_id = provider_id[:64]
                row.last_press_id = press or None
    ids["invite_sent_at"] = sent_at
    ids["invite_provider_id"] = provider_id
    ids["last_press_id"] = press
    _sync(ids)
    return {"sent": True, "duplicate": False, "invite_sent_at": sent_at, "invite_provider_id": provider_id, "email": email}


def create_and_send(email: str, press_id: str, base_url: str = "") -> dict:
    created = create_set(email)
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
    if opened_email != ids.get("email"):
        raise ValueError("That invite link is not valid or has expired.")
    subject = ids["shop_id"] if opened_role == "seller" else ids["creator_id"]
    if opened_role == "seller" and ids.get("shop_id") not in db.memory_shops and not db.configured():
        from types import SimpleNamespace

        db.memory_shops[ids["shop_id"]] = SimpleNamespace(id=ids["shop_id"], name="Homeware supplier", is_pilot=True)
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
