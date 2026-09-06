"""oidc — single sign-on identity on users, plus in-flight login state

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table so this also works on SQLite, which cannot ALTER much.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("oidc_subject", sa.String(), nullable=True))
        batch.add_column(sa.Column("oidc_issuer", sa.String(), nullable=True))
    op.create_index(op.f("ix_users_oidc_subject"), "users", ["oidc_subject"], unique=False)

    op.create_table(
        "oidc_login_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("next_path", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state"),
    )
    op.create_index(op.f("ix_oidc_login_states_state"), "oidc_login_states", ["state"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_oidc_login_states_state"), table_name="oidc_login_states")
    op.drop_table("oidc_login_states")
    op.drop_index(op.f("ix_users_oidc_subject"), table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("oidc_issuer")
        batch.drop_column("oidc_subject")
