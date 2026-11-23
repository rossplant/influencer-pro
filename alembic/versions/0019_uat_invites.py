"""Short UAT invite paths.

Revision ID: 0019_uat_invites
Revises: 0018_uat_invite_id
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_uat_invites"
down_revision: Union[str, Sequence[str], None] = "0018_uat_invite_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uat_invites",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("set_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_uat_invites_set_id", "uat_invites", ["set_id"])


def downgrade() -> None:
    op.drop_index("ix_uat_invites_set_id", table_name="uat_invites")
    op.drop_table("uat_invites")
