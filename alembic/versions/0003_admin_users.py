"""Store the operator account created by /admin/setup.

Revision ID: 0003_admin_users
Revises: 0002_shortlist_objective
Create Date: 2026-11-15

0001 is already applied in production and is not edited. 0002 belongs to the
match revision on the parent branch, so this account is the next revision.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_admin_users"
down_revision: Union[str, Sequence[str], None] = "0002_shortlist_objective"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
    )


def downgrade() -> None:
    op.drop_table("admin_users")
