"""Admin password reset token and session epoch.

Revision ID: 0016_admin_password_reset
Revises: 0015_portal_email_role
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_admin_password_reset"
down_revision: Union[str, Sequence[str], None] = "0015_portal_email_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("admin_users") as batch:
        batch.add_column(sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("reset_token_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("reset_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("admin_users") as batch:
        batch.drop_column("reset_expires_at")
        batch.drop_column("reset_token_hash")
        batch.drop_column("session_epoch")
