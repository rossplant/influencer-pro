"""Store marketing consent on shops, creators, and pilot applications.

Revision ID: 0007_marketing_consent
Revises: 0006_billing_pilots
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_marketing_consent"
down_revision: Union[str, Sequence[str], None] = "0006_billing_pilots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shops", sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("creators", sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(
        "pilot_applications",
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("pilot_applications", "marketing_consent")
    op.drop_column("creators", "marketing_consent")
    op.drop_column("shops", "marketing_consent")
