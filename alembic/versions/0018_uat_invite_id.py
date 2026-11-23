"""Clear a UAT invite that was marked sent when Resend did not accept it.

Revision ID: 0018_uat_invite_id
Revises: 0017_uat_set
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_uat_invite_id"
down_revision: Union[str, Sequence[str], None] = "0017_uat_set"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("uat_sets", sa.Column("invite_provider_id", sa.String(length=64), nullable=True))
    op.execute("UPDATE uat_sets SET invite_sent_at = NULL, last_press_id = NULL WHERE invite_provider_id IS NULL")


def downgrade() -> None:
    op.drop_column("uat_sets", "invite_provider_id")
