"""Sign-up acceptance wording and acceptance records.

Wording is from 09_Signup_Acceptance_Wording (Draft v0.3). It is a draft for
legal review. Every checkbox starts unticked. The email marketing checkbox is
separate from the cookie choice and is always optional.
"""

from __future__ import annotations

import html
import os
import re
import uuid
from typing import Optional

import db
from models import TermsAcceptance, utcnow

TERMS_VERSION = os.getenv("TERMS_VERSION", "").strip() or "draft-v0.4.6"
WORDING_VERSION = "09_Signup_Acceptance_Wording draft v0.3"

SELLER_PANEL = (
    "Plantr Collab is a paid subscription service. Plans: Starter A$29, Growth A$79 or Scale A$229 per month "
    "(or 10 times the monthly price per year). Prices exclude GST. We are not yet registered for GST, so no GST "
    "is charged now. If we register, we will give you 30 days' notice before adding GST. Plans renew automatically "
    "until you cancel. Plantr takes no cut of your sales. You pay Creatrs' commission directly. "
    "Pilot suppliers are at no charge until 20 January 2027 unless cancelled, then A$39.50 a month "
    "for 3 months, then A$79 a month, but only if they choose to subscribe."
)
CREATOR_PANEL = (
    "Plantr Collab is a paid subscription service for Creatrs. Plans: Membr A$15, Curatr A$19 or Curatr Pro A$49 "
    "per month (or 10 times the monthly price per year). Prices exclude GST (not charged until we register for GST, "
    "with 30 days' notice). Plans renew automatically until you cancel. Foundr is a permanent invite-only status. "
    "Foundrs are at no charge until 20 January 2027 unless cancelled, then Curatr at A$9 a month for 12 months with code FOUNDR9, then A$19 a month, but only "
    "if they choose to subscribe."
)

SELLER_APPLY = (
    "I understand Plantr Collab is a paid service after the free pilot ends on 20 January 2027. I have read the "
    "Privacy Policy and agree that Plantr may use these details to assess my application. Applying does not create "
    "a contract or commit me to pay."
)
SELLER_AUTHORITY = "All users must be 18 or over. You must be 18 or over and authorised to apply for this business."
CREATOR_AGE = "I confirm I am 18 or over. All users must be 18 or over."
CREATOR_APPLY = (
    "I understand Creatr plans are paid (from A$15 a month ex GST), and that Foundrs are at no charge only until 20 January "
    "2027. I have read the Privacy Policy. I agree that Plantr may review my application and public social profile "
    "metrics, using automated tools with a final decision by a person. Applying does not create a contract or commit "
    "me to pay."
)
SELLER_JOIN_TERMS = (
    "I am authorised to act for this business. I agree to the Platform Terms of Service, Seller Terms, Seller Pilot "
    "Agreement, Data Processing Addendum and Acceptable Use Policy, and I have read the Privacy Policy."
)
SELLER_JOIN_PILOT = (
    "I understand the pilot is at no charge from 20 November 2026 to 20 January 2027 unless cancelled. After that, Plantr Collab is paid. If I "
    "choose to continue on Growth, I will pay A$39.50 a month ex GST for 3 months, then A$79 a month ex GST, renewing "
    "automatically until I cancel. I will not be charged unless I choose a plan and enter payment details."
)
CREATOR_JOIN_TERMS = (
    "I am 18 or over. All users must be 18 or over. I agree to the Platform Terms of Service, Creatr and Foundr Terms and Acceptable Use "
    "Policy. I will clearly disclose every paid or commission-earning promotion (for example with #ad and the "
    "platform's paid-partnership label). I have read the Privacy Policy."
)
CREATOR_JOIN_PILOT = (
    "I understand the Foundr pilot is at no charge until 20 January 2027 unless cancelled. Foundr stays an invite-only status. After that, if I choose to continue, Curatr is A$9 "
    "a month ex GST for 12 months with code FOUNDR9, then A$19 a month ex GST, renewing automatically until I cancel. "
    "I will not be charged unless I choose a plan and enter payment details."
)
CHECKOUT_AUTHORISE = (
    "I agree to the {terms} and authorise Plantr to charge my payment method on a recurring basis as described above."
)
EMAIL_CONSENT = "Send me Plantr news, tips and offers by email. I can unsubscribe at any time."

APPLY_DOCUMENTS = {"privacy_policy": TERMS_VERSION, "acceptance_wording": WORDING_VERSION}
SELLER_JOIN_DOCUMENTS = {
    "platform_terms": TERMS_VERSION,
    "seller_terms": TERMS_VERSION,
    "seller_pilot_agreement": TERMS_VERSION,
    "data_processing_addendum": TERMS_VERSION,
    "acceptable_use_policy": TERMS_VERSION,
    "privacy_policy": TERMS_VERSION,
}
CREATOR_JOIN_DOCUMENTS = {
    "platform_terms": TERMS_VERSION,
    "creator_and_foundr_terms": TERMS_VERSION,
    "acceptable_use_policy": TERMS_VERSION,
    "privacy_policy": TERMS_VERSION,
}

ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
OPENING_SOON = (
    "Pilot applications are opening soon. We are finishing our company registration and will open applications "
    "once the operator's details can be shown here. Please check back shortly."
)

DRAFT_NOTICE = "Draft — pending legal review"

# These pages only repeat acceptance wording already stored here. They are not a full legal instrument.
LEGAL_PAGES = {
    "platform": ("Terms", "The platform terms are named in the join acceptance. [PLATFORM TERMS PENDING LEGAL REVIEW]"),
    "seller": ("Seller terms", SELLER_PANEL + " [SELLER TERMS PENDING LEGAL REVIEW]"),
    "seller-pilot": ("Supplier pilot agreement", SELLER_JOIN_PILOT + " [SUPPLIER PILOT AGREEMENT PENDING LEGAL REVIEW]"),
    "data-processing": ("Data processing addendum", "[DATA PROCESSING ADDENDUM PENDING LEGAL REVIEW]"),
    "acceptable-use": ("Acceptable use", "[ACCEPTABLE USE POLICY PENDING LEGAL REVIEW]"),
    "creatr-foundr": ("Creatr and Foundr terms", CREATOR_PANEL + " " + CREATOR_JOIN_TERMS + " [CREATR AND FOUNDR TERMS PENDING LEGAL REVIEW]"),
    "fees": ("Fees and plans", SELLER_PANEL + " " + CREATOR_PANEL + " [FEE SCHEDULE PENDING LEGAL REVIEW]"),
    "integration-data": ("Integration data", "[INTEGRATION DATA TERMS PENDING LEGAL REVIEW]"),
    "privacy": ("Privacy", ""),
}


def clean_abn(raw: str) -> Optional[str]:
    """Return an 11-digit ABN, None when blank, or raise ValueError when it is not a valid ABN."""
    digits = re.sub(r"\s+", "", raw or "")
    if not digits:
        return None
    if not re.fullmatch(r"\d{11}", digits):
        raise ValueError("An ABN has 11 digits.")
    values = [int(ch) for ch in digits]
    values[0] -= 1
    if sum(value * weight for value, weight in zip(values, ABN_WEIGHTS)) % 89 != 0:
        raise ValueError("That ABN is not valid. Check the 11 digits.")
    return digits


def checkbox(name: str, text: str, *, required: bool) -> str:
    req = " required" if required else ""
    tag = "" if required else " (optional)"
    return (
        f"<label class='check'><input name='{html.escape(name)}' type='checkbox' value='yes'{req}> "
        f"{html.escape(text)}{tag}</label>"
    )


def legal_page(slug: str, *, privileged: bool = False) -> str:
    import brand_ui

    title, body = LEGAL_PAGES[slug]
    if not privileged:
        content = f"<h1>{html.escape(title)}</h1><p>{html.escape(DRAFT_NOTICE)}</p>"
    else:
        content = (
            f"<p class='banner'>DRAFT — NEEDS LEGAL REVIEW (NOT BINDING)</p>"
            f"<h1>{html.escape(title)}</h1><p>{html.escape(body)}</p>"
        )
    return brand_ui.document(title, content)


def record(
    *,
    subject_type: str,
    subject_id: str,
    context: str,
    documents: dict,
    texts: list[str],
    price_shown: str = "",
    confirmations: Optional[dict] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "context": context,
        "terms_version": TERMS_VERSION,
        "documents": dict(documents),
        "text_shown": "\n".join(texts),
        "price_shown": price_shown or None,
        "confirmations": dict(confirmations or {}),
        "ip": (ip or "")[:64] or None,
        "user_agent": (user_agent or "")[:512] or None,
        "accepted_at": utcnow(),
    }
    if str(TERMS_VERSION).startswith("draft"):
        row["confirmations"]["binding"] = False
        row["confirmations"]["acceptance_label"] = "UAT test acceptance"
        row["text_shown"] = row["text_shown"] + "\nUAT test acceptance. Not a binding acceptance of draft text."
    if db.configured():
        with db.session_scope() as session:
            session.add(TermsAcceptance(**row))
    else:
        db.memory_terms_acceptances.append(row)
    return row


def acceptances(subject_id: str) -> list[dict]:
    if db.configured():
        from sqlalchemy import select

        with db.session_scope() as session:
            rows = session.scalars(select(TermsAcceptance).where(TermsAcceptance.subject_id == subject_id)).all()
            return [{c.name: getattr(r, c.name) for c in TermsAcceptance.__table__.columns} for r in rows]
    return [row for row in db.memory_terms_acceptances if row["subject_id"] == subject_id]
