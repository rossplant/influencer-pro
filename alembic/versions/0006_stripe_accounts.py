"""Stripe customers, Connect accounts, and webhook idempotency.

Revision ID: 0006_stripe_accounts
Revises: 0005_portal_accounts
Create Date: 2026-11-15

New money defaults are AUD. Existing currency values are left unchanged.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_stripe_accounts"
down_revision: Union[str, Sequence[str], None] = "0005_portal_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.add_column("shops", sa.Column("stripe_customer_id", sa.String(length=64), nullable=True))
    op.create_index("uq_shops_stripe_customer_id", "shops", ["stripe_customer_id"], unique=True)
    op.add_column("creators", sa.Column("stripe_account_id", sa.String(length=64), nullable=True))
    op.add_column(
        "creators",
        sa.Column("payouts_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "creators",
        sa.Column("details_submitted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    json_default = "'[]'::jsonb" if op.get_bind().dialect.name == "postgresql" else "'[]'"
    op.add_column(
        "creators",
        sa.Column(
            "stripe_requirements_due",
            _json(),
            nullable=False,
            server_default=sa.text(json_default),
        ),
    )
    op.create_index("uq_creators_stripe_account_id", "creators", ["stripe_account_id"], unique=True)
    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stripe_event_id", name="uq_stripe_events_event_id"),
    )
    for table in ("products", "subscriptions", "attributed_orders"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("currency", server_default="AUD")


def downgrade() -> None:
    for table in ("products", "subscriptions", "attributed_orders"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("currency", server_default="USD")
    op.drop_table("stripe_events")
    op.drop_index("uq_creators_stripe_account_id", table_name="creators")
    op.drop_column("creators", "stripe_requirements_due")
    op.drop_column("creators", "details_submitted")
    op.drop_column("creators", "payouts_enabled")
    op.drop_column("creators", "stripe_account_id")
    op.drop_index("uq_shops_stripe_customer_id", table_name="shops")
    op.drop_column("shops", "stripe_customer_id")
