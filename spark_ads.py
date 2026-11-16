"""TikTok Spark codes shared inside a collab.

The ad destination is the seller's own product URL with plantr, s=tt and c=ad.
The Plantr /r/ link is only an alternative. The Spark code is an authorisation
code: it is Fernet-encrypted with SHOPIFY_TOKEN_KEY, shown only to this collab's
creator and seller, and never put in a list or a log.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

import attribution
import commission
import db
import shopify_sync
from models import CollabAgreement, CollabSparkAd, CommissionAuditLog, CreatorProductLink, utcnow

HELPER = "Boosted posts must keep the creator's ad disclosure. Plantr doesn't run or pay for ads."
MIN_DAYS = 7
MAX_DAYS = 365


def add_code(
    creator_id: str,
    collab_id: str,
    *,
    post_url: str,
    spark_code: str,
    authorised_until: str,
    usage_note: Optional[str],
    consent: bool,
    actor: str,
) -> dict:
    """The collab's creator enters the code. That entry is the consent."""
    commission.require_db()
    if consent is not True:
        raise HTTPException(status_code=422, detail="Explicit consent is required to store a Spark code")
    url = attribution.safe_destination(post_url)
    if url is None or len(url) > 2000:
        raise HTTPException(status_code=422, detail="post_url must be an http(s) link")
    secret = (spark_code or "").strip()
    if not 4 <= len(secret) <= 512 or any(char in secret for char in "\r\n"):
        raise HTTPException(status_code=422, detail="Enter the Spark code from TikTok")
    until = _until(authorised_until)
    note = commission._clean_text(usage_note, field="usage_note", max_len=500)
    ciphertext = shopify_sync.encrypt(secret)
    now = utcnow()
    with db.session_scope() as session:
        agreement = _collab(session, collab_id, creator_id=creator_id)
        if agreement.status == "withdrawn":
            raise HTTPException(status_code=409, detail="This collab is withdrawn")
        link = _link_for(session, agreement)
        destination = attribution.direct_url(link.link_url, link.discount_code, source_param="tt", channel="ad")
        if not destination:
            raise HTTPException(status_code=409, detail="This collab needs a product URL before an ad destination can be created")
        row = CollabSparkAd(
            id=str(uuid.uuid4()),
            collab_id=agreement.id,
            creator_id=agreement.creator_id,
            post_url=url,
            spark_code=ciphertext,
            authorised_until=until,
            status="active",
            usage_note=note,
            ad_destination=destination,
            consented_at=now,
            created_at=now,
        )
        session.add(row)
        session.flush()
        _audit(session, agreement, actor, "spark_code_added", row)
        return _public(session, row, include_secret=True)


def revoke(creator_id: str, collab_id: str, spark_id: str, *, actor: str) -> dict:
    commission.require_db()
    with db.session_scope() as session:
        agreement = _collab(session, collab_id, creator_id=creator_id)
        row = _owned(session, agreement, spark_id)
        _expire(row)
        if row.status != "revoked":
            row.status = "revoked"
            row.revoked_at = utcnow()
            _audit(session, agreement, actor, "spark_code_revoked", row)
        return _public(session, row, include_secret=False)


def list_codes(*, collab_id: str, viewer: str, creator_id: Optional[str] = None, shop_id: Optional[str] = None) -> dict:
    commission.require_db()
    with db.session_scope() as session:
        agreement = _collab(session, collab_id, creator_id=creator_id, shop_id=shop_id)
        rows = session.scalars(
            select(CollabSparkAd).where(CollabSparkAd.collab_id == agreement.id).order_by(CollabSparkAd.created_at.desc())
        ).all()
        for row in rows:
            _expire(row)
        body = {"spark_ads": [_public(session, row, include_secret=False) for row in rows]}
        if viewer == "seller":
            body["helper_text"] = HELPER
        return body


def get_code(*, collab_id: str, spark_id: str, viewer: str, creator_id: Optional[str] = None, shop_id: Optional[str] = None) -> dict:
    """Decrypted code for this collab's seller or creator. Revoked codes stay hidden."""
    commission.require_db()
    with db.session_scope() as session:
        agreement = _collab(session, collab_id, creator_id=creator_id, shop_id=shop_id)
        row = _owned(session, agreement, spark_id)
        _expire(row)
        body = _public(session, row, include_secret=True)
        if viewer == "seller":
            body["helper_text"] = HELPER
        return body


def _until(raw: str) -> date:
    try:
        until = date.fromisoformat((raw or "").strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="authorised_until must be YYYY-MM-DD") from None
    days = (until - utcnow().date()).days
    if days < MIN_DAYS or days > MAX_DAYS:
        raise HTTPException(status_code=422, detail="TikTok authorisation must be between 7 and 365 days")
    return until


def _collab(session, collab_id: str, *, creator_id: Optional[str] = None, shop_id: Optional[str] = None) -> CollabAgreement:
    agreement = session.get(CollabAgreement, collab_id)
    if agreement is None or (creator_id and agreement.creator_id != creator_id) or (shop_id and agreement.shop_id != shop_id):
        raise HTTPException(status_code=404, detail="Collab not found")
    return agreement


def _owned(session, agreement: CollabAgreement, spark_id: str) -> CollabSparkAd:
    row = session.get(CollabSparkAd, spark_id)
    if row is None or row.collab_id != agreement.id:
        raise HTTPException(status_code=404, detail="Spark code not found")
    return row


def _link_for(session, agreement: CollabAgreement) -> CreatorProductLink:
    query = select(CreatorProductLink).where(
        CreatorProductLink.shop_id == agreement.shop_id,
        CreatorProductLink.creator_id == agreement.creator_id,
    )
    if agreement.product_id:
        query = query.where(CreatorProductLink.product_id == agreement.product_id)
    link = session.scalars(query.order_by(CreatorProductLink.created_at.asc())).first()
    if link is None or not link.discount_code or not attribution.safe_destination(link.link_url):
        raise HTTPException(status_code=409, detail="This collab needs a product URL before an ad destination can be created")
    return link


def _link_code(session, row: CollabSparkAd) -> Optional[str]:
    agreement = session.get(CollabAgreement, row.collab_id)
    if agreement is None:
        return None
    link = session.scalars(
        select(CreatorProductLink)
        .where(CreatorProductLink.shop_id == agreement.shop_id, CreatorProductLink.creator_id == agreement.creator_id)
        .order_by(CreatorProductLink.created_at.asc())
    ).first()
    if agreement.product_id and link is not None and link.product_id != agreement.product_id:
        exact = session.scalar(
            select(CreatorProductLink).where(
                CreatorProductLink.shop_id == agreement.shop_id,
                CreatorProductLink.creator_id == agreement.creator_id,
                CreatorProductLink.product_id == agreement.product_id,
            )
        )
        link = exact or link
    return link.discount_code if link is not None else None


def _expire(row: CollabSparkAd, today: Optional[date] = None) -> None:
    today = today or utcnow().date()
    if row.status == "active" and row.authorised_until < today:
        row.status = "expired"


def _public(session, row: CollabSparkAd, *, include_secret: bool) -> dict:
    _expire(row)
    body = {
        "id": row.id,
        "collab_id": row.collab_id,
        "creator_id": row.creator_id,
        "post_url": row.post_url,
        "authorised_until": row.authorised_until,
        "status": row.status,
        "usage_note": row.usage_note,
        "created_at": row.created_at,
        "revoked_at": row.revoked_at,
        "consented_at": row.consented_at,
    }
    if row.status == "active":
        body["ad_destination"] = row.ad_destination
        code = _link_code(session, row)
        if code:
            body["plantr_alternative"] = f"/r/{code}?s=tt&c=ad"
    if include_secret and row.status != "revoked":
        opened = shopify_sync.decrypt(row.spark_code)
        if opened:
            body["spark_code"] = opened
    return body


def _audit(session, agreement: CollabAgreement, actor: str, action: str, row: CollabSparkAd) -> None:
    """Append-only. The Spark code itself is not written here."""
    session.add(
        CommissionAuditLog(
            id=str(uuid.uuid4()),
            shop_id=agreement.shop_id,
            statement_id=None,
            actor_role="creator",
            actor_id=str(actor or "creator")[:255],
            action=action,
            details={
                "spark_ad_id": row.id,
                "collab_id": agreement.id,
                "authorised_until": row.authorised_until.isoformat(),
                "status": row.status,
            },
            created_at=utcnow(),
        )
    )
