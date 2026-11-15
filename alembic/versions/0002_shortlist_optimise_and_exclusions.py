"""Store the match objective and excluded creators.

Revision ID: 0002_shortlist_objective
Revises: 0001_initial
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_shortlist_objective"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.add_column(
        "shortlists",
        sa.Column("optimise_for", sa.String(length=16), nullable=False, server_default=sa.text("'balanced'")),
    )
    op.create_table(
        "shortlist_exclusions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shortlist_id", sa.String(length=36), sa.ForeignKey("shortlists.id"), nullable=False),
        sa.Column("creator_id", sa.String(length=64), nullable=True),
        sa.Column("handle", sa.String(length=255), nullable=True),
        sa.Column("reasons", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_shortlist_exclusions_shortlist_id", "shortlist_exclusions", ["shortlist_id"])


def downgrade() -> None:
    op.drop_table("shortlist_exclusions")
    op.drop_column("shortlists", "optimise_for")
