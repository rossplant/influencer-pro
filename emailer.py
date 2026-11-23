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
    "uat_invite": {
        "subject": "Your Plantr Collab test invites",
        "body": (
            "Supplier invite (homeware supplier): {supplier_url}. "
            "Creatr invite (45,000 audience): {creatr_url}. "
            "All users must be 18 or over. Set a password on each link. These are test accounts."
        ),
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


def send(template_key: str, to_email: str, **values) -> dict:
    spec = TEMPLATES[template_key]
    subject = spec["subject"].format(**values)
    body = spec["body"].format(**values)
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
        to=to_email, subject=subject, text=body, idempotency_key=f"{template_key}:{row['id']}"
    )
    sent = result.get("status") == mailer.SENT
    logger.info("email template=%s to=%s status=%s", template_key, _mask(to_email), result.get("status"))
    out = {"logged": True, "sent": sent, "status": result.get("status"), "provider_id": result.get("id"), "template": template_key}
    if not sent:
        out["notice"] = PROVIDER_REQUIRED if result.get("status") == mailer.NO_PROVIDER else "Email provider returned an error."
    return out


def _mask(email: str) -> str:
    if "@" not in email:
        return "redacted"
    name, domain = email.split("@", 1)
    return f"{name[:1]}***@{domain}"
