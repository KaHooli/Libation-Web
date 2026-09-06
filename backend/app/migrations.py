"""Schema migration, run once at startup.

Replaces `Base.metadata.create_all` + the hand-rolled `_migrate_db`. `create_all`
never alters an existing table, which is fine while a schema only grows new
tables but strands any deployed database the moment a column changes.

Three cases, all handled by `run_migrations`:

  * **Fresh database** — no tables at all. Alembic runs every revision.
  * **Existing pre-Alembic database** — has `users` but no `alembic_version`.
    The legacy catch-up runs, the database is stamped at the baseline, and
    Alembic applies everything after it. Nothing is rebuilt and no data moves.
  * **Already under Alembic** — upgrade to head.

Every entry point takes an optional engine so the test suite can drive the same
code against a throwaway SQLite file and against PostgreSQL.
"""

from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from .database import engine as default_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_DIR = BACKEND_DIR / "alembic"

#: The revision representing the schema as it was before Alembic existed.
BASELINE_REVISION = "0001"


def alembic_config(connection: Optional[Connection] = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    # Set explicitly so the config resolves regardless of working directory.
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def _engine(eng: Optional[Engine] = None) -> Engine:
    return default_engine if eng is None else eng


def table_names(eng: Optional[Engine] = None) -> set[str]:
    with _engine(eng).connect() as connection:
        return set(inspect(connection).get_table_names())


def needs_legacy_adoption(eng: Optional[Engine] = None) -> bool:
    """True for a database built by the pre-Alembic code path."""
    tables = table_names(eng)
    return "alembic_version" not in tables and "users" in tables


def adopt_legacy_database(eng: Optional[Engine] = None) -> None:
    """Catch a pre-Alembic database up to the baseline, then stamp it."""
    target = _engine(eng)

    if target.dialect.name == "sqlite":
        # PRAGMA-based — and only a SQLite database can predate Alembic, since
        # PostgreSQL support arrived alongside it.
        from .legacy_migrations import migrate_pre_alembic

        migrate_pre_alembic(target)

    with target.connect() as connection:
        command.stamp(alembic_config(connection), BASELINE_REVISION)
    print(f"[Libation] Adopted existing database at revision {BASELINE_REVISION}")


def upgrade_to_head(eng: Optional[Engine] = None) -> None:
    with _engine(eng).connect() as connection:
        command.upgrade(alembic_config(connection), "head")


def run_migrations(eng: Optional[Engine] = None) -> None:
    if needs_legacy_adoption(eng):
        adopt_legacy_database(eng)
    upgrade_to_head(eng)


# ── Data seeding ──────────────────────────────────────────────────────────────
# `ON CONFLICT ... DO NOTHING` rather than SQLite's `INSERT OR IGNORE`, so the
# same statement runs on PostgreSQL.

_UPSERT_IGNORE = text(
    "INSERT INTO system_settings (key, value) VALUES (:k, :v) "
    "ON CONFLICT (key) DO NOTHING"
)


def system_setting_defaults() -> dict[str, str]:
    from .services.chaptarr import SETTING_KEYS as CHAPTARR_SETTING_KEYS

    defaults: dict[str, str] = {"last_auto_download_at": ""}
    defaults.update(CHAPTARR_SETTING_KEYS)
    return defaults


def seed_system_settings(db: Session) -> None:
    """Ensure every known settings key exists, without touching values already set."""
    conn = db.connection()
    for key, value in system_setting_defaults().items():
        conn.execute(_UPSERT_IGNORE, {"k": key, "v": value})
    db.commit()
