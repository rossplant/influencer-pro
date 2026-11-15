"""Billing subscriptions, pilots, and attribution. No Connect accounts.

Revision ID: 0006_billing_pilots
Revises: 0005_portal_accounts
Create Date: 2026-11-15

Boolean defaults use sa.false() or sa.true(). Existing currency values stay as stored.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_billing_pilots"
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
    op.add_column("shops", sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True))
    op.add_column("shops", sa.Column("subscription_status", sa.String(length=32), nullable=True))
    op.add_column("shops", sa.Column("billing_interval", sa.String(length=16), nullable=True))
    op.add_column("shops", sa.Column("catalogue", sa.String(length=16), nullable=False, server_default="open"))
    op.add_column("shops", sa.Column("first_look_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("shops", sa.Column("attribution_first", _json(), nullable=True))
    op.add_column("shops", sa.Column("attribution_last", _json(), nullable=True))
    op.add_column("shops", sa.Column("signup_code", sa.String(length=64), nullable=True))
    op.add_column("shops", sa.Column("signup_campaign", sa.String(length=64), nullable=True))
    op.add_column("shops", sa.Column("signup_channel", sa.String(length=64), nullable=True))

    op.add_column("creators", sa.Column("plan_tier", sa.String(length=32), nullable=False, server_default="member"))
    op.add_column("creators", sa.Column("plan_status", sa.String(length=32), nullable=False, server_default="invited"))
    op.add_column("creators", sa.Column("stripe_customer_id", sa.String(length=64), nullable=True))
    op.create_index("uq_creators_stripe_customer_id", "creators", ["stripe_customer_id"], unique=True)
    op.add_column("creators", sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True))
    op.add_column("creators", sa.Column("subscription_status", sa.String(length=32), nullable=True))
    op.add_column("creators", sa.Column("billing_interval", sa.String(length=16), nullable=True))
    op.add_column("creators", sa.Column("age_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("creators", sa.Column("founding_number", sa.Integer(), nullable=True))
    op.add_column("creators", sa.Column("attribution_first", _json(), nullable=True))
    op.add_column("creators", sa.Column("attribution_last", _json(), nullable=True))
    op.add_column("creators", sa.Column("signup_code", sa.String(length=64), nullable=True))
    op.add_column("creators", sa.Column("signup_campaign", sa.String(length=64), nullable=True))
    op.add_column("creators", sa.Column("signup_channel", sa.String(length=64), nullable=True))

    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_campaigns_shop_id", "campaigns", ["shop_id"])
    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stripe_event_id", name="uq_stripe_events_event_id"),
    )
    op.create_table(
        "billing_subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=True),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("stripe_customer_id", sa.String(length=64), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True),
        sa.Column("plan_tier", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("interval", sa.String(length=16), nullable=False),
        sa.Column("lookup_key", sa.String(length=64), nullable=True),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promo_code", sa.String(length=64), nullable=True),
        sa.Column("invite_code", sa.String(length=64), nullable=True),
        sa.Column("campaign", sa.String(length=64), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stripe_subscription_id", name="uq_billing_subscriptions_stripe_id"),
    )
    op.create_table(
        "pilot_cohorts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("seat_cap", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pilot_tier", sa.String(length=32), nullable=False),
        sa.Column("post_coupon_key", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("slug", name="uq_pilot_cohorts_slug"),
    )
    op.create_table(
        "pilot_invites",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("cohort_id", sa.String(length=36), sa.ForeignKey("pilot_cohorts.id"), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("campaign", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_pilot_invites_code"),
    )
    op.create_index("ix_pilot_invites_cohort_id", "pilot_invites", ["cohort_id"])
    op.create_table(
        "pilot_applications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("cohort_slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("categories", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("founding_number", sa.Integer(), nullable=True),
        sa.Column("attribution", _json(), nullable=True),
        sa.Column("age_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pilot_applications_cohort", "pilot_applications", ["cohort_slug"])
    op.create_table(
        "pilot_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("cohort_id", sa.String(length=36), sa.ForeignKey("pilot_cohorts.id"), nullable=False),
        sa.Column("application_id", sa.String(length=36), sa.ForeignKey("pilot_applications.id"), nullable=True),
        sa.Column("invite_id", sa.String(length=36), sa.ForeignKey("pilot_invites.id"), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("shop_id", sa.String(length=36), sa.ForeignKey("shops.id"), nullable=True),
        sa.Column("creator_id", sa.String(length=36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("founding_number", sa.Integer(), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pilot_memberships_cohort", "pilot_memberships", ["cohort_id"])
    op.create_table(
        "pilot_visits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("path", sa.String(length=128), nullable=False),
        sa.Column("cohort_slug", sa.String(length=64), nullable=True),
        sa.Column("campaign", sa.String(length=64), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "pilot_surveys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("subject_id", sa.String(length=36), nullable=True),
        sa.Column("cohort_slug", sa.String(length=64), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("feels_right", sa.String(length=16), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "email_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("template_key", sa.String(length=64), nullable=False),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "tracked_events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("subject_key", sa.String(length=255), nullable=True),
        sa.Column("event_name", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_key", name="uq_tracked_events_subject"),
    )
    for table in ("products", "subscriptions", "attributed_orders"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("currency", server_default="AUD")


def downgrade() -> None:
    for table in ("products", "subscriptions", "attributed_orders"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("currency", server_default="USD")
    op.drop_table("tracked_events")
    op.drop_table("email_messages")
    op.drop_table("pilot_surveys")
    op.drop_table("pilot_visits")
    op.drop_table("pilot_memberships")
    op.drop_index("ix_pilot_applications_cohort", table_name="pilot_applications")
    op.drop_table("pilot_applications")
    op.drop_index("ix_pilot_invites_cohort_id", table_name="pilot_invites")
    op.drop_table("pilot_invites")
    op.drop_table("pilot_cohorts")
    op.drop_table("billing_subscriptions")
    op.drop_table("stripe_events")
    op.drop_index("ix_campaigns_shop_id", table_name="campaigns")
    op.drop_table("campaigns")
    for column in (
        "signup_channel",
        "signup_campaign",
        "signup_code",
        "attribution_last",
        "attribution_first",
        "founding_number",
        "age_confirmed",
        "billing_interval",
        "subscription_status",
        "stripe_subscription_id",
    ):
        op.drop_column("creators", column)
    op.drop_index("uq_creators_stripe_customer_id", table_name="creators")
    op.drop_column("creators", "stripe_customer_id")
    op.drop_column("creators", "plan_status")
    op.drop_column("creators", "plan_tier")
    for column in (
        "signup_channel",
        "signup_campaign",
        "signup_code",
        "attribution_last",
        "attribution_first",
        "first_look_opt_in",
        "catalogue",
        "billing_interval",
        "subscription_status",
        "stripe_subscription_id",
    ):
        op.drop_column("shops", column)
    op.drop_index("uq_shops_stripe_customer_id", table_name="shops")
    op.drop_column("shops", "stripe_customer_id")
