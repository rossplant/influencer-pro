"""One email can hold a supplier login and a Creatr login.

Revision ID: 0015_portal_email_role
Revises: 0014_uat_feedback
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0015_portal_email_role"
down_revision: Union[str, Sequence[str], None] = "0014_uat_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("portal_accounts") as batch:
        batch.drop_constraint("uq_portal_accounts_email", type_="unique")
        batch.create_unique_constraint("uq_portal_accounts_email_role", ["email", "role"])


def downgrade() -> None:
    with op.batch_alter_table("portal_accounts") as batch:
        batch.drop_constraint("uq_portal_accounts_email_role", type_="unique")
        batch.create_unique_constraint("uq_portal_accounts_email", ["email"])
