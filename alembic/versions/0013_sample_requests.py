"""Creatr sample requests.

Revision ID: 0013_sample_requests
Revises: 0012_seller_catalogue
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_sample_requests"
down_revision: Union[str, Sequence[str], None] = "0012_seller_catalogue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shops") as batch:
        batch.add_column(sa.Column("sample_max_per_month", sa.Integer(), nullable=False, server_default="5"))
        batch.add_column(sa.Column("sample_post_due_days", sa.Integer(), nullable=False, server_default="14"))
    op.create_table(
        "sample_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("shop_id", sa.String(length=36), nullable=False),
        sa.Column("creator_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="requested"),
        sa.Column("decline_reason", sa.Text(), nullable=True),
        sa.Column("address_line", sa.Text(), nullable=True),
        sa.Column("suburb", sa.String(length=120), nullable=True),
        sa.Column("region", sa.String(length=80), nullable=True),
        sa.Column("postcode", sa.String(length=12), nullable=True),
        sa.Column("tracking", sa.Text(), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("post_due_on", sa.Date(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("post_links", sa.Text(), nullable=True),
        sa.Column("disclosure_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reminder_7_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reminder_2_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("address_redacted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"]),
        sa.ForeignKeyConstraint(["creator_id"], ["creators.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'declined', 'shipped', 'delivered', 'posted', 'overdue')",
            name="ck_sample_requests_status",
        ),
    )
    op.create_index("ix_sample_requests_shop_id", "sample_requests", ["shop_id"])
    op.create_index("ix_sample_requests_creator_id", "sample_requests", ["creator_id"])
    op.create_index("ix_sample_requests_product_id", "sample_requests", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_sample_requests_product_id", table_name="sample_requests")
    op.drop_index("ix_sample_requests_creator_id", table_name="sample_requests")
    op.drop_index("ix_sample_requests_shop_id", table_name="sample_requests")
    op.drop_table("sample_requests")
    with op.batch_alter_table("shops") as batch:
        batch.drop_column("sample_post_due_days")
        batch.drop_column("sample_max_per_month")
