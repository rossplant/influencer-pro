"""Order attribution flag and a queue for failed ad-platform sends.

Revision ID: 0008_attribution_window
Revises: 0007_marketing_consent
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_attribution_window"
down_revision: Union[str, Sequence[str], None] = "0007_marketing_consent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "attributed_orders",
        sa.Column("attributed", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "tracking_retries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tracking_retries_event_id", "tracking_retries", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_tracking_retries_event_id", table_name="tracking_retries")
    op.drop_table("tracking_retries")
    op.drop_column("attributed_orders", "attributed")
