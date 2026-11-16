"""Shopify order sync, collab agreements, and light commission tracking.

Revision ID: 0010_shopify_order_sync
Revises: 0009_reminders_and_acceptance
Create Date: 2026-11-16

No bank or payout fields are added anywhere.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_shopify_order_sync"
down_revision: Union[str, Sequence[str], None] = "0009_reminders_and_acceptance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("attributed_orders", sa.Column("attribution_method", sa.String(length=16), nullable=True))
    op.add_column("attributed_orders", sa.Column("attribution_note", sa.String(length=255), nullable=True))
    op.add_column("attributed_orders", sa.Column("financial_status", sa.String(length=32), nullable=True))
    op.add_column("attributed_orders", sa.Column("settled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("attributed_orders", sa.Column("cancelled_at", TS, nullable=True))
    op.add_column("attributed_orders", sa.Column("tax_excluded", sa.Numeric(12, 2), nullable=True))
    op.add_column("attributed_orders", sa.Column("shipping_excluded", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "attributed_orders",
        sa.Column("statement_reported_net", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.add_column("attributed_orders", sa.Column("external_updated_at", TS, nullable=True))
    op.add_column("creators", sa.Column("abn", sa.String(length=11), nullable=True))
    op.add_column("creator_product_links", sa.Column("store_discount_code", sa.String(length=64), nullable=True))
    op.create_index("ix_creator_product_links_store_discount_code", "creator_product_links", ["store_discount_code"])

    op.create_table(
        "shopify_connections",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False, unique=True),
        sa.Column("shop_domain", sa.String(length=255), nullable=False, unique=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("scopes", sa.String(length=255), nullable=True),
        sa.Column("access_token_enc", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", TS, nullable=True),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("refresh_token_expires_at", TS, nullable=True),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
        sa.Column("webhook_path_token", sa.String(length=64), nullable=True, unique=True),
        sa.Column("oauth_state", sa.String(length=64), nullable=True),
        sa.Column("oauth_state_expires_at", TS, nullable=True),
        sa.Column("installed_at", TS, nullable=True),
        sa.Column("uninstalled_at", TS, nullable=True),
        sa.Column("last_webhook_at", TS, nullable=True),
        sa.Column("last_backfill_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("mode IN ('app', 'store_webhooks')", name="ck_shopify_connections_mode"),
    )
    op.create_index("ix_shopify_connections_oauth_state", "shopify_connections", ["oauth_state"])
    op.create_table(
        "shopify_webhook_receipts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("delivery_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("shop_domain", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("received_at", TS, nullable=False),
    )
    op.create_index("ix_shopify_webhook_receipts_shop_domain", "shopify_webhook_receipts", ["shop_domain"])
    op.create_table(
        "collab_agreements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("shortlist_item_id", sa.String(length=36), sa.ForeignKey("shortlist_items.id"), nullable=True),
        sa.Column("commission_rate", sa.Numeric(6, 2), nullable=False),
        sa.Column("payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("paid_via", sa.String(length=32), nullable=False, server_default="plantr_tracked"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="offered"),
        sa.Column("terms_version", sa.String(length=64), nullable=False),
        sa.Column("terms_text", sa.Text(), nullable=False),
        sa.Column("terms_sha256", sa.String(length=64), nullable=False),
        sa.Column("seller_accepted_at", TS, nullable=False),
        sa.Column("seller_accepted_by", sa.String(length=255), nullable=False),
        sa.Column("seller_ip", sa.String(length=64), nullable=True),
        sa.Column("creator_accepted_at", TS, nullable=True),
        sa.Column("creator_accepted_by", sa.String(length=255), nullable=True),
        sa.Column("creator_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint(
            "paid_via IN ('plantr_tracked', 'tiktok_shop', 'shopify_collabs')", name="ck_collab_agreements_paid_via"
        ),
        sa.CheckConstraint("status IN ('offered', 'active', 'withdrawn', 'ended')", name="ck_collab_agreements_status"),
    )
    op.create_index("ix_collab_agreements_shop_id", "collab_agreements", ["shop_id"])
    op.create_index("ix_collab_agreements_creator_id", "collab_agreements", ["creator_id"])
    op.create_index("ix_collab_agreements_pair", "collab_agreements", ["shop_id", "creator_id", "product_id"])
    op.create_table(
        "commission_statements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("net_sales", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount_owed", sa.Numeric(12, 2), nullable=False),
        sa.Column("orders_count", sa.Integer(), nullable=False),
        sa.Column("rate_missing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("overdue_on", sa.Date(), nullable=False),
        sa.Column("issued_at", TS, nullable=False),
        sa.Column("marked_paid_on", sa.Date(), nullable=True),
        sa.Column("marked_paid_at", TS, nullable=True),
        sa.Column("paid_reference", sa.String(length=120), nullable=True),
        sa.Column("confirmed_at", TS, nullable=True),
        sa.Column("disputed_at", TS, nullable=True),
        sa.Column("dispute_reason", sa.Text(), nullable=True),
        sa.Column("dispute_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("resolved_at", TS, nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("overdue_notified_at", TS, nullable=True),
        sa.UniqueConstraint("shop_id", "creator_id", "period", "currency", name="uq_commission_statements_period"),
        sa.CheckConstraint(
            "status IN ('open', 'marked_paid', 'confirmed', 'disputed', 'resolved')",
            name="ck_commission_statements_status",
        ),
    )
    op.create_index("ix_commission_statements_shop_id", "commission_statements", ["shop_id"])
    op.create_index("ix_commission_statements_creator_id", "commission_statements", ["creator_id"])
    op.create_table(
        "commission_statement_lines",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("statement_id", sa.String(length=36), sa.ForeignKey("commission_statements.id"), nullable=False),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("attributed_orders.id"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("net_sales", sa.Numeric(12, 2), nullable=False),
        sa.Column("commission_rate", sa.Numeric(6, 2), nullable=True),
        sa.Column("commission", sa.Numeric(12, 2), nullable=False),
        sa.Column("collab_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_commission_statement_lines_statement_id", "commission_statement_lines", ["statement_id"])
    op.create_index("ix_commission_statement_lines_order_id", "commission_statement_lines", ["order_id"])
    op.create_table(
        "commission_audit_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), nullable=True),
        sa.Column("statement_id", sa.String(length=36), nullable=True),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_commission_audit_log_shop_id", "commission_audit_log", ["shop_id"])
    op.create_index("ix_commission_audit_log_statement_id", "commission_audit_log", ["statement_id"])
    op.create_table(
        "collab_block_lifts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("statement_ids", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("lifted_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_collab_block_lifts_shop_id", "collab_block_lifts", ["shop_id"])
    op.create_table(
        "seller_payment_responses",
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), primary_key=True),
        sa.Column("text", sa.String(length=280), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "abn_warning_acks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("context", sa.String(length=32), nullable=False),
        sa.Column("reference_id", sa.String(length=36), nullable=True),
        sa.Column("warning_version", sa.String(length=32), nullable=False),
        sa.Column("warning_text", sa.Text(), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", TS, nullable=False),
    )
    op.create_index("ix_abn_warning_acks_shop_id", "abn_warning_acks", ["shop_id"])
    op.create_index("ix_abn_warning_acks_creator_id", "abn_warning_acks", ["creator_id"])
    op.create_table(
        "commission_corrections",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("statement_id", sa.String(length=36), sa.ForeignKey("commission_statements.id"), nullable=False),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("entry_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_creator"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("evidence_url", sa.String(length=500), nullable=True),
        sa.Column("proposed_paid_on", sa.Date(), nullable=False),
        sa.Column("original_status", sa.String(length=16), nullable=False),
        sa.Column("original_paid_on", sa.Date(), nullable=True),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("requested_at", TS, nullable=False),
        sa.Column("respond_by", TS, nullable=False),
        sa.Column("creator_response_at", TS, nullable=True),
        sa.Column("creator_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_creator', 'agreed', 'deemed_agreed', 'disagreed', 'admin_corrected', 'admin_kept')",
            name="ck_commission_corrections_status",
        ),
    )
    op.create_index("ix_commission_corrections_statement_id", "commission_corrections", ["statement_id"])
    op.create_index("ix_commission_corrections_shop_id", "commission_corrections", ["shop_id"])
    op.create_index("ix_commission_corrections_creator_id", "commission_corrections", ["creator_id"])


def downgrade() -> None:
    op.drop_index("ix_commission_corrections_creator_id", table_name="commission_corrections")
    op.drop_index("ix_commission_corrections_shop_id", table_name="commission_corrections")
    op.drop_index("ix_commission_corrections_statement_id", table_name="commission_corrections")
    op.drop_table("commission_corrections")
    op.drop_index("ix_abn_warning_acks_creator_id", table_name="abn_warning_acks")
    op.drop_index("ix_abn_warning_acks_shop_id", table_name="abn_warning_acks")
    op.drop_table("abn_warning_acks")
    op.drop_table("seller_payment_responses")
    op.drop_index("ix_collab_block_lifts_shop_id", table_name="collab_block_lifts")
    op.drop_table("collab_block_lifts")
    op.drop_index("ix_commission_audit_log_statement_id", table_name="commission_audit_log")
    op.drop_index("ix_commission_audit_log_shop_id", table_name="commission_audit_log")
    op.drop_table("commission_audit_log")
    op.drop_index("ix_commission_statement_lines_order_id", table_name="commission_statement_lines")
    op.drop_index("ix_commission_statement_lines_statement_id", table_name="commission_statement_lines")
    op.drop_table("commission_statement_lines")
    op.drop_index("ix_commission_statements_creator_id", table_name="commission_statements")
    op.drop_index("ix_commission_statements_shop_id", table_name="commission_statements")
    op.drop_table("commission_statements")
    op.drop_index("ix_collab_agreements_pair", table_name="collab_agreements")
    op.drop_index("ix_collab_agreements_creator_id", table_name="collab_agreements")
    op.drop_index("ix_collab_agreements_shop_id", table_name="collab_agreements")
    op.drop_table("collab_agreements")
    op.drop_index("ix_shopify_webhook_receipts_shop_domain", table_name="shopify_webhook_receipts")
    op.drop_table("shopify_webhook_receipts")
    op.drop_index("ix_shopify_connections_oauth_state", table_name="shopify_connections")
    op.drop_table("shopify_connections")
    op.drop_index("ix_creator_product_links_store_discount_code", table_name="creator_product_links")
    op.drop_column("creator_product_links", "store_discount_code")
    op.drop_column("creators", "abn")
    for column in (
        "external_updated_at",
        "statement_reported_net",
        "shipping_excluded",
        "tax_excluded",
        "cancelled_at",
        "settled",
        "financial_status",
        "attribution_note",
        "attribution_method",
    ):
        op.drop_column("attributed_orders", column)
