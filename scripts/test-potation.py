#!/usr/bin/env python3
"""Tests for the Potation foundations: migrations, dual-backend support, creds.

Covers the things that are cheap to get wrong and expensive to discover in
production:

  * `DATABASE_URL` normalisation and per-backend engine arguments, since
    `check_same_thread` is a sqlite3 argument that raises on psycopg.
  * The three migration entry states — fresh, pre-Alembic, already migrated —
    and the promise the baseline revision makes: a database *adopted* from the
    old hand-rolled `_migrate_db` must end up with the same schema as one built
    from scratch by Alembic.
  * That adoption preserves existing rows rather than rebuilding tables.
  * Encryption of stored Audible credentials, including the failure mode that
    matters: a missing or replaced key file must be reported, not swallowed.
  * Timezone-aware timestamps surviving a round trip — PostgreSQL silently
    strips tzinfo from a naive column, which is why the new tables use
    `DateTime(timezone=True)`.

Runs against SQLite always. Also runs the whole database section against
PostgreSQL when `POTATION_TEST_POSTGRES_URL` is set, which is how CI catches
dialect bugs.

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-potation.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="potation-test-"))
CONFIG = WORKDIR / "config"
BOOKS = WORKDIR / "audiobooks"
DATA = WORKDIR / "data"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

# Must be set before `app.config` is imported.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "potation-test-only-not-a-real-secret")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin")

# Same guard as the Chaptarr suite: the app must confine itself to the
# directories its settings name. A hardcoded "/config" or "/data" merely
# succeeds on a root dev box while failing on an unprivileged host.
PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.database import (  # noqa: E402
    db_directory,
    engine_kwargs,
    is_sqlite,
    normalized_url,
)
from app.migrations import (  # noqa: E402
    BASELINE_REVISION,
    adopt_legacy_database,
    needs_legacy_adoption,
    run_migrations,
    seed_system_settings,
    system_setting_defaults,
    table_names,
    upgrade_to_head,
)

POTATION_TABLES = {
    "audible_accounts",
    "books",
    "book_files",
    "audible_licenses",
    "download_jobs",
    "download_quota",
    "reconciliation_runs",
}

LEGACY_TABLES = {
    "users",
    "sessions",
    "scans",
    "downloads",
    "chaptarr_imports",
    "audible_account_settings",
    "system_settings",
}


# ── URL handling and per-backend engine arguments ─────────────────────────────

def test_url_normalisation() -> None:
    for raw in ("postgres://u:p@h/db", "postgresql://u:p@h/db", "postgresql+psycopg2://u:p@h/db"):
        url = normalized_url(raw)
        assert url.drivername == "postgresql+psycopg", f"{raw} -> {url.drivername}"
    # An explicit driver is left alone, and SQLite is untouched.
    assert normalized_url("postgresql+asyncpg://u:p@h/db").drivername == "postgresql+asyncpg"
    assert normalized_url("sqlite:////data/app.db").drivername == "sqlite"
    print("✓ PostgreSQL URL aliases normalise to psycopg 3, SQLite untouched")

    # The Unraid template exposes DATABASE_URL as an optional field, so it can
    # arrive set-but-blank. That must fall back, not raise at startup.
    from app.config import DEFAULT_DATABASE_URL

    expected = make_url(DEFAULT_DATABASE_URL)
    for blank in ("", "   "):
        assert normalized_url(blank) == expected, f"{blank!r} -> {normalized_url(blank)}"
    print("✓ a blank DATABASE_URL falls back to the SQLite default")


def test_engine_kwargs() -> None:
    sqlite_kwargs = engine_kwargs(normalized_url("sqlite:////tmp/x.db"))
    assert sqlite_kwargs["connect_args"] == {"check_same_thread": False}

    pg_kwargs = engine_kwargs(normalized_url("postgres://u:p@h/db"))
    # check_same_thread is a sqlite3 driver argument; psycopg raises on it.
    assert "connect_args" not in pg_kwargs, pg_kwargs
    assert pg_kwargs["pool_pre_ping"] is True
    print("✓ engine arguments are per-backend (no check_same_thread on PostgreSQL)")


def test_db_directory_is_sqlite_only() -> None:
    # db_directory() reads the configured URL, which is SQLite under test.
    assert is_sqlite()
    assert db_directory() == str(DATA)
    print("✓ db_directory resolves the SQLite directory and skips other backends")


# ── Migration lifecycle, run once per backend ─────────────────────────────────

#: SQLite stores TEXT, VARCHAR and JSON identically for our purposes — the
#: values are always JSON documents or short strings, and SQLAlchemy does the
#: JSON serialisation in Python from the *model* declaration rather than the
#: column type. Older databases carry TEXT where a fresh one declares JSON or
#: VARCHAR, because that is what the original `ALTER TABLE` statements said.
#: Treat them as one type when comparing the two migration routes.
_INTERCHANGEABLE_TEXT = {"TEXT", "VARCHAR", "JSON"}


def _normalise_type(raw: str) -> str:
    upper = raw.upper()
    return "TEXT*" if upper in _INTERCHANGEABLE_TEXT else upper


def _schema_fingerprint(engine) -> dict:
    """Tables, columns and index names, for comparing two migration routes."""
    inspector = inspect(engine)
    fingerprint = {}
    for table in sorted(inspector.get_table_names()):
        if table == "alembic_version":
            continue
        columns = {
            c["name"]: _normalise_type(str(c["type"])) for c in inspector.get_columns(table)
        }
        indexes = sorted(i["name"] for i in inspector.get_indexes(table))
        fingerprint[table] = {"columns": columns, "indexes": indexes}
    return fingerprint


def _current_revision(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _make_engine(url: str):
    # The normalised URL must be what reaches create_engine, not just what the
    # kwargs are chosen from — a bare `postgresql://` would otherwise resolve to
    # psycopg2, which is not installed.
    normalised = normalized_url(url)
    return create_engine(normalised, **engine_kwargs(normalised))


def _drop_everything(engine) -> None:
    """Reset a database between scenarios, for whichever backend it is."""
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    else:
        path = engine.url.database
        engine.dispose()
        if path and path != ":memory:":
            Path(path).unlink(missing_ok=True)


def _build_legacy_sqlite(path: Path) -> None:
    """A database shaped like one the pre-Alembic code left behind.

    Deliberately mid-sequence, because that is the awkward real case: `users`
    already has `is_admin` and `permissions` from an earlier run of the old
    `_migrate_db` — and `permissions` is `TEXT`, the type the original ALTER
    used — but is still missing the three columns added after that, and neither
    raw-SQL table exists yet.
    """
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users ("
            " id INTEGER NOT NULL PRIMARY KEY,"
            " username VARCHAR NOT NULL,"
            " hashed_password VARCHAR NOT NULL,"
            " totp_secret VARCHAR,"
            " totp_enabled BOOLEAN NOT NULL,"
            " is_active BOOLEAN NOT NULL,"
            " created_at DATETIME,"
            " is_admin BOOLEAN NOT NULL DEFAULT 0,"
            " permissions TEXT)"
        ))
        conn.execute(text("CREATE UNIQUE INDEX ix_users_username ON users (username)"))
        conn.execute(text("CREATE INDEX ix_users_id ON users (id)"))
        conn.execute(text(
            "CREATE TABLE sessions ("
            " id INTEGER NOT NULL PRIMARY KEY,"
            " user_id INTEGER NOT NULL,"
            " refresh_token_hash VARCHAR NOT NULL,"
            " expires_at DATETIME NOT NULL,"
            " created_at DATETIME,"
            " last_used_at DATETIME,"
            " user_agent VARCHAR,"
            " ip_address VARCHAR,"
            " UNIQUE (refresh_token_hash),"
            " FOREIGN KEY(user_id) REFERENCES users (id))"
        ))
        conn.execute(text("CREATE INDEX ix_sessions_id ON sessions (id)"))
        conn.execute(text("CREATE INDEX ix_sessions_user_id ON sessions (user_id)"))
        conn.execute(text(
            "CREATE TABLE scans (id INTEGER NOT NULL PRIMARY KEY, status VARCHAR NOT NULL,"
            " books_added INTEGER, output VARCHAR, error_message VARCHAR,"
            " started_at DATETIME, completed_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE downloads (id INTEGER NOT NULL PRIMARY KEY, book_id VARCHAR NOT NULL,"
            " book_title VARCHAR, user_id INTEGER NOT NULL, status VARCHAR NOT NULL,"
            " progress INTEGER, error_message VARCHAR, started_at DATETIME,"
            " completed_at DATETIME, created_at DATETIME,"
            " FOREIGN KEY(user_id) REFERENCES users (id))"
        ))
        conn.execute(text("CREATE INDEX ix_downloads_book_id ON downloads (book_id)"))
        conn.execute(text(
            "CREATE TABLE chaptarr_imports (id INTEGER NOT NULL PRIMARY KEY,"
            " book_id VARCHAR NOT NULL, book_title VARCHAR, status VARCHAR NOT NULL,"
            " matched_by VARCHAR, command_id INTEGER, file_path VARCHAR, message VARCHAR,"
            " user_id INTEGER, created_at DATETIME, completed_at DATETIME,"
            " FOREIGN KEY(user_id) REFERENCES users (id))"
        ))
        conn.execute(text(
            "CREATE INDEX ix_chaptarr_imports_book_id ON chaptarr_imports (book_id)"
        ))
        # A real user and session, to prove adoption does not rebuild tables.
        # `permissions` carries a value so we can show data in the legacy TEXT
        # column survives a route that declares the column as JSON.
        conn.execute(text(
            "INSERT INTO users (id, username, hashed_password, totp_enabled, is_active,"
            " created_at, is_admin, permissions)"
            " VALUES (1, 'admin', 'not-a-real-hash', 0, 1, '2025-01-01 00:00:00', 1,"
            " '{\"can_download\": true}')"
        ))
        conn.execute(text(
            "INSERT INTO sessions (id, user_id, refresh_token_hash, expires_at)"
            " VALUES (1, 1, 'deadbeef', '2030-01-01 00:00:00')"
        ))
    engine.dispose()


def run_database_suite(url: str, label: str) -> dict:
    """The whole migration lifecycle against one backend. Returns the fresh-build
    fingerprint so callers can compare routes."""
    print(f"\n-- {label} --")

    # 1. Fresh database: Alembic builds everything.
    engine = _make_engine(url)
    _drop_everything(engine)
    engine = _make_engine(url)

    assert not needs_legacy_adoption(engine), "an empty database is not a legacy one"
    run_migrations(engine)

    tables = table_names(engine)
    missing = (LEGACY_TABLES | POTATION_TABLES) - tables
    assert not missing, f"missing after fresh migrate: {sorted(missing)}"
    assert "alembic_version" in tables
    print(f"✓ [{label}] fresh database migrates to head with all tables")

    fresh_fingerprint = _schema_fingerprint(engine)

    # 2. Re-running is a no-op, not an error.
    run_migrations(engine)
    assert _schema_fingerprint(engine) == fresh_fingerprint
    print(f"✓ [{label}] re-running migrations is idempotent")

    # 3. Settings seeding is portable and does not clobber set values.
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    with factory() as db:
        seed_system_settings(db)
        db.execute(
            text("UPDATE system_settings SET value = :v WHERE key = 'chaptarr_url'"),
            {"v": "http://chaptarr.local"},
        )
        db.commit()
    with factory() as db:
        seed_system_settings(db)  # second pass must not reset it
        kept = db.execute(
            text("SELECT value FROM system_settings WHERE key = 'chaptarr_url'")
        ).scalar()
        count = db.execute(text("SELECT COUNT(*) FROM system_settings")).scalar()
    assert kept == "http://chaptarr.local", kept
    assert count == len(system_setting_defaults()), count
    print(f"✓ [{label}] system settings seed once and keep values already set")

    # 4. Timezone-aware timestamps survive a round trip. PostgreSQL strips
    #    tzinfo from a naive column, which is the bug this guards against.
    aware = datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc)
    with factory() as db:
        db.execute(
            text(
                "INSERT INTO reconciliation_runs (status, files_scanned, books_matched,"
                " unmatched, started_at) VALUES ('complete', 1, 1, 0, :ts)"
            ),
            {"ts": aware},
        )
        db.commit()
        got = db.execute(text("SELECT started_at FROM reconciliation_runs")).scalar()
    if isinstance(got, str):  # SQLite hands back text
        got = datetime.fromisoformat(got)
    assert got.utcoffset() is not None, f"tzinfo was stripped: {got!r}"
    assert got.astimezone(timezone.utc) == aware, got
    print(f"✓ [{label}] timezone-aware timestamps round-trip intact")

    engine.dispose()
    return fresh_fingerprint


def run_legacy_adoption_suite(url: str, fresh_fingerprint: dict) -> None:
    """SQLite only: a pre-Alembic database is adopted, not rebuilt."""
    print("\n-- SQLite legacy adoption --")
    path = Path(url.replace("sqlite:///", ""))
    path.unlink(missing_ok=True)
    _build_legacy_sqlite(path)

    engine = _make_engine(url)
    assert needs_legacy_adoption(engine), "a pre-Alembic database must be detected"

    # A carried-over account setting, added the way the old code did.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS audible_account_settings "
            "(account_id TEXT PRIMARY KEY, added_by_user_id INTEGER,"
            " auto_download INTEGER NOT NULL DEFAULT 0)"
        ))
        conn.execute(text(
            "INSERT INTO audible_account_settings VALUES ('acct-1', 1, 1)"
        ))

    # Adoption stamps the baseline rather than replaying it, so the existing
    # tables are left exactly as they are.
    adopt_legacy_database(engine)
    assert _current_revision(engine) == BASELINE_REVISION, _current_revision(engine)
    assert not needs_legacy_adoption(engine), "adoption must be one-time"

    upgrade_to_head(engine)
    assert _current_revision(engine) != BASELINE_REVISION, "later revisions did not apply"

    with engine.connect() as conn:
        # Data survived — the tables were altered, not recreated.
        assert conn.execute(text("SELECT username FROM users WHERE id = 1")).scalar() == "admin"
        assert conn.execute(
            text("SELECT refresh_token_hash FROM sessions WHERE id = 1")
        ).scalar() == "deadbeef"
        # Columns the old _migrate_db added are present, the admin stayed
        # flagged, and the value in the legacy TEXT `permissions` column is intact.
        assert conn.execute(text("SELECT is_admin FROM users WHERE id = 1")).scalar() in (1, True)
        assert conn.execute(text("SELECT owner_name FROM users WHERE id = 1")).scalar() is None
        assert conn.execute(
            text("SELECT permissions FROM users WHERE id = 1")
        ).scalar() == '{"can_download": true}'
        # The account setting was carried into audible_accounts, needing re-auth.
        row = conn.execute(text(
            "SELECT added_by_user_id, auto_download, needs_reauth, auth_blob"
            " FROM audible_accounts WHERE account_id = 'acct-1'"
        )).first()
    assert row is not None, "audible_account_settings row was not carried over"
    added_by, auto, reauth, blob = row
    assert added_by == 1 and bool(auto) is True, row
    assert bool(reauth) is True, "a carried account has no credentials, so it needs re-auth"
    assert blob is None, "credentials must not be invented during migration"
    print("✓ pre-Alembic database is adopted with rows and settings preserved")

    adopted_fingerprint = _schema_fingerprint(engine)
    if adopted_fingerprint != fresh_fingerprint:
        for table in sorted(set(adopted_fingerprint) | set(fresh_fingerprint)):
            a, f = adopted_fingerprint.get(table), fresh_fingerprint.get(table)
            if a != f:
                print(f"  {table}:\n    adopted={a}\n    fresh  ={f}", file=sys.stderr)
        raise AssertionError(
            "adopted schema differs from a freshly migrated one — the baseline "
            "revision no longer matches what the old code produced"
        )
    print("✓ adopted schema is identical to a freshly migrated one")
    engine.dispose()


# ── Credential encryption ─────────────────────────────────────────────────────

def test_credentials() -> None:
    from app.services.potation import creds

    key_file = creds.key_path()
    if key_file.exists():
        key_file.unlink()

    secret = {"refresh_token": "Atnr|xyz", "adp_token": "{enc:...}", "device_private_key": "-----"}
    token = creds.encrypt_json(secret)
    assert creds.decrypt_json(token) == secret
    assert "refresh_token" not in token, "the blob must not be readable in the stored form"
    print("✓ credentials round-trip through encryption")

    assert key_file.exists(), "the key file should have been created on first use"
    mode = key_file.stat().st_mode & 0o777
    assert mode == 0o600, f"key file mode is {oct(mode)}, expected 0o600"
    print("✓ credential key is created 0600 on the config volume")

    # Losing or replacing the key must be reported, never silently ignored.
    from cryptography.fernet import Fernet

    key_file.write_bytes(Fernet.generate_key())
    try:
        creds.decrypt(token)
    except creds.CredentialDecryptError as exc:
        assert "re-authorised" in str(exc), str(exc)
    else:
        raise AssertionError("decrypting with a replaced key must raise")
    print("✓ a replaced key raises CredentialDecryptError rather than failing silently")

    # The forgiving variant is what account loading uses, so one bad blob
    # flags that account instead of taking startup down.
    assert creds.try_decrypt_json(token) is None
    assert creds.try_decrypt_json(None) is None
    print("✓ try_decrypt_json degrades to None so a lost key flags the account")


def main() -> None:
    test_url_normalisation()
    test_engine_kwargs()
    test_db_directory_is_sqlite_only()
    test_credentials()

    sqlite_url = f"sqlite:///{DATA / 'lifecycle.db'}"
    fresh = run_database_suite(sqlite_url, "sqlite")
    run_legacy_adoption_suite(sqlite_url, fresh)

    pg_url = os.environ.get("POTATION_TEST_POSTGRES_URL")
    if pg_url:
        run_database_suite(pg_url, "postgresql")
    else:
        print("\n- PostgreSQL section skipped (set POTATION_TEST_POSTGRES_URL to run it)")

    assert_no_stray_dirs()
    print("\n✓ no stray top-level directories created")
    print("\nAll Potation foundation checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
