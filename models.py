"""SQLAlchemy models for Plantr persistence.

Pilot tables (shops, creators, products, events, shortlists, shortlist_items,
briefs, api_requests) are written by the API. subscriptions,
subscription_changes, monthly_mrr_snapshots, and attributed_orders are created
here and have no endpoints yet.

MRR rules for monthly_mrr_snapshots (and for the mrr value returned with a shop):
- A monthly price is the MRR.
- An annual price is divided by 12.
- Free and trial contribute 0.
- Starter, the entry plan_tier, is 0.

events is append-only: the application inserts rows and does not update or
delete them. api_requests stores no request or response bodies.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    shopify_domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="starter", server_default="starter")
    plan_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    is_pilot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    products_enriched: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    matches_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    briefs_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_enrich_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_match_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_brief_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    products: Mapped[list["Product"]] = relationship(back_populates="shop")


class Creator(Base):
    """Influencer profile from influencer_schema.json, plus interests, restrictions, platforms, and follower band."""

    __tablename__ = "creators"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    handle: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    interests: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    expertise_level: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    preferred_promotion_styles: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    audience_demographics: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    content_formats: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    average_engagement_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    past_categories: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    restrictions: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    availability: Mapped[str] = mapped_column(String(32), nullable=False, default="open", server_default="open")
    commission_preference: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    platforms: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    follower_band: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    profile: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "light IS NULL OR light IN ('low', 'medium', 'bright')",
            name="ck_products_light",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description_short: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_long: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    images: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    pet_safe: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    light: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    care_level: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    size: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    claims: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    shop: Mapped[Shop] = relationship(back_populates="products")


class Event(Base):
    """Append-only shop and creator activity.

    Every event whose shop_id is set carries plan_tier, plan_status, and the
    usage counters in properties. Rows are inserted, never updated or deleted.
    """

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("actor IN ('system', 'operator', 'api')", name="ck_events_actor"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    shop_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shops.id"), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    properties: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)


class Shortlist(Base):
    __tablename__ = "shortlists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shops.id"), nullable=True, index=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    match_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    shop: Mapped[Optional[Shop]] = relationship()
    items: Mapped[list["ShortlistItem"]] = relationship(back_populates="shortlist")


class ShortlistItem(Base):
    __tablename__ = "shortlist_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shortlist_id: Mapped[str] = mapped_column(ForeignKey("shortlists.id"), nullable=False, index=True)
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    shortlist: Mapped[Shortlist] = relationship(back_populates="items")


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        CheckConstraint("source IN ('grok', 'fallback')", name="ck_briefs_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    disclosure_line: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    product: Mapped[Product] = relationship()


class ApiRequest(Base):
    """One row per HTTP request when a database is configured.

    The raw API key, request body, and response body are never stored.
    api_key_id is a short hash label. Rows older than 90 days are purged on startup.
    """

    __tablename__ = "api_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    route: Mapped[str] = mapped_column(String(512), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    api_key_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    grok_tokens_prompt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grok_tokens_completion: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grok_fallback: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)


class Subscription(Base):
    """Schema only. No endpoint writes this table in this version."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    interval: Mapped[str] = mapped_column(String(16), nullable=False, default="month")
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class SubscriptionChange(Base):
    """Schema only. change_type includes 'contraction'. upgrade_trigger_snapshot is JSON."""

    __tablename__ = "subscription_changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subscription_id: Mapped[Optional[str]] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    paid_to_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    upgrade_trigger_snapshot: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    from_tier: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    to_tier: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MonthlyMrrSnapshot(Base):
    """Schema only. One row per shop per month.

    MRR rules:
    - A monthly price is the MRR.
    - An annual price is divided by 12.
    - Free and trial contribute 0.
    - Starter, the entry plan_tier, is 0.
    """

    __tablename__ = "monthly_mrr_snapshots"
    __table_args__ = (UniqueConstraint("shop_id", "month", name="uq_mrr_shop_month"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    interval: Mapped[str] = mapped_column(String(16), nullable=False, default="month")
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    mrr: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AttributedOrder(Base):
    """Schema only. No endpoint writes this table in this version."""

    __tablename__ = "attributed_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    external_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

