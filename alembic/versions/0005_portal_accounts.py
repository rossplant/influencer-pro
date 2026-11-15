"""Seller and creator logins for the pilot apps.

Revision ID: 0005_portal_accounts
Revises: 0004_attribution_keys
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_portal_accounts"
down_revision: Union[str, Sequence[str], None] = "0004_attribution_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portal_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=True),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_portal_accounts_email"),
        sa.CheckConstraint("role IN ('seller', 'creator')", name="ck_portal_accounts_role"),
        sa.CheckConstraint(
            "(role = 'seller' AND shop_id IS NOT NULL AND creator_id IS NULL) OR "
            "(role = 'creator' AND creator_id IS NOT NULL AND shop_id IS NULL)",
            name="ck_portal_accounts_subject",
        ),
    )
    op.create_index("ix_portal_accounts_shop_id", "portal_accounts", ["shop_id"])
    op.create_index("ix_portal_accounts_creator_id", "portal_accounts", ["creator_id"])


def downgrade() -> None:
    op.drop_index("ix_portal_accounts_creator_id", table_name="portal_accounts")
    op.drop_index("ix_portal_accounts_shop_id", table_name="portal_accounts")
    op.drop_table("portal_accounts")
