"""Billing reminders log, terms acceptance records, and seller application fields.

Revision ID: 0009_reminders_and_acceptance
Revises: 0008_attribution_window
Create Date: 2026-11-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_reminders_and_acceptance"
down_revision: Union[str, Sequence[str], None] = "0008_attribution_window"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("shops", "creators", "pilot_applications"):
        op.add_column(
            table,
            sa.Column("email_marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.add_column("billing_subscriptions", sa.Column("discount_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pilot_applications", sa.Column("business_name", sa.String(length=255), nullable=True))
    op.add_column("pilot_applications", sa.Column("abn", sa.String(length=11), nullable=True))
    op.add_column(
        "pilot_applications",
        sa.Column("authority_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "terms_acceptances",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("context", sa.String(length=64), nullable=False),
        sa.Column("terms_version", sa.String(length=64), nullable=False),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("text_shown", sa.Text(), nullable=False),
        sa.Column("price_shown", sa.Text(), nullable=True),
        sa.Column("confirmations", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_terms_acceptances_subject_id", "terms_acceptances", ["subject_id"])
    op.create_table(
        "reminder_sends",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("reminder_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reminder_sends")
    op.drop_index("ix_terms_acceptances_subject_id", table_name="terms_acceptances")
    op.drop_table("terms_acceptances")
    op.drop_column("pilot_applications", "authority_confirmed")
    op.drop_column("pilot_applications", "abn")
    op.drop_column("pilot_applications", "business_name")
    op.drop_column("billing_subscriptions", "discount_end")
    for table in ("shops", "creators", "pilot_applications"):
        op.drop_column(table, "email_marketing_consent")
