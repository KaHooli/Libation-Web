"""must_change_password — force a new password off the generated one

A first startup with no ADMIN_PASSWORD set now mints a random password and
prints it to stdout rather than defaulting to admin/admin. This flag is what
holds the account at the change-password prompt until that generated password
has actually been replaced.

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default so rows that already exist get 0 rather than NULL: an
    # upgrade must never drop an existing install at a password prompt.
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("must_change_password")
