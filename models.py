"""SQLAlchemy models for Plantr persistence.

Pilot tables (shops, creators, products, events, shortlists, shortlist_items,
briefs, api_requests) are written by the API. subscriptions,
subscription_changes, monthly_mrr_snapshots, and attributed_orders are created
here and have no endpoints yet.

MRR rules for monthly_mrr_snapshots (and for the mrr value returned with a shop):
- Monthly list price is the MRR (growth 49, scale 149).
- Annual list price is divided by 12 (growth 490, scale 1490).
- Free, trial, trialing, and starter contribute 0.

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
    Index,
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


class Category(Base):
    """Shared taxonomy. Top-level rows are seeded. parent_slug is an optional subcategory."""

    __tablename__ = "categories"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    shopify_domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="starter", server_default="starter")
    plan_status: Mapped[str] = mapped_column(String(32), nullable=False, default="free", server_default="free")
    is_pilot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0", index=True)
    is_underserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    underserved_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True, index=True)
    market: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True, index=True)
    primary_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    products_enriched: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    matches_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    briefs_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_enrich_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_match_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_brief_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    shopify_connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_attributed_sale_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_match_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_campaign_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    products: Mapped[list["Product"]] = relationship(back_populates="shop")


class Creator(Base):
    """Influencer profile from influencer_schema.json, plus interests, restrictions, platforms, and follower band."""

    __tablename__ = "creators"
    __table_args__ = (
        CheckConstraint(
            "metrics_source IN ('self_reported', 'platform_api', 'manual_verified')",
            name="ck_creators_metrics_source",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    handle: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    interests: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    expertise_level: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    preferred_promotion_styles: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    audience_demographics: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    content_formats: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    past_categories: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    restrictions: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    excluded_categories: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    excluded_claims: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    active_categories: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    desired_categories: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    avg_views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    engagement_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    metrics_source: Mapped[str] = mapped_column(String(32), nullable=False, default="self_reported", server_default="self_reported")
    metrics_as_of: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    est_conversion_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_order_value: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    typical_price_band: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    availability: Mapped[str] = mapped_column(String(32), nullable=False, default="open", server_default="open")
    commission_preference: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    platforms: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    follower_band: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    profile_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_match_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_post_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    profile: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Product(Base):
    """A catalogue item for a brand. Attributes are free-form; claims keep a source."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("source IN ('shopify', 'csv', 'manual', 'api')", name="ck_products_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description_short: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_long: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    images: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD", server_default="USD")
    tags: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    claims: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    commission_rate: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    shop: Mapped[Shop] = relationship(back_populates="products")


class Event(Base):
    """Append-only shop and creator activity.

    Shop events carry plan_tier and plan_status in properties. Catalogue and
    period counters are columns. Rows are inserted, never updated or deleted.
    """

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("actor IN ('system', 'operator', 'api')", name="ck_events_actor"),
        Index("ix_events_entity_time", "entity_type", "entity_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    shop_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shops.id"), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    products_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    product_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    matches_count_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    match_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    enrichments_count_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    briefs_count_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    billing_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    limit_hit: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
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
    __table_args__ = (
        UniqueConstraint("shortlist_id", "creator_id", name="uq_shortlist_items_shortlist_creator"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shortlist_id: Mapped[str] = mapped_column(ForeignKey("shortlists.id"), nullable=False, index=True)
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    declined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decline_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    shortlist: Mapped[Shortlist] = relationship(back_populates="items")


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        CheckConstraint("source IN ('grok', 'fallback')", name="ck_briefs_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True)
    shortlist_item_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shortlist_items.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    disclosure_line: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    product: Mapped[Product] = relationship()
    shop: Mapped[Shop] = relationship()


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
    request_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    grok_tokens_prompt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grok_tokens_completion: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grok_fallback: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    grok_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grok_model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class Subscription(Base):
    """Schema only. No endpoint writes this table in this version."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    interval: Mapped[str] = mapped_column(String(16), nullable=False, default="month")
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    mrr: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class SubscriptionChange(Base):
    """Schema only. Paid-to-free is change_type='contraction' with paid_to_free true."""

    __tablename__ = "subscription_changes"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('new', 'expansion', 'contraction', 'churn', 'reactivation', 'trial_started', 'trial_converted')",
            name="ck_subscription_changes_change_type",
        ),
        Index("ix_subscription_changes_shop_created", "shop_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subscription_id: Mapped[Optional[str]] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    paid_to_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    upgrade_trigger_snapshot: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    from_tier: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    to_tier: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    from_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    to_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    from_mrr: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    to_mrr: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    mrr_delta: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    trigger_limit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MonthlyMrrSnapshot(Base):
    """Schema only. One row per shop per month.

    MRR rules:
    - Monthly list price is the MRR (growth 49, scale 149).
    - Annual list price / 12 is the MRR (growth 490, scale 1490).
    - Free, trial, trialing, and starter contribute 0.
    """

    __tablename__ = "monthly_mrr_snapshots"
    __table_args__ = (UniqueConstraint("shop_id", "month", name="uq_mrr_shop_month"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    interval: Mapped[str] = mapped_column(String(16), nullable=False, default="month")
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    mrr: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AttributedOrder(Base):
    """Schema only. No endpoint writes this table in this version."""

    __tablename__ = "attributed_orders"
    __table_args__ = (
        UniqueConstraint("platform", "external_order_id", name="uq_orders_platform_external"),
        Index("ix_orders_shop_attribution_date", "shop_id", "attribution_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True, index=True)
    market: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    external_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_creator_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    gmv_gross: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    refunded_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    cancelled_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    net_gmv: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True)
    shortlist_item_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shortlist_items.id"), nullable=True)
    brief_id: Mapped[Optional[str]] = mapped_column(ForeignKey("briefs.id"), nullable=True)
    attribution_date: Mapped[date] = mapped_column(Date, nullable=False)
    click_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    click_window_source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    settlement_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    estimated_commission: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    actual_commission: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    fee_revenue: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    fee_rate: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    ingest_source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class CreatorProductLink(Base):
    """Schema only. A tracking link or discount code for one creator and one product."""

    __tablename__ = "creator_product_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False, index=True)
    shortlist_item_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shortlist_items.id"), nullable=True)
    link_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discount_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

