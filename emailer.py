"""Email templates. Every message is logged, then handed to mailer.py.

Until RESEND_API_KEY and EMAIL_FROM are set, nothing is delivered.
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
    "Email is logged only. Set RESEND_API_KEY and EMAIL_FROM to send. Nothing is sent."
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
