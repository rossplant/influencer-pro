"""Email templates. Every message is logged, then handed to mailer.py.

Until RESEND_API_KEY and RESEND_FROM are set, nothing is delivered.
EMAIL_FROM is used only when RESEND_FROM is empty.
Invite emails set Reply-To. Other templates do not.
"""

from __future__ import annotations

import html
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import auth
import db
import mailer
import tracking
from models import EmailMessage, InviteOptOut, utcnow

logger = logging.getLogger("plantr.email")

PROVIDER_REQUIRED = (
    "Email is logged only. Set RESEND_API_KEY and RESEND_FROM to send. Nothing is sent."
)

TEMPLATES = {
    "invite": {
        "subject": "Your Plantr Collab invite",
        "body": "You have been invited. Open {url} to continue. This invite expires.",
    },
    "approval": {
        "subject": "Your Plantr Collab application",
        "body": "Your application was approved. Use invite {code}. {url}",
    },
    "pilot_ending": {
        "subject": "Your Plantr Collab pilot ends on {ends}",
        "body": (
            "Your free Plantr Collab pilot ends on {ends}. After that Plantr Collab is a paid service. "
            "{offer}If you have not chosen a plan, you will not be charged. "
            "If you have subscribed, your first charge is on {ends}; cancel before then in Billing and you will not be charged. "
            "Prices exclude GST."
        ),
    },
    "first_charge_7_days": {
        "subject": "Your first Plantr Collab charge is on {charge_date}",
        "body": (
            "Your free period ends on {charge_date}. We will then charge {amount} ex GST for {plan}, "
            "renewing automatically every {unit} until you cancel. Cancel before {charge_date} in Billing and you will not be charged."
        ),
    },
    "annual_renewal_14_days": {
        "subject": "Your Plantr Collab annual plan renews on {renews}",
        "body": (
            "Your {plan} annual plan renews on {renews} at {amount} ex GST for the year. "
            "Cancel before {renews} in Billing if you do not want to renew."
        ),
    },
    "discount_ending_14_days": {
        "subject": "Your Plantr Collab discount ends on {ends}",
        "body": (
            "Your {code} discount on {plan} ends on {ends}. From then you will pay {amount} ex GST per {unit}, "
            "renewing automatically until you cancel. You can change or cancel your plan in Billing."
        ),
    },
    "converted": {
        "subject": "Your Plantr Collab plan is active",
        "body": "Your plan is now {tier}. The supplier or you pays Plantr for the subscription. Commission is settled by the supplier.",
    },
    "signup_verification": {
        "subject": "Confirm your Plantr Collab email",
        "body": "Confirm your email by opening {url}. This link expires. If you did not sign up, you can ignore this message.",
    },
    "password_reset": {
        "subject": "Reset your Plantr Collab password",
        "body": "Reset your password by opening {url}. This link expires. If you did not ask for this, you can ignore this message.",
    },
    "admin_password_reset": {
        "subject": "Reset your Plantr Collab admin password",
        "body": "Set a new admin password: {url}. This link works once and expires in 30 minutes. If you did not ask for this, you can ignore this message.",
    },
    "first_charge_2_days": {
        "subject": "Your Plantr Collab charge is in 2 days",
        "body": (
            "Your first charge of {amount} ex GST for {plan} is on {charge_date}. "
            "Cancel before then in Billing and you will not be charged."
        ),
    },
    "growth_step_7_days": {
        "subject": "Your Growth price changes on {ends}",
        "body": (
            "On {ends} your Growth plan moves to {amount} ex GST a month. "
            "Cancel or change plan in Billing before then if you do not want that price."
        ),
    },
    "supplier_badge_notice": {
        "subject": "A note on your Plantr Collab profile",
        "body": "{notice}",
    },
    "sample_due_7": {
        "subject": "Your post about {product} is due in 7 days",
        "body": "The post for {product} is due on {due}. Put #Ad at the start of the caption. Gifted samples count as advertising.",
    },
    "sample_due_2": {
        "subject": "Your post about {product} is due in 2 days",
        "body": "The post for {product} is due on {due}. Put #Ad at the start of the caption. Gifted samples count as advertising.",
    },
    "new_match": {
        "subject": "A new match is ready",
        "body": "A match for {product} is ready. Open Matches to see the brief, the link and the code.",
    },
    "sample_accepted": {
        "subject": "Your sample request was accepted",
        "body": "The supplier accepted your sample request for {product}. The address you gave is now shared with them.",
    },
    "statement_ready": {
        "subject": "Your commission statement is ready",
        "body": "The statement for {period} is ready. Open Statements to read it or download the CSV. This is new.",
    },
    "quality_warning": {
        "subject": "Quality standard missed for {month}",
        "body": (
            "Last month missed the quality standard. {standard} "
            "One more month in a row removes earned status. You can request a review from Home."
        ),
    },
    "growth_step_2_days": {
        "subject": "Your Growth price changes in 2 days",
        "body": (
            "On {ends} your Growth plan moves to {amount} ex GST a month. "
            "Cancel or change plan in Billing before then if you do not want that price."
        ),
    },
}


# Approved invite files. Replace these files to change the copy. One button each.
INVITE_FILES = {
    "supplier_invite": "invite-supplier",
    "creatr_invite": "invite-creatr",
}
_TEMPLATE_DIR = Path(__file__).resolve().parent / "emails"
_STAGING_LINE = "Test environment — this invite is for staging only."
_DEFAULT_FROM_NAME = "Ross at Plantr Collab"
_DEFAULT_REPLY_TO = "hello@plantr-collab.com"


def invite_from_name() -> str:
    return os.getenv("INVITE_FROM_NAME", "").strip() or _DEFAULT_FROM_NAME


def invite_reply_to() -> str:
    return os.getenv("INVITE_REPLY_TO", "").strip() or _DEFAULT_REPLY_TO


def _from_address() -> str:
    raw = os.getenv("RESEND_FROM", "").strip() or os.getenv("EMAIL_FROM", "").strip()
    if "<" in raw and ">" in raw:
        return raw[raw.rfind("<") + 1:raw.rfind(">")].strip()
    return raw


def invite_from_header() -> str:
    """Display name plus the RESEND_FROM address. The address itself is unchanged."""
    name = invite_from_name()
    address = _from_address()
    if not address:
        return name
    return f"{name} <{address}>"


def show_test_banner() -> bool:
    """The test-environment line stays off production. APP_ENV wins when it is set."""
    explicit = os.getenv("APP_ENV", "").strip().lower()
    if explicit:
        return explicit != "production"
    return not tracking.production()


def _template_parts(kind: str) -> tuple[str, str, str, str]:
    stem = INVITE_FILES[kind]
    html_body = (_TEMPLATE_DIR / f"{stem}.html").read_text(encoding="utf-8")
    text = (_TEMPLATE_DIR / f"{stem}.txt").read_text(encoding="utf-8")
    title_at = html_body.find("<title>")
    title_end = html_body.find("</title>", title_at)
    subject = html_body[title_at + len("<title>"):title_end].strip()
    marker = 'mso-hide:all;">'
    pre_at = html_body.find(marker)
    pre_end = html_body.find("<", pre_at + len(marker))
    preheader = " ".join(html_body[pre_at + len(marker):pre_end].split("&nbsp;", 1)[0].split())
    if not html_body.endswith("\n"):
        html_body += "\n"
    return subject, preheader, html_body, text


def invite_preheader(kind: str) -> str:
    return _template_parts(kind)[1]


def _without_staging(html_body: str, text: str) -> tuple[str, str]:
    start = html_body.find("<!-- STAGING ONLY:")
    if start != -1:
        end = html_body.find("</tr>", start)
        if end != -1:
            html_body = html_body[:start] + html_body[end + len("</tr>"):]
    if text.rstrip("\n").endswith(_STAGING_LINE):
        trimmed = text.rstrip("\n")
        trimmed = trimmed[: -len(_STAGING_LINE)].rstrip("\n")
        text = trimmed + ("\n" if text.endswith("\n") else "")
    return html_body, text


def render_invite(
    kind: str,
    *,
    url: str = "",
    first_name: str = "",
    brand_name: str = "",
    unsubscribe_url: str = "",
    show_banner: Optional[bool] = None,
) -> tuple[str, str, str]:
    """Fill the approved supplier or Creatr template. Returns subject, text, HTML."""
    subject, _preheader, html_body, text = _template_parts(kind)
    if show_banner is None:
        show_banner = show_test_banner()
    if not show_banner:
        html_body, text = _without_staging(html_body, text)
    given = (first_name or "").strip()
    if given:
        subject = subject.replace("{{first_name}}", given)
    else:
        subject = subject.replace("{{first_name}}, your ", "Your ")
        subject = subject.replace("{{first_name}}, ", "")
        subject = subject.replace("{{first_name}}", "")
    greeting = given or "there"
    brand = (brand_name or "").strip() or "your shop"
    fields = {
        "first_name": greeting,
        "brand_name": brand,
        "invite_link": url,
        "unsubscribe_url": unsubscribe_url,
    }
    for key, value in fields.items():
        text = text.replace("{{" + key + "}}", value)
        html_body = html_body.replace("{{" + key + "}}", html.escape(value, quote=True))
    return subject, text, html_body


def invite_opt_out_token(email: str) -> str:
    payload = {
        "purpose": "invite-opt-out",
        "email": email.strip().lower(),
        "exp": int(time.time()) + 180 * 24 * 3600,
    }
    body = auth._b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{auth._sign(body)}"


def invite_opt_out_url(base: str, email: str) -> str:
    return f"{base.rstrip('/')}/invite/opt-out/{invite_opt_out_token(email)}"


def read_opt_out_token(token: str) -> str:
    payload = auth._read_signed(token)
    if not payload or payload.get("purpose") != "invite-opt-out":
        return ""
    return str(payload.get("email") or "").strip().lower()


def invite_opted_out(email: str) -> bool:
    wanted = email.strip().lower()
    if not wanted:
        return False
    if db.configured():
        with db.session_scope() as session:
            if session.get(InviteOptOut, wanted) is not None:
                return True
    return wanted in getattr(db, "memory_invite_opt_outs", set())


def record_invite_opt_out(email: str) -> None:
    wanted = email.strip().lower()
    if not wanted:
        return
    if not hasattr(db, "memory_invite_opt_outs"):
        db.memory_invite_opt_outs = set()
    db.memory_invite_opt_outs.add(wanted)
    if db.configured():
        with db.session_scope() as session:
            if session.get(InviteOptOut, wanted) is None:
                session.add(InviteOptOut(email=wanted))


def send(template_key: str, to_email: str, **values) -> dict:
    sender = values.pop("_mailer", None) if "_mailer" in values else None
    if template_key in INVITE_FILES:
        subject, body, html_body = render_invite(
            template_key,
            url=str(values.get("url") or ""),
            first_name=str(values.get("first_name") or ""),
            brand_name=str(values.get("brand_name") or ""),
            unsubscribe_url=str(values.get("unsubscribe_url") or ""),
        )
    else:
        spec = TEMPLATES[template_key]
        subject = spec["subject"].format(**values)
        body = spec["body"].format(**values)
        html_body = None
    row = {
        "id": str(uuid.uuid4()),
        "template_key": template_key,
        "to_email": to_email,
        "subject": subject,
        "body": body,
        "created_at": utcnow(),
    }
    if db.configured():
        with db.session_scope() as session:
            session.add(EmailMessage(**row))
    else:
        db.memory_emails.append(row)
    send_kwargs = {
        "to": to_email,
        "subject": subject,
        "text": body,
        "html": html_body,
        "idempotency_key": f"{template_key}:{row['id']}",
    }
    if template_key in INVITE_FILES:
        send_kwargs["reply_to"] = invite_reply_to()
        header = invite_from_header()
        if "<" in header:
            send_kwargs["sender"] = header
    result = (sender or mailer.default()).send(**send_kwargs)
    sent = result.get("status") == mailer.SENT
    logger.info("email template=%s to=%s status=%s", template_key, _mask(to_email), result.get("status"))
    out = {"logged": True, "sent": sent, "status": result.get("status"), "provider_id": result.get("id"), "template": template_key}
    if result.get("reason"):
        out["reason"] = result["reason"]
    if not sent:
        out["notice"] = PROVIDER_REQUIRED if result.get("status") == mailer.NO_PROVIDER else "Email provider returned an error."
    return out


def _mask(email: str) -> str:
    if "@" not in email:
        return "redacted"
    name, domain = email.split("@", 1)
    return f"{name[:1]}***@{domain}"
