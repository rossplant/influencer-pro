"""Names and an optional second email on a UAT set.

Revision ID: 0021_uat_set_names
Revises: 0020_invite_opt_outs
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_uat_set_names"
down_revision: Union[str, Sequence[str], None] = "0020_invite_opt_outs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("uat_sets") as batch:
        batch.add_column(sa.Column("first_name", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("brand_name", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("creatr_email", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("uat_sets") as batch:
        batch.drop_column("creatr_email")
        batch.drop_column("brand_name")
        batch.drop_column("first_name")
