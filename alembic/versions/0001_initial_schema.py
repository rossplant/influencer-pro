"""Initial Plantr schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-15

MRR rules (monthly_mrr_snapshots, documented for later writers):
- A monthly price is the MRR.
- An annual price is divided by 12.
- Free and trial contribute 0.
- Starter, the entry plan_tier, is 0.

events is append-only. api_requests has no body columns; the app purges rows
older than 90 days. JSON columns are JSONB on Postgres and JSON on SQLite.
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
        "shops",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("shopify_domain", sa.String(length=255), nullable=True),
        sa.Column("plan_tier", sa.String(length=32), nullable=False, server_default=sa.text("'starter'")),
        sa.Column("plan_status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("is_pilot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("products_enriched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matches_run", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("briefs_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_enrich_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_match_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_brief_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "creators",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("handle", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("interests", json_type, nullable=False),
        sa.Column("expertise_level", json_type, nullable=False),
        sa.Column("preferred_promotion_styles", json_type, nullable=False),
        sa.Column("audience_demographics", json_type, nullable=True),
        sa.Column("content_formats", json_type, nullable=False),
        sa.Column("average_engagement_rate", sa.Float(), nullable=True),
        sa.Column("past_categories", json_type, nullable=False),
        sa.Column("restrictions", json_type, nullable=False),
        sa.Column("availability", sa.String(length=32), nullable=False, server_default=sa.text("'open'")),
        sa.Column("commission_preference", sa.String(length=64), nullable=True),
        sa.Column("platforms", json_type, nullable=False),
        sa.Column("follower_band", sa.String(length=64), nullable=True),
        sa.Column("profile", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_creators_handle", "creators", ["handle"])
    op.create_table(
        "products",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description_short", sa.Text(), nullable=True),
        sa.Column("description_long", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("images", json_type, nullable=False),
        sa.Column("pet_safe", sa.Boolean(), nullable=True),
        sa.Column("light", sa.String(length=16), nullable=True),
        sa.Column("care_level", sa.String(length=64), nullable=True),
        sa.Column("size", sa.String(length=64), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("claims", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("light IS NULL OR light IN ('low', 'medium', 'bright')", name="ck_products_light"),
    )
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
        sa.Column("properties", json_type, nullable=False),
        sa.CheckConstraint("actor IN ('system', 'operator', 'api')", name="ck_events_actor"),
    )
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_event_name", "events", ["event_name"])
    op.create_index("ix_events_shop_id", "events", ["shop_id"])
    op.create_index("ix_events_shop_name_time", "events", ["shop_id", "event_name", "occurred_at"])
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
    )
    op.create_index("ix_shortlist_items_shortlist_id", "shortlist_items", ["shortlist_id"])
    op.create_index("ix_shortlist_items_creator_id", "shortlist_items", ["creator_id"])
    op.create_table(
        "briefs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("disclosure_line", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("source IN ('grok', 'fallback')", name="ck_briefs_source"),
    )
    op.create_index("ix_briefs_product_id", "briefs", ["product_id"])
    op.create_table(
        "api_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("route", sa.String(length=512), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.String(length=64), nullable=True),
        sa.Column("grok_tokens_prompt", sa.Integer(), nullable=True),
        sa.Column("grok_tokens_completion", sa.Integer(), nullable=True),
        sa.Column("grok_fallback", sa.Boolean(), nullable=True),
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
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_subscription_changes_shop_id", "subscription_changes", ["shop_id"])
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
    op.create_table(
        "attributed_orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("external_order_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_attributed_orders_shop_id", "attributed_orders", ["shop_id"])


def downgrade() -> None:
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
