"""baseline — the schema as it stood before Potation

This reproduces exactly what `Base.metadata.create_all` plus the hand-rolled
`_migrate_db` in `app/main.py` produced, so that:

  * a fresh database gets it by running this revision, and
  * an existing deployed database gets `_migrate_db` run once to bring it up to
    this shape and is then *stamped* with this revision rather than rebuilt.

Both routes must end at the same schema; `scripts/test-potation.py` asserts it.

Revision ID: 0001
Revises:
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("totp_secret", sa.String(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=True),
        sa.Column("download_cap", sa.Integer(), nullable=True),
        sa.Column("audible_account_id", sa.String(), nullable=True),
        sa.Column("owner_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index(op.f("ix_sessions_id"), "sessions", ["id"], unique=False)
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)

    op.create_table(
        "scans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("books_added", sa.Integer(), nullable=True),
        sa.Column("output", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "downloads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.String(), nullable=False),
        sa.Column("book_title", sa.String(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_downloads_book_id"), "downloads", ["book_id"], unique=False)

    op.create_table(
        "chaptarr_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.String(), nullable=False),
        sa.Column("book_title", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("matched_by", sa.String(), nullable=True),
        sa.Column("command_id", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chaptarr_imports_book_id"), "chaptarr_imports", ["book_id"], unique=False
    )

    # These two were never ORM models — `_migrate_db` created them with raw SQL,
    # so they are declared here with the equivalent portable types.
    op.create_table(
        "audible_account_settings",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("added_by_user_id", sa.Integer(), nullable=True),
        sa.Column("auto_download", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("account_id"),
    )

    op.create_table(
        "system_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_table("audible_account_settings")
    op.drop_index(op.f("ix_chaptarr_imports_book_id"), table_name="chaptarr_imports")
    op.drop_table("chaptarr_imports")
    op.drop_index(op.f("ix_downloads_book_id"), table_name="downloads")
    op.drop_table("downloads")
    op.drop_table("scans")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
