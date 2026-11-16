"""Manual sales reporting for sellers who are not on Shopify.

Revision ID: 0013_manual_sales_reporting
Revises: 0012_seller_catalogue
Create Date: 2026-11-16

Shops that already have a Shopify domain are marked shopify_sync.
Shops without one stay unset so the profile asks on the next visit.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_manual_sales_reporting"
down_revision: Union[str, Sequence[str], None] = "0012_seller_catalogue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shops") as batch:
        batch.add_column(sa.Column("sells_on_shopify", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("sales_reporting", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("manual_approved", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("manual_approved_by", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("manual_approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_shops_sales_reporting",
            "sales_reporting IS NULL OR sales_reporting IN ('manual', 'shopify_sync')",
        )
    shops = sa.table(
        "shops",
        sa.column("shopify_domain", sa.String),
        sa.column("sells_on_shopify", sa.Boolean),
        sa.column("sales_reporting", sa.String),
    )
    op.execute(
        shops.update()
        .where(shops.c.shopify_domain.is_not(None))
        .where(shops.c.shopify_domain != "")
        .values(sells_on_shopify=True, sales_reporting="shopify_sync")
    )
    op.create_table(
        "sales_reports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="received"),
        sa.Column("csv_text", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("net_total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.UniqueConstraint("shop_id", "period", name="uq_sales_reports_shop_period"),
        sa.CheckConstraint("kind IN ('csv', 'no_sales')", name="ck_sales_reports_kind"),
        sa.CheckConstraint("status IN ('received', 'accepted', 'rejected')", name="ck_sales_reports_status"),
    )
    op.create_index("ix_sales_reports_shop_id", "sales_reports", ["shop_id"])
    op.create_index("ix_sales_reports_status", "sales_reports", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sales_reports_status", table_name="sales_reports")
    op.drop_index("ix_sales_reports_shop_id", table_name="sales_reports")
    op.drop_table("sales_reports")
    with op.batch_alter_table("shops") as batch:
        batch.drop_constraint("ck_shops_sales_reporting", type_="check")
        batch.drop_column("manual_approved_at")
        batch.drop_column("manual_approved_by")
        batch.drop_column("manual_approved")
        batch.drop_column("sales_reporting")
        batch.drop_column("sells_on_shopify")
