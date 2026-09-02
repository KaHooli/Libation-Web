import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings


def db_directory() -> Optional[str]:
    """Directory holding the SQLite file in DATABASE_URL, or None for other backends.

    Derived rather than hardcoded to `/data`: the deployed container mounts its
    volume there, but tests and local runs point DATABASE_URL somewhere else and
    have no business creating `/data` (and on an unprivileged host, cannot).
    """
    url = make_url(settings.DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return os.path.dirname(os.path.abspath(url.database)) or None


def ensure_db_directory() -> None:
    directory = db_directory()
    if directory:
        os.makedirs(directory, exist_ok=True)


ensure_db_directory()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
