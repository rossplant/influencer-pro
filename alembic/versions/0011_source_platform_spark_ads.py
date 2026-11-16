"""Source platform on orders, per-platform codes, and collab Spark Ads.

Revision ID: 0011_source_platform_spark_ads
Revises: 0010_shopify_order_sync
Create Date: 2026-11-16

No bank or payout fields. spark_code stores ciphertext only.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_source_platform_spark_ads"
down_revision: Union[str, Sequence[str], None] = "0010_shopify_order_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("attributed_orders") as batch:
        batch.add_column(sa.Column("source_platform", sa.String(length=16), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("channel_kind", sa.String(length=16), nullable=True))
        batch.create_check_constraint(
            "ck_attributed_orders_source_platform",
            "source_platform IN ('tiktok', 'instagram', 'youtube', 'facebook', 'other', 'unknown')",
        )
        batch.create_check_constraint(
            "ck_attributed_orders_channel_kind",
            "channel_kind IS NULL OR channel_kind IN ('link', 'code', 'ad')",
        )
    op.create_table(
        "creator_platform_codes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("link_id", sa.String(length=36), sa.ForeignKey("creator_product_links.id"), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("source_platform", sa.String(length=16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("shop_id", "code", name="uq_creator_platform_codes_shop_code"),
        sa.CheckConstraint(
            "source_platform IN ('tiktok', 'instagram', 'youtube', 'facebook', 'other')",
            name="ck_creator_platform_codes_source_platform",
        ),
    )
    op.create_index("ix_creator_platform_codes_shop_id", "creator_platform_codes", ["shop_id"])
    op.create_index("ix_creator_platform_codes_creator_id", "creator_platform_codes", ["creator_id"])
    op.create_index("ix_creator_platform_codes_link_id", "creator_platform_codes", ["link_id"])
    op.create_table(
        "collab_spark_ads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("collab_id", sa.String(length=36), sa.ForeignKey("collab_agreements.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("post_url", sa.Text(), nullable=False),
        sa.Column("spark_code", sa.Text(), nullable=False),
        sa.Column("authorised_until", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("usage_note", sa.Text(), nullable=True),
        sa.Column("ad_destination", sa.Text(), nullable=False),
        sa.Column("consented_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.CheckConstraint("status IN ('active', 'expired', 'revoked')", name="ck_collab_spark_ads_status"),
    )
    op.create_index("ix_collab_spark_ads_collab_id", "collab_spark_ads", ["collab_id"])
    op.create_index("ix_collab_spark_ads_creator_id", "collab_spark_ads", ["creator_id"])


def downgrade() -> None:
    op.drop_index("ix_collab_spark_ads_creator_id", table_name="collab_spark_ads")
    op.drop_index("ix_collab_spark_ads_collab_id", table_name="collab_spark_ads")
    op.drop_table("collab_spark_ads")
    op.drop_index("ix_creator_platform_codes_link_id", table_name="creator_platform_codes")
    op.drop_index("ix_creator_platform_codes_creator_id", table_name="creator_platform_codes")
    op.drop_index("ix_creator_platform_codes_shop_id", table_name="creator_platform_codes")
    op.drop_table("creator_platform_codes")
    with op.batch_alter_table("attributed_orders") as batch:
        batch.drop_constraint("ck_attributed_orders_channel_kind", type_="check")
        batch.drop_constraint("ck_attributed_orders_source_platform", type_="check")
        batch.drop_column("channel_kind")
        batch.drop_column("source_platform")
