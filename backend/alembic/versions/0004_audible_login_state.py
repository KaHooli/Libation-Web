"""audible login state — in-flight device registrations

Replaces the live `libationcli login-external` subprocess that was held in a
module-level dict between the two halves of the sign-in flow.

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audible_login_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("marketplace", sa.String(), nullable=False),
        sa.Column("country_code", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("serial", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("started_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["started_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state"),
    )
    op.create_index(
        op.f("ix_audible_login_states_state"), "audible_login_states", ["state"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_audible_login_states_state"), table_name="audible_login_states")
    op.drop_table("audible_login_states")
