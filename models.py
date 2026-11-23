"""SQLAlchemy models for Plantr persistence.

Pilot tables (shops, creators, products, events, shortlists, shortlist_items,
briefs, api_requests) are written by the API. subscriptions,
subscription_changes, monthly_mrr_snapshots, and attributed_orders are created
here and have no endpoints yet.

MRR rules for monthly_mrr_snapshots (and for the mrr value returned with a shop):
- Monthly list price is the MRR (growth 79, scale 229), ex GST.
- Annual list price is divided by 12 (growth 790, scale 2290).
- Free, trial, trialing, and starter contribute 0.

events is append-only: the application inserts rows and does not update or
delete them. api_requests stores no request or response bodies.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.types import TypeDecorator
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
    false as sql_false,
    true as sql_true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """Store UTC and always return a timezone-aware datetime, including on SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


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
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    subscription_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    billing_interval: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    catalogue: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    first_look_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    attribution_first: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    attribution_last: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    signup_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    signup_campaign: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    signup_channel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    # Opt-in to marketing email. Separate from marketing_consent, which is the cookie and ad-platform choice.
    email_marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    primary_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # Trading name from the pilot application. `name` stays the account name entered at join.
    business_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    abn: Mapped[Optional[str]] = mapped_column(String(11), nullable=True)
    store_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    sample_max_per_month: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    sample_post_due_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14, server_default="14")
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
    metrics_as_of: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
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
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="member", server_default="member")
    plan_status: Mapped[str] = mapped_column(String(32), nullable=False, default="invited", server_default="invited")
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    subscription_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    billing_interval: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    age_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    founding_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attribution_first: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    attribution_last: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    signup_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    signup_campaign: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    signup_channel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    # Opt-in to marketing email. Separate from marketing_consent, which is the cookie and ad-platform choice.
    email_marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    # Optional. Creators without an ABN can join; sellers see a no-ABN withholding warning.
    abn: Mapped[Optional[str]] = mapped_column(String(11), nullable=True)
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
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="AUD", server_default="AUD")
    tags: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    claims: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    commission_rate: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    # Public product URL. Kept in step with attributes.url so older readers still work.
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why_chosen: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value_to_creators: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    optimise_for: Mapped[str] = mapped_column(String(16), nullable=False, default="balanced", server_default="balanced")
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


class ShortlistExclusion(Base):
    """A creator removed from a shortlist, with the reasons."""

    __tablename__ = "shortlist_exclusions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shortlist_id: Mapped[str] = mapped_column(ForeignKey("shortlists.id"), nullable=False, index=True)
    creator_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    handle: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reasons: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


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
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="AUD")
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
    - Annual list price / 12 is the MRR (growth 790, scale 2290).
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
        CheckConstraint(
            "source_platform IN ('tiktok', 'instagram', 'youtube', 'facebook', 'other', 'unknown')",
            name="ck_attributed_orders_source_platform",
        ),
        CheckConstraint(
            "channel_kind IS NULL OR channel_kind IN ('link', 'code', 'ad')",
            name="ck_attributed_orders_channel_kind",
        ),
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
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="AUD")
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
    attributed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql_true())
    # Shopify order sync (0010). Amounts are ex shipping and ex tax.
    attribution_method: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    attribution_note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    financial_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql_true())
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    tax_excluded: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    shipping_excluded: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    statement_reported_net: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    external_updated_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    # Where the sale was reported from. Reporting only: it does not change the amount owed.
    source_platform: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    channel_kind: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)


class AdminUser(Base):
    """The single operator account. The password is set in the browser, not in an env var."""

    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reset_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reset_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PortalAccount(Base):
    """A seller login for one shop, or a creator login for one creator."""

    __tablename__ = "portal_accounts"
    __table_args__ = (
        UniqueConstraint("email", "role", name="uq_portal_accounts_email_role"),
        CheckConstraint("role IN ('seller', 'creator')", name="ck_portal_accounts_role"),
        CheckConstraint(
            "(role = 'seller' AND shop_id IS NOT NULL AND creator_id IS NULL) OR "
            "(role = 'creator' AND creator_id IS NOT NULL AND shop_id IS NULL)",
            name="ck_portal_accounts_subject",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shops.id"), nullable=True, index=True)
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class StripeEvent(Base):
    """One Stripe webhook delivery. stripe_event_id makes processing idempotent."""

    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Campaign(Base):
    """A seller campaign. The count is capped by the shop's plan."""

    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class BillingSubscription(Base):
    """The Stripe Billing subscription for a seller or a creator."""

    __tablename__ = "billing_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    shop_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shops.id"), nullable=True, index=True)
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True, index=True)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="inactive")
    interval: Mapped[str] = mapped_column(String(16), nullable=False, default="month")
    lookup_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trial_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    promo_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    invite_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    discount_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PilotCohort(Base):
    __tablename__ = "pilot_cohorts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    seat_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pilot_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    post_coupon_key: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql_true())


class PilotInvite(Base):
    __tablename__ = "pilot_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("pilot_cohorts.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    campaign: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False, default="invite")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PilotApplication(Base):
    __tablename__ = "pilot_applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cohort_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    categories: Mapped[str] = mapped_column(Text, nullable=False, default="")
    contact: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    founding_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attribution: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    age_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    email_marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    business_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    abn: Mapped[Optional[str]] = mapped_column(String(11), nullable=True)
    authority_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PilotMembership(Base):
    __tablename__ = "pilot_memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("pilot_cohorts.id"), nullable=False, index=True)
    application_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pilot_applications.id"), nullable=True)
    invite_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pilot_invites.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    shop_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shops.id"), nullable=True)
    creator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("creators.id"), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="invited")
    founding_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    invited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    onboarded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    active_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PilotVisit(Base):
    __tablename__ = "pilot_visits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    path: Mapped[str] = mapped_column(String(128), nullable=False)
    cohort_slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PilotSurvey(Base):
    __tablename__ = "pilot_surveys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    subject_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    cohort_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    feels_right: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmailMessage(Base):
    """A logged email. Nothing in this table has been delivered to an inbox."""

    __tablename__ = "email_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class TrackedEvent(Base):
    """Idempotency key for a business event. The row in events is the log."""

    __tablename__ = "tracked_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    subject_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    event_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class TrackingRetry(Base):
    """A platform send that failed. The internal event is already stored."""

    __tablename__ = "tracking_retries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
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
    # The seller's own Shopify discount code for this creator (for example JESS10). Optional.
    store_discount_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)



class TermsAcceptance(Base):
    """What a person ticked, the exact text shown, and when. Append-only."""

    __tablename__ = "terms_acceptances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    context: Mapped[str] = mapped_column(String(64), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(64), nullable=False)
    documents: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    text_shown: Mapped[str] = mapped_column(Text, nullable=False)
    price_shown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confirmations: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ReminderSend(Base):
    """Idempotent log for scheduled billing reminders. One row per reminder_key."""

    __tablename__ = "reminder_sends"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reminder_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ShopifyConnection(Base):
    """How one shop sends orders to Plantr. Secrets are Fernet-encrypted. No bank or customer data."""

    __tablename__ = "shopify_connections"
    __table_args__ = (
        CheckConstraint("mode IN ('app', 'store_webhooks')", name="ck_shopify_connections_mode"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, unique=True)
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    scopes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    access_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    refresh_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token_expires_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    webhook_secret_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_path_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    oauth_state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    oauth_state_expires_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    installed_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    uninstalled_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    last_webhook_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    last_backfill_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class ShopifyWebhookReceipt(Base):
    """One row per delivered webhook id. Bodies are not stored."""

    __tablename__ = "shopify_webhook_receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    delivery_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    shop_domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class CollabAgreement(Base):
    """Offer terms both parties accepted: exact text, version, hash, and who accepted when."""

    __tablename__ = "collab_agreements"
    __table_args__ = (
        CheckConstraint(
            "paid_via IN ('plantr_tracked', 'tiktok_shop', 'shopify_collabs')",
            name="ck_collab_agreements_paid_via",
        ),
        CheckConstraint("status IN ('offered', 'active', 'withdrawn', 'ended')", name="ck_collab_agreements_status"),
        Index("ix_collab_agreements_pair", "shop_id", "creator_id", "product_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False, index=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True)
    shortlist_item_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shortlist_items.id"), nullable=True)
    commission_rate: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    payment_terms_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    paid_via: Mapped[str] = mapped_column(String(32), nullable=False, default="plantr_tracked", server_default="plantr_tracked")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="offered", server_default="offered")
    terms_version: Mapped[str] = mapped_column(String(64), nullable=False)
    terms_text: Mapped[str] = mapped_column(Text, nullable=False)
    terms_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    seller_accepted_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    seller_accepted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    seller_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    creator_accepted_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    creator_accepted_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    creator_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class CommissionStatement(Base):
    """Estimated commission owed by one shop to one creator for one month. Plantr never moves this money."""

    __tablename__ = "commission_statements"
    __table_args__ = (
        UniqueConstraint("shop_id", "creator_id", "period", "currency", name="uq_commission_statements_period"),
        CheckConstraint(
            "status IN ('open', 'marked_paid', 'confirmed', 'disputed', 'resolved')",
            name="ck_commission_statements_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    net_sales: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    amount_owed: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    payment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    overdue_on: Mapped[date] = mapped_column(Date, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)
    marked_paid_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    marked_paid_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    paid_reference: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    disputed_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    dispute_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dispute_amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resolution_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    overdue_notified_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)


class CommissionStatementLine(Base):
    """The order rows behind a statement. Adjustments carry later refunds forward."""

    __tablename__ = "commission_statement_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    statement_id: Mapped[str] = mapped_column(ForeignKey("commission_statements.id"), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("attributed_orders.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    net_sales: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    commission_rate: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    commission: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    collab_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class CommissionAuditLog(Base):
    """Append-only. Corrections are new rows."""

    __tablename__ = "commission_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    statement_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class CollabBlockLift(Base):
    """An admin lifted a seller's block for the statements listed. New problems block again."""

    __tablename__ = "collab_block_lifts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    statement_ids: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    lifted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class SellerPaymentResponse(Base):
    """The seller's short public response shown next to their pays-on-time record."""

    __tablename__ = "seller_payment_responses"

    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), primary_key=True)
    text: Mapped[str] = mapped_column(String(280), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class AbnWarningAck(Base):
    """A seller acknowledged the no-ABN withholding warning for a creator. Append-only."""

    __tablename__ = "abn_warning_acks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False, index=True)
    context: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    warning_version: Mapped[str] = mapped_column(String(32), nullable=False)
    warning_text: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged_by: Mapped[str] = mapped_column(String(255), nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    acknowledged_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class CommissionCorrection(Base):
    """A seller's request to correct a late or missed entry in the pays-on-time record.

    The creator agrees or disagrees within the window; silence counts as agreement.
    A disagreement goes to a Plantr admin, who corrects or keeps the entry with a reason.
    """

    __tablename__ = "commission_corrections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_creator', 'agreed', 'deemed_agreed', 'disagreed', 'admin_corrected', 'admin_kept')",
            name="ck_commission_corrections_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    statement_id: Mapped[str] = mapped_column(ForeignKey("commission_statements.id"), nullable=False, index=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False, index=True)
    entry_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_creator", server_default="pending_creator")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    proposed_paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    original_status: Mapped[str] = mapped_column(String(16), nullable=False)
    original_paid_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)
    respond_by: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    creator_response_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    creator_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CreatorPlatformCode(Base):
    """An optional extra code for one creator, tagged to a source platform (for example MIA-TT).

    The creator's Plantr code and untagged store code stay as they are. A sale that
    matches one of these codes is still one credit; the tag is for reporting.
    """

    __tablename__ = "creator_platform_codes"
    __table_args__ = (
        UniqueConstraint("shop_id", "code", name="uq_creator_platform_codes_shop_code"),
        CheckConstraint(
            "source_platform IN ('tiktok', 'instagram', 'youtube', 'facebook', 'other')",
            name="ck_creator_platform_codes_source_platform",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False, index=True)
    link_id: Mapped[str] = mapped_column(ForeignKey("creator_product_links.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_platform: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class CollabSparkAd(Base):
    """A TikTok Spark code the creator chose to share for one collab.

    spark_code is a Fernet ciphertext (SHOPIFY_TOKEN_KEY). It is never listed or logged.
    ad_destination is the seller's own product URL, not a Plantr redirect.
    """

    __tablename__ = "collab_spark_ads"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'expired', 'revoked')", name="ck_collab_spark_ads_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collab_id: Mapped[str] = mapped_column(ForeignKey("collab_agreements.id"), nullable=False, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False, index=True)
    post_url: Mapped[str] = mapped_column(Text, nullable=False)
    spark_code: Mapped[str] = mapped_column(Text, nullable=False)
    authorised_until: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    usage_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ad_destination: Mapped[str] = mapped_column(Text, nullable=False)
    consented_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)


class SampleRequest(Base):
    """A Creatr's request for a product sample. The address is hidden until approval."""

    __tablename__ = "sample_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'approved', 'declined', 'shipped', 'delivered', 'posted', 'overdue')",
            name="ck_sample_requests_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), nullable=False, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested", server_default="requested")
    decline_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    address_line: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    suburb: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    postcode: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    tracking: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    post_due_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    post_links: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    disclosure_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    reminder_7_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    reminder_2_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    address_redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class UatFeedback(Base):
    """A pass or fail note from the staging UAT checklist."""

    __tablename__ = "uat_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class UatSet(Base):
    """The staging UAT supplier and Creatr. One row, kept in the database."""

    __tablename__ = "uat_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    creator_id: Mapped[str] = mapped_column(String(36), nullable=False)
    audience: Mapped[int] = mapped_column(Integer, nullable=False, default=45000, server_default="45000")
    invite_sent_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime(), nullable=True)
    invite_provider_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_press_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, default=utcnow)


class UatInvite(Base):
    """A short UAT invite path. It expires and is not a long signed token."""

    __tablename__ = "uat_invites"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    set_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
