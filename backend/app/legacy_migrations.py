"""Pre-Alembic schema catch-up, kept only to adopt existing databases.

Before Alembic there was a hand-rolled `_migrate_db` in `main.py` that added
columns with `PRAGMA table_info` checks and `ALTER TABLE`. Deployed SQLite
databases may sit at any point along that sequence, so this runs once against
such a database to bring it up to the `0001` baseline shape, after which it is
stamped and Alembic takes over.

It is SQLite-only by construction (`PRAGMA`), which is fine: a PostgreSQL
database cannot predate Alembic — Postgres support arrived with it.

Two deliberate differences from the original, both bug fixes. It works on a
`Connection` rather than a `Session`, because the original re-used the
connection it got from `db.connection()` after calling `db.commit()` — which
returns that connection to the pool and invalidates the reference. The original
only escaped this because the loop rarely had more than one column to add. And
it commits once at the end rather than after each statement, so a failure
part-way through leaves nothing half-applied.

Delete this module one release after Potation ships, along with the adoption
branch in `migrations.adopt_legacy_database`.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .config import settings

# Types match what `Base.metadata.create_all` declares, so a database adopted
# here ends up identical to one Alembic built from scratch. The original used
# TEXT for all three of the trailing columns, so databases that already ran
# those ALTERs keep TEXT — harmless on SQLite, where TEXT, VARCHAR and JSON are
# interchangeable for these values, and unreachable on PostgreSQL, which cannot
# predate Alembic.
_ADDED_COLUMNS = (
    ("is_admin", "BOOLEAN NOT NULL DEFAULT 0"),
    ("permissions", "JSON"),
    ("download_cap", "INTEGER"),
    ("audible_account_id", "VARCHAR"),
    ("owner_name", "VARCHAR"),
)


def migrate_pre_alembic(engine: Engine) -> None:
    """Bring a pre-Alembic SQLite database up to the 0001 baseline shape."""
    added: list[str] = []

    with engine.begin() as conn:
        users_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()
        }

        for name, ddl in _ADDED_COLUMNS:
            if name in users_cols:
                continue
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
            if name == "is_admin":
                conn.execute(
                    text("UPDATE users SET is_admin = 1 WHERE username = :u"),
                    {"u": settings.ADMIN_USERNAME},
                )
            added.append(name)

        # Never ORM models — the old code created these with raw SQL.
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS audible_account_settings "
            "(account_id TEXT PRIMARY KEY, added_by_user_id INTEGER, "
            "auto_download INTEGER NOT NULL DEFAULT 0)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS system_settings "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
        ))

    for name in added:
        print(f"[Libation] Migrated: added {name} column")
