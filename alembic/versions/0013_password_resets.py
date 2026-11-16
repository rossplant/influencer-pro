"""Single-use seller and creator password reset links.

Revision ID: 0013_password_resets
Revises: 0012_seller_catalogue
Create Date: 2026-11-16

The raw token is not stored. token_hash is SHA-256 of the secret in the link.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_password_resets"
down_revision: Union[str, Sequence[str], None] = "0012_seller_catalogue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=True),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_password_resets_token_hash"),
        sa.CheckConstraint("role IN ('seller', 'creator')", name="ck_password_resets_role"),
    )
    op.create_index("ix_password_resets_shop_id", "password_resets", ["shop_id"])
    op.create_index("ix_password_resets_creator_id", "password_resets", ["creator_id"])


def downgrade() -> None:
    op.drop_index("ix_password_resets_creator_id", table_name="password_resets")
    op.drop_index("ix_password_resets_shop_id", table_name="password_resets")
    op.drop_table("password_resets")
