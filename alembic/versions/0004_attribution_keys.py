"""One tracking code per creator and product, and one imported order per shop.

Revision ID: 0004_attribution_keys
Revises: 0003_admin_users
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_attribution_keys"
down_revision: Union[str, Sequence[str], None] = "0003_admin_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_links_product_creator",
        "creator_product_links",
        ["product_id", "creator_id"],
        unique=True,
    )
    op.create_index(
        "uq_links_discount_code",
        "creator_product_links",
        ["discount_code"],
        unique=True,
    )
    with op.batch_alter_table("attributed_orders") as batch:
        batch.drop_constraint("uq_orders_platform_external", type_="unique")
        batch.create_unique_constraint("uq_orders_shop_external", ["shop_id", "external_order_id"])


def downgrade() -> None:
    with op.batch_alter_table("attributed_orders") as batch:
        batch.drop_constraint("uq_orders_shop_external", type_="unique")
        batch.create_unique_constraint("uq_orders_platform_external", ["platform", "external_order_id"])
    op.drop_index("uq_links_discount_code", table_name="creator_product_links")
    op.drop_index("uq_links_product_creator", table_name="creator_product_links")
