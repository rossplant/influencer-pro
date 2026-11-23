"""UAT checklist notes.

Revision ID: 0014_uat_feedback
Revises: 0013_sample_requests
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_uat_feedback"
down_revision: Union[str, Sequence[str], None] = "0013_sample_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uat_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("step", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("uat_feedback")
