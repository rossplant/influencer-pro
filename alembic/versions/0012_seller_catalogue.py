"""Seller profile fields and curated product columns.

Revision ID: 0012_seller_catalogue
Revises: 0011_source_platform_spark_ads
Create Date: 2026-11-16

products.url is filled from attributes.url. attributes.url is left in place.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_seller_catalogue"
down_revision: Union[str, Sequence[str], None] = "0011_source_platform_spark_ads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shops") as batch:
        batch.add_column(sa.Column("business_name", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("abn", sa.String(length=11), nullable=True))
        batch.add_column(sa.Column("store_url", sa.Text(), nullable=True))
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("why_chosen", sa.Text(), nullable=True))
        batch.add_column(sa.Column("value_to_creators", sa.Text(), nullable=True))
        batch.add_column(sa.Column("image_url", sa.Text(), nullable=True))
    from catalogue import copy_urls_from_attributes

    copy_urls_from_attributes(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_column("image_url")
        batch.drop_column("value_to_creators")
        batch.drop_column("why_chosen")
        batch.drop_column("url")
    with op.batch_alter_table("shops") as batch:
        batch.drop_column("store_url")
        batch.drop_column("abn")
        batch.drop_column("business_name")
