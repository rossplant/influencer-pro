"""Email templates. Every message is logged, then handed to mailer.py.

Until RESEND_API_KEY and RESEND_FROM are set, nothing is delivered.
EMAIL_FROM is used only when RESEND_FROM is empty. There is no reply-to.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import db
import mailer
from models import EmailMessage, utcnow

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


# Placeholder invite copy. Marketing replaces these fields. Keep each email under about 120 words.
# Per-role emails have one button. The combined UAT email carries both journeys.
INVITE_COPY = {
    "supplier_invite": {
        "subject": "Your Plantr Collab supplier test invite",
        "preheader": "A test account for the supplier journey. All users must be 18 or over.",
        "headline": "Supplier test invite",
        "body": "This is a test account for the supplier journey. Set a password to continue. All users must be 18 or over.",
        "buttons": (("Open the supplier invite", "url"),),
        "footer": "Plantr Collab. No cut of your sales.",
    },
    "creatr_invite": {
        "subject": "Your Plantr Collab Creatr test invite",
        "preheader": "A test account for the Creatr journey. All users must be 18 or over.",
        "headline": "Creatr test invite",
        "body": "This is a test account for the Creatr journey. Set a password to continue. All users must be 18 or over.",
        "buttons": (("Open the Creatr invite", "url"),),
        "footer": "Plantr Collab. No cut of your sales.",
    },
    "uat_invite": {
        "subject": "Your Plantr Collab test invites",
        "preheader": "Test accounts for two journeys. All users must be 18 or over.",
        "headline": "Your test invites",
        "body": "These are test accounts. There are two journeys. All users must be 18 or over.",
        "buttons": (
            ("Open the supplier invite", "supplier_url"),
            ("Open the Creatr invite", "creatr_url"),
        ),
        "footer": "Plantr Collab. No cut of your sales.",
    },
}


def render_invite(kind: str, **urls: str) -> tuple[str, str, str]:
    """Return subject, plain text, and HTML from INVITE_COPY. Each URL is on its own line."""
    spec = INVITE_COPY[kind]
    buttons = []
    for label, key in spec["buttons"]:
        href = str(urls.get(key) or "")
        buttons.append((label, href))
    text_lines = [spec["preheader"], "", spec["headline"], "", spec["body"], ""]
    for label, href in buttons:
        text_lines.extend([label, f"<{href}>", ""])
    text_lines.append(spec["footer"])
    text = "\n".join(text_lines).strip() + "\n"
    button_html = "".join(
        f"<p><a href=\"{href}\" style=\"background:#C2127F;color:#ffffff;padding:12px 18px;border-radius:8px;text-decoration:none;display:inline-block\">{label}</a></p>"
        for label, href in buttons
    )
    html_body = (
        "<!DOCTYPE html><html><body style=\"margin:0;padding:24px;font-family:Inter,Arial,sans-serif;color:#17211c;background:#ffffff\">"
        f"<div style=\"display:none;max-height:0;overflow:hidden\">{spec['preheader']}</div>"
        "<p style=\"margin:0 0 8px;font-weight:700;color:#C2127F\">Plantr Collab</p>"
        f"<h1 style=\"font-size:24px\">{spec['headline']}</h1>"
        f"<p>{spec['body']}</p>"
        f"{button_html}"
        f"<p>{spec['footer']}</p>"
        "</body></html>"
    )
    return spec["subject"], text, html_body


def uat_invite_parts(supplier_url: str, creatr_url: str) -> tuple[str, str]:
    """Plain text and HTML for the combined UAT invite. Copy lives in INVITE_COPY."""
    _subject, text, html_body = render_invite("uat_invite", supplier_url=supplier_url, creatr_url=creatr_url)
    return text, html_body


def send(template_key: str, to_email: str, **values) -> dict:
    if template_key in INVITE_COPY:
        subject, body, html_body = render_invite(template_key, **values)
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
    sender = values.pop("_mailer", None) if "_mailer" in values else None
    result = (sender or mailer.default()).send(
        to=to_email, subject=subject, text=body, html=html_body, idempotency_key=f"{template_key}:{row['id']}"
    )
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
