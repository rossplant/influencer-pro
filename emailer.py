"""Email templates with a logging backend.

No message is delivered. A provider has to be connected before launch.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import db
from models import EmailMessage, utcnow

logger = logging.getLogger("plantr.email")

PROVIDER_REQUIRED = (
    "Email is logged only. Connect an email provider before launch. Nothing is sent."
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
    "pilot_ending_7_days": {
        "subject": "Your Plantr Collab pilot ends in 7 days",
        "body": "Your pilot window ends on {ends}. Add a payment method if you want to stay on the paid plan.",
    },
    "converted": {
        "subject": "Your Plantr Collab plan is active",
        "body": "Your plan is now {tier}. The supplier or you pays Plantr for the subscription. Commission is settled by the supplier.",
    },
}


def send(template_key: str, to_email: str, **values) -> dict:
    spec = TEMPLATES[template_key]
    subject = spec["subject"]
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
    logger.info("email logged template=%s to=%s provider=none", template_key, _mask(to_email))
    return {"logged": True, "sent": False, "template": template_key, "notice": PROVIDER_REQUIRED}


def _mask(email: str) -> str:
    if "@" not in email:
        return "redacted"
    name, domain = email.split("@", 1)
    return f"{name[:1]}***@{domain}"
