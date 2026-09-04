"""potation schema — tables the native engine owns

Additive only. The pre-existing `downloads`, `scans` and
`audible_account_settings` tables stay in place until Phase D, so Phase A
changes no API behaviour.

`audible_account_settings` rows are copied into `audible_accounts` so the
per-account auto-download choice and the "who added this" attribution survive
the move. Accounts themselves are re-authorised (tokens live in Libation's
`AccountsSettings.json`, which we deliberately do not read), so a copied row
starts with no `auth_blob` and `needs_reauth` set.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audible_accounts",
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("locale", sa.String(), nullable=False, server_default="us"),
        sa.Column("marketplace_id", sa.String(), nullable=True),
        sa.Column("account_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("auth_blob", sa.Text(), nullable=True),
        sa.Column("added_by_user_id", sa.Integer(), nullable=True),
        sa.Column("auto_download", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("needs_reauth", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("account_id"),
    )

    op.create_table(
        "books",
        sa.Column("asin", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("parent_asin", sa.String(), nullable=True),
        sa.Column("part_index", sa.Integer(), nullable=True),
        sa.Column("is_multipart_parent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("subtitle", sa.String(), nullable=True),
        sa.Column("authors", sa.JSON(), nullable=True),
        sa.Column("narrators", sa.JSON(), nullable=True),
        sa.Column("series_name", sa.String(), nullable=True),
        sa.Column("series_sequence", sa.String(), nullable=True),
        sa.Column("length_minutes", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("is_abridged", sa.Boolean(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("content_delivery_type", sa.String(), nullable=True),
        sa.Column("is_audible_plus", sa.Boolean(), nullable=True),
        sa.Column("purchase_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publisher", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.String(), nullable=True),
        sa.Column("liberated_override", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["audible_accounts.account_id"]),
        sa.ForeignKeyConstraint(["parent_asin"], ["books.asin"]),
        sa.PrimaryKeyConstraint("asin"),
    )
    op.create_index(op.f("ix_books_account_id"), "books", ["account_id"], unique=False)
    op.create_index(op.f("ix_books_parent_asin"), "books", ["parent_asin"], unique=False)

    op.create_table(
        "book_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_asin", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="audio"),
        sa.Column("part_index", sa.Integer(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("mtime", sa.Float(), nullable=True),
        sa.Column("root", sa.String(), nullable=True),
        sa.Column("asin_source", sa.String(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["book_asin"], ["books.asin"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
    )
    op.create_index(op.f("ix_book_files_book_asin"), "book_files", ["book_asin"], unique=False)

    op.create_table(
        "audible_licenses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_asin", sa.String(), nullable=False),
        sa.Column("drm_type", sa.String(), nullable=True),
        sa.Column("key_ciphertext", sa.Text(), nullable=True),
        sa.Column("iv_ciphertext", sa.Text(), nullable=True),
        sa.Column("download_url", sa.Text(), nullable=True),
        sa.Column("url_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acr", sa.String(), nullable=True),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["book_asin"], ["books.asin"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audible_licenses_book_asin"), "audible_licenses", ["book_asin"], unique=False
    )

    op.create_table(
        "download_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_asin", sa.String(), nullable=False),
        sa.Column("book_title", sa.String(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(), nullable=False, server_default="queued"),
        sa.Column("stage_progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_total", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("license_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["license_id"], ["audible_licenses.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_download_jobs_book_asin"), "download_jobs", ["book_asin"], unique=False)
    op.create_index(op.f("ix_download_jobs_state"), "download_jobs", ["state"], unique=False)
    op.create_index(op.f("ix_download_jobs_user_id"), "download_jobs", ["user_id"], unique=False)

    op.create_table(
        "download_quota",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("book_asin", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("is_audible_plus", sa.Boolean(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_download_quota_account_id"), "download_quota", ["account_id"], unique=False)
    op.create_index(op.f("ix_download_quota_book_asin"), "download_quota", ["book_asin"], unique=False)
    op.create_index(op.f("ix_download_quota_recorded_at"), "download_quota", ["recorded_at"], unique=False)

    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("roots", sa.JSON(), nullable=True),
        sa.Column("files_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("books_matched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmatched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    _carry_over_account_settings()


def _carry_over_account_settings() -> None:
    """Copy `audible_account_settings` into `audible_accounts`.

    The auto-download choice and the "who added this" attribution are worth
    keeping; the credentials are not carried across because they live in
    Libation's `AccountsSettings.json`, which Potation deliberately does not
    read. Every carried row therefore needs re-authorisation.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "audible_account_settings" not in inspector.get_table_names():
        return

    rows = bind.execute(
        sa.text(
            "SELECT account_id, added_by_user_id, auto_download "
            "FROM audible_account_settings"
        )
    ).fetchall()
    if not rows:
        return

    for account_id, added_by_user_id, auto_download in rows:
        bind.execute(
            sa.text(
                "INSERT INTO audible_accounts "
                "(account_id, locale, added_by_user_id, auto_download, is_active,"
                " needs_reauth, created_at) "
                "VALUES (:aid, 'us', :uid, :auto, :t, :t, :now) "
                "ON CONFLICT (account_id) DO NOTHING"
            ).bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True))),
            {
                "aid": account_id,
                "uid": added_by_user_id,
                "auto": bool(auto_download),
                "t": True,
                "now": _utcnow(),
            },
        )


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def downgrade() -> None:
    op.drop_table("reconciliation_runs")
    op.drop_index(op.f("ix_download_quota_recorded_at"), table_name="download_quota")
    op.drop_index(op.f("ix_download_quota_book_asin"), table_name="download_quota")
    op.drop_index(op.f("ix_download_quota_account_id"), table_name="download_quota")
    op.drop_table("download_quota")
    op.drop_index(op.f("ix_download_jobs_user_id"), table_name="download_jobs")
    op.drop_index(op.f("ix_download_jobs_state"), table_name="download_jobs")
    op.drop_index(op.f("ix_download_jobs_book_asin"), table_name="download_jobs")
    op.drop_table("download_jobs")
    op.drop_index(op.f("ix_audible_licenses_book_asin"), table_name="audible_licenses")
    op.drop_table("audible_licenses")
    op.drop_index(op.f("ix_book_files_book_asin"), table_name="book_files")
    op.drop_table("book_files")
    op.drop_index(op.f("ix_books_parent_asin"), table_name="books")
    op.drop_index(op.f("ix_books_account_id"), table_name="books")
    op.drop_table("books")
    op.drop_table("audible_accounts")
