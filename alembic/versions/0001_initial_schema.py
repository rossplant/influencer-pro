"""Initial Plantr schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-15

MRR rules (monthly_mrr_snapshots, documented for later writers):
- Monthly list price is the MRR (growth 49, scale 149).
- Annual list price / 12 is the MRR (growth 490, scale 1490).
- Free, trial, trialing, and starter contribute 0.

events is append-only. Shop events stamp catalogue and period counters as
columns. api_requests has no body columns; the app purges rows older than
90 days. JSON columns are JSONB on Postgres and JSON on SQLite.

If this revision was already applied with the pre-analytics columns
(attributed_orders.amount, events without products_count), startup drops
those tables and recreates them. Production is not on 0.10 and has no
tables, so that path does not run there. Do not use it once production
has data; ship a new revision instead.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    json_type = _json()
    op.create_table(
        "categories",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("parent_slug", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_categories_parent_slug", "categories", ["parent_slug"])
    from taxonomy import TOP_LEVEL_CATEGORIES

    categories = sa.table(
        "categories",
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("parent_slug", sa.String),
    )
    op.bulk_insert(
        categories,
        [{"slug": slug, "name": name, "parent_slug": None} for slug, name in TOP_LEVEL_CATEGORIES],
    )
    op.create_table(
        "shops",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("shopify_domain", sa.String(length=255), nullable=True),
        sa.Column("plan_tier", sa.String(length=32), nullable=False, server_default=sa.text("'starter'")),
        sa.Column("plan_status", sa.String(length=32), nullable=False, server_default=sa.text("'free'")),
        sa.Column("is_pilot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_underserved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("underserved_reason", sa.Text(), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("market", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("primary_category", sa.String(length=64), nullable=True),
        sa.Column("products_enriched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matches_run", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("briefs_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_enrich_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_match_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_brief_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shopify_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_attributed_sale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_match_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_campaign_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("shopify_domain", name="uq_shops_shopify_domain"),
    )
    op.create_index("ix_shops_is_pilot", "shops", ["is_pilot"])
    op.create_index("ix_shops_created_at", "shops", ["created_at"])
    op.create_index("ix_shops_country", "shops", ["country"])
    op.create_index("ix_shops_market", "shops", ["market"])
    op.create_index("ix_shops_currency", "shops", ["currency"])
    op.create_index("ix_shops_primary_category", "shops", ["primary_category"])
    op.create_table(
        "creators",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("handle", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("handle", name="uq_creators_handle"),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("interests", json_type, nullable=False),
        sa.Column("expertise_level", json_type, nullable=False),
        sa.Column("preferred_promotion_styles", json_type, nullable=False),
        sa.Column("audience_demographics", json_type, nullable=True),
        sa.Column("content_formats", json_type, nullable=False),
        sa.Column("past_categories", json_type, nullable=False),
        sa.Column("restrictions", json_type, nullable=False),
        sa.Column("excluded_categories", json_type, nullable=False),
        sa.Column("excluded_claims", json_type, nullable=False),
        sa.Column("active_categories", json_type, nullable=False),
        sa.Column("desired_categories", json_type, nullable=False),
        sa.Column("avg_views", sa.Integer(), nullable=True),
        sa.Column("engagement_rate", sa.Float(), nullable=True),
        sa.Column("metrics_source", sa.String(length=32), nullable=False, server_default=sa.text("'self_reported'")),
        sa.Column("metrics_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("est_conversion_rate", sa.Float(), nullable=True),
        sa.Column("avg_order_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("typical_price_band", json_type, nullable=True),
        sa.Column("availability", sa.String(length=32), nullable=False, server_default=sa.text("'open'")),
        sa.Column("commission_preference", sa.String(length=64), nullable=True),
        sa.Column("platforms", json_type, nullable=False),
        sa.Column("follower_band", sa.String(length=64), nullable=True),
        sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_match_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_post_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "metrics_source IN ('self_reported', 'platform_api', 'manual_verified')",
            name="ck_creators_metrics_source",
        ),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description_short", sa.Text(), nullable=True),
        sa.Column("description_long", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("subcategory", sa.String(length=255), nullable=True),
        sa.Column("images", json_type, nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("tags", json_type, nullable=False),
        sa.Column("attributes", json_type, nullable=False),
        sa.Column("claims", json_type, nullable=False),
        sa.Column("commission_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('shopify', 'csv', 'manual', 'api')", name="ck_products_source"),
    )
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_shop_id", "products", ["shop_id"])
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=True),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("products_count", sa.Integer(), nullable=True),
        sa.Column("product_limit", sa.Integer(), nullable=True),
        sa.Column("matches_count_period", sa.Integer(), nullable=True),
        sa.Column("match_limit", sa.Integer(), nullable=True),
        sa.Column("enrichments_count_period", sa.Integer(), nullable=True),
        sa.Column("briefs_count_period", sa.Integer(), nullable=True),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("limit_hit", sa.Boolean(), nullable=True),
        sa.Column("properties", json_type, nullable=False),
        sa.CheckConstraint("actor IN ('system', 'operator', 'api')", name="ck_events_actor"),
    )
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_event_name", "events", ["event_name"])
    op.create_index("ix_events_shop_id", "events", ["shop_id"])
    op.create_index("ix_events_shop_name_time", "events", ["shop_id", "event_name", "occurred_at"])
    op.create_index("ix_events_entity_time", "events", ["entity_type", "entity_id", "occurred_at"])
    op.create_table(
        "shortlists",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=True),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("match_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_shortlists_shop_id", "shortlists", ["shop_id"])
    op.create_index("ix_shortlists_product_id", "shortlists", ["product_id"])
    op.create_table(
        "shortlist_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shortlist_id", sa.String(length=36), sa.ForeignKey("shortlists.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", json_type, nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decline_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("shortlist_id", "creator_id", name="uq_shortlist_items_shortlist_creator"),
    )
    op.create_index("ix_shortlist_items_shortlist_id", "shortlist_items", ["shortlist_id"])
    op.create_index("ix_shortlist_items_creator_id", "shortlist_items", ["creator_id"])
    op.create_table(
        "briefs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("shortlist_item_id", sa.String(length=36), sa.ForeignKey("shortlist_items.id"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("disclosure_line", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("source IN ('grok', 'fallback')", name="ck_briefs_source"),
    )
    op.create_index("ix_briefs_product_id", "briefs", ["product_id"])
    op.create_index("ix_briefs_shop_id", "briefs", ["shop_id"])
    op.create_table(
        "api_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("route", sa.String(length=512), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("grok_tokens_prompt", sa.Integer(), nullable=True),
        sa.Column("grok_tokens_completion", sa.Integer(), nullable=True),
        sa.Column("grok_fallback", sa.Boolean(), nullable=True),
        sa.Column("grok_latency_ms", sa.Integer(), nullable=True),
        sa.Column("grok_model", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_api_requests_ts", "api_requests", ["ts"])
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("plan_tier", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("interval", sa.String(length=16), nullable=False, server_default=sa.text("'month'")),
        sa.Column("price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("mrr", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_subscriptions_shop_id", "subscriptions", ["shop_id"])
    op.create_table(
        "subscription_changes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("subscription_id", sa.String(length=36), sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("paid_to_free", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("upgrade_trigger_snapshot", json_type, nullable=True),
        sa.Column("from_tier", sa.String(length=32), nullable=True),
        sa.Column("to_tier", sa.String(length=32), nullable=True),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("from_mrr", sa.Numeric(12, 2), nullable=True),
        sa.Column("to_mrr", sa.Numeric(12, 2), nullable=True),
        sa.Column("mrr_delta", sa.Numeric(12, 2), nullable=True),
        sa.Column("trigger_limit", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "change_type IN ('new', 'expansion', 'contraction', 'churn', 'reactivation', 'trial_started', 'trial_converted')",
            name="ck_subscription_changes_change_type",
        ),
    )
    op.create_index("ix_subscription_changes_shop_id", "subscription_changes", ["shop_id"])
    op.create_index("ix_subscription_changes_shop_created", "subscription_changes", ["shop_id", "created_at"])
    op.create_table(
        "monthly_mrr_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("plan_tier", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("interval", sa.String(length=16), nullable=False, server_default=sa.text("'month'")),
        sa.Column("price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("mrr", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("shop_id", "month", name="uq_mrr_shop_month"),
    )
    op.create_index("ix_monthly_mrr_snapshots_shop_id", "monthly_mrr_snapshots", ["shop_id"])
    op.create_index("ix_monthly_mrr_snapshots_month", "monthly_mrr_snapshots", ["month"])
    op.create_table(
        "attributed_orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("market", sa.String(length=64), nullable=True),
        sa.Column("external_order_id", sa.String(length=128), nullable=False),
        sa.Column("external_creator_id", sa.String(length=128), nullable=True),
        sa.Column("gmv_gross", sa.Numeric(12, 2), nullable=False),
        sa.Column("refunded_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("cancelled_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("net_gmv", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("shortlist_item_id", sa.String(length=36), sa.ForeignKey("shortlist_items.id"), nullable=True),
        sa.Column("brief_id", sa.String(length=36), sa.ForeignKey("briefs.id"), nullable=True),
        sa.Column("attribution_date", sa.Date(), nullable=False),
        sa.Column("click_window_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("click_window_source", sa.String(length=64), nullable=True),
        sa.Column("settlement_status", sa.String(length=32), nullable=True),
        sa.Column("estimated_commission", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_commission", sa.Numeric(12, 2), nullable=True),
        sa.Column("fee_revenue", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("fee_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("ingest_source", sa.String(length=64), nullable=True),
        sa.Column("raw_payload", json_type, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("platform", "external_order_id", name="uq_orders_platform_external"),
    )
    op.create_index("ix_attributed_orders_shop_id", "attributed_orders", ["shop_id"])
    op.create_index("ix_orders_shop_attribution_date", "attributed_orders", ["shop_id", "attribution_date"])
    op.create_index("ix_orders_country", "attributed_orders", ["country"])
    op.create_index("ix_orders_market", "attributed_orders", ["market"])
    op.create_table(
        "creator_product_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("shortlist_item_id", sa.String(length=36), sa.ForeignKey("shortlist_items.id"), nullable=True),
        sa.Column("link_url", sa.Text(), nullable=True),
        sa.Column("discount_code", sa.String(length=64), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_creator_product_links_shop_id", "creator_product_links", ["shop_id"])
    op.create_index(
        "uq_links_product_creator_code",
        "creator_product_links",
        ["product_id", "creator_id", "discount_code"],
        unique=True,
        sqlite_where=sa.text("discount_code IS NOT NULL"),
        postgresql_where=sa.text("discount_code IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("creator_product_links")
    op.drop_table("attributed_orders")
    op.drop_table("monthly_mrr_snapshots")
    op.drop_table("subscription_changes")
    op.drop_table("subscriptions")
    op.drop_table("api_requests")
    op.drop_table("briefs")
    op.drop_table("shortlist_items")
    op.drop_table("shortlists")
    op.drop_table("events")
    op.drop_table("products")
    op.drop_table("creators")
    op.drop_table("shops")
    op.drop_table("categories")
