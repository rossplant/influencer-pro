"""Store the staging UAT supplier and Creatr so a redeploy keeps them.

Revision ID: 0017_uat_set
Revises: 0016_admin_password_reset
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_uat_set"
down_revision: Union[str, Sequence[str], None] = "0016_admin_password_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uat_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("shop_id", sa.String(length=36), nullable=False),
        sa.Column("creator_id", sa.String(length=36), nullable=False),
        sa.Column("audience", sa.Integer(), nullable=False, server_default="45000"),
        sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_press_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("uat_sets")
