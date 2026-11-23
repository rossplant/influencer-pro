"""Opt-outs for Plantr Collab invites.

Revision ID: 0020_invite_opt_outs
Revises: 0019_uat_invites
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_invite_opt_outs"
down_revision: Union[str, Sequence[str], None] = "0019_uat_invites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invite_opt_outs",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("invite_opt_outs")
