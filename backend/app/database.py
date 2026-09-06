import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import DEFAULT_DATABASE_URL, settings


# What people actually paste into DATABASE_URL. SQLAlchemy maps a bare
# `postgresql://` to psycopg2, which this image does not ship, and rejects
# `postgres://` outright — so both are rewritten to the psycopg 3 driver.
_PG_ALIASES = frozenset({"postgres", "postgresql", "postgresql+psycopg2"})


def normalized_url(raw: Optional[str] = None) -> URL:
    """DATABASE_URL as a URL, with PostgreSQL aliases pointed at psycopg 3."""
    value = settings.DATABASE_URL if raw is None else raw
    # The Unraid template offers DATABASE_URL as an optional field, so it can
    # arrive set-but-blank. That means "use the default", not "crash at startup".
    value = (value or "").strip() or DEFAULT_DATABASE_URL

    url = make_url(value)
    if url.drivername in _PG_ALIASES:
        url = url.set(drivername="postgresql+psycopg")
    return url


def is_sqlite(url: Optional[URL] = None) -> bool:
    return (normalized_url() if url is None else url).get_backend_name() == "sqlite"


def db_directory() -> Optional[str]:
    """Directory holding the SQLite file in DATABASE_URL, or None for other backends.

    Derived rather than hardcoded to `/data`: the deployed container mounts its
    volume there, but tests and local runs point DATABASE_URL somewhere else and
    have no business creating `/data` (and on an unprivileged host, cannot).
    """
    url = normalized_url()
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return os.path.dirname(os.path.abspath(url.database)) or None


def ensure_db_directory() -> None:
    directory = db_directory()
    if directory:
        os.makedirs(directory, exist_ok=True)


def engine_kwargs(url: URL) -> dict:
    """Connect and pool arguments for whichever backend `url` names.

    `check_same_thread` is a sqlite3 driver argument; passing it to psycopg
    raises, so it can never be sent unconditionally.
    """
    if url.get_backend_name() == "sqlite":
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}


ensure_db_directory()

_url = normalized_url()
engine = create_engine(_url, **engine_kwargs(_url))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
