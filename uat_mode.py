"""UAT mode is staging only. Production never turns it on."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import db

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
        from sqlalchemy import delete

        from models import UatFeedback

        with db.session_scope() as session:
            session.execute(delete(UatFeedback))
    db.memory_uat_feedback = []
    db.memory_uat_ids = {}
    db.memory_uat_sales = []


def create_set(notify_email: str) -> dict:
    """Isolated supplier, products, placeholder scenes, and a Creatr. No password is returned or logged."""
    if not enabled():
        raise RuntimeError("UAT mode is off")
    if os.getenv("STRIPE_SECRET_KEY", "").startswith("sk_live"):
        raise RuntimeError("UAT refuses a live Stripe key")
    email = (notify_email or "").strip().lower()
    if "@" not in email:
        raise ValueError("Enter an email address for the password link")
    shop_id = str(uuid.uuid4())
    creator_id = str(uuid.uuid4())
    products = []
    import secrets

    import account_mail
    import auth
    import emailer
    import listing_studio
    import portal

    secret = secrets.token_urlsafe(24)
    creatr_secret = secrets.token_urlsafe(24)
    from datetime import datetime, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    db.memory_shops[shop_id] = SimpleNamespace(
        id=shop_id,
        name="Homeware supplier",
        business_name="Homeware supplier",
        is_pilot=True,
        shopify_domain=f"uat-{shop_id[:8]}.myshopify.com",
        subscription_status=None,
        plan_tier="starter",
    )
    db.memory_creators[creator_id] = {"id": creator_id, "handle": "@uat"}
    db.memory_creator_objects[creator_id] = SimpleNamespace(
        id=creator_id, handle="@uat", founding_number=None, signup_campaign="founding_curators", audience=45000
    )
    portal.create_login(role="seller", email=email, password=secret, password_confirm=secret, shop_id=shop_id)
    portal.create_login(role="creator", email=email, password=creatr_secret, password_confirm=creatr_secret, creator_id=creator_id)
    for title in ("Stoneware bowl", "Ribbed knit", "Pump bottle"):
        product_id = str(uuid.uuid4())
        product = {"id": product_id, "title": title, "shop_id": shop_id, "samples_enabled": True}
        products.append(product)
        if not hasattr(db, "memory_products") or not isinstance(db.memory_products, dict):
            db.memory_products = {}
        db.memory_products[product_id] = SimpleNamespace(
            id=product_id, title=title, shop_id=shop_id, samples_enabled=True, commission_rate=10, image_url=None
        )
        listing_studio.lock_sheet(product_id, {"title": title, "category": "home", "confirmed": True, "piece_count": "1"})
        state = listing_studio.state_for(product_id)
        state["scenes"] = [{"id": str(uuid.uuid4()), "type": "in a home", "status": "Approved", "approved": True, "image": "placeholder", "reasons": []}]
        state["angles"] = [{"name": "Tuesday place", "hook": "Ready when you are.", "caption": f"#Ad {title} for an ordinary night.", "approved": True}]
        state["status"] = "Approved"
    shortlist_id = str(uuid.uuid4())
    db.memory_shortlists.append(SimpleNamespace(id=shortlist_id, shop_id=shop_id, product_id=products[0]["id"]))
    db.memory_shortlist_items.append(
        SimpleNamespace(id=str(uuid.uuid4()), shortlist_id=shortlist_id, creator_id=creator_id, accepted_at=now, creator=None)
    )
    db.memory_uat_ids = {
        "shop_id": shop_id,
        "creator_id": creator_id,
        "email": email,
        "products": products,
        "billing": "sandbox",
        "audience": 45000,
        "invite_sent": False,
    }
    return {"shop_id": shop_id, "creator_id": creator_id, "products": len(products), "password_sent": False, "billing": "sandbox"}


def send_invite() -> dict:
    """Email the two labelled links only after an admin asks. No password is included."""
    if not enabled():
        raise RuntimeError("UAT mode is off")
    ids = getattr(db, "memory_uat_ids", None) or {}
    email = str(ids.get("email") or "")
    if "@" not in email:
        raise ValueError("Create a UAT set before sending the invite")
    import auth
    import emailer

    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    supplier_token = auth.issue_form_token(f"uat-set:seller:{email}")
    creatr_token = auth.issue_form_token(f"uat-set:creator:{email}")
    emailer.send(
        "uat_invite",
        email,
        supplier_url=f"{base}/uat/invite/{supplier_token}",
        creatr_url=f"{base}/uat/invite/{creatr_token}",
    )
    ids["invite_sent"] = True
    return {"sent": True}


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


def activate_invite(token: str, *, born, attested: bool, password: str) -> dict:
    import creatr_age
    import portal

    opened = open_invite(token)
    blocked = creatr_age.check_signup(born=born, attested=attested)
    if blocked:
        raise ValueError(blocked)
    ids = getattr(db, "memory_uat_ids", None) or {}
    if opened["email"] != ids.get("email"):
        raise ValueError("That invite link is not valid")
    subject = ids["shop_id"] if opened["role"] == "seller" else ids["creator_id"]
    portal.create_login(
        role=opened["role"],
        email=opened["email"],
        password=password,
        password_confirm=password,
        shop_id=ids["shop_id"] if opened["role"] == "seller" else None,
        creator_id=ids["creator_id"] if opened["role"] == "creator" else None,
    )
    return {"role": opened["role"], "email": opened["email"], "subject_id": subject}


def simulate_sale(code: str = "UAT") -> dict:
    if not enabled():
        raise RuntimeError("UAT mode is off")
    if not hasattr(db, "memory_uat_sales"):
        db.memory_uat_sales = []
    row = {"id": str(uuid.uuid4()), "code": (code or "UAT").strip(), "source": "uat"}
    db.memory_uat_sales.append(row)
    return row
