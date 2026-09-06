import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# `alembic upgrade` may be invoked from anywhere (CLI, or programmatically at
# startup), so make `app` importable regardless of the working directory.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import Base, engine  # noqa: E402

# Import every model module so Base.metadata is complete for autogenerate.
from app.models import chaptarr as _chaptarr_models  # noqa: F401,E402
from app.models import download as _download_models  # noqa: F401,E402
from app.models import potation as _potation_models  # noqa: F401,E402
from app.models import user as _user_models  # noqa: F401,E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure_and_run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER most things, so Alembic has to rebuild the table.
        # PostgreSQL can, and batch mode there would rebuild tables for nothing.
        render_as_batch=(connection.dialect.name == "sqlite"),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=engine.url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A caller that already holds a connection (the startup path) passes it in,
    # so migrations run on the same connection rather than opening a second one.
    existing = config.attributes.get("connection")
    if existing is not None:
        _configure_and_run(existing)
        return
    with engine.connect() as connection:
        _configure_and_run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
