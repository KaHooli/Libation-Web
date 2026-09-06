import os
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import SessionLocal, ensure_db_directory
from .migrations import run_migrations, seed_system_settings
from .models import user as user_models  # noqa: F401 — registers models
from .models import download as download_models  # noqa: F401 — registers models
from .models import chaptarr as chaptarr_models  # noqa: F401 — registers models
from .models import potation as potation_models  # noqa: F401 — registers models
from .models.download import Scan, Download
from .api import auth as auth_router
from .api import library as library_router
from .api import accounts as accounts_router
from .api import downloads as downloads_router
from .api import users as users_router
from .api import settings as settings_router
from .api import liberate as liberate_router
from .api import updates as updates_router
from .api import logs as logs_router
from .api import chaptarr as chaptarr_router
from .services.auth import hash_password, get_user_by_username
from .services.logger import get_logger
from .models.user import User
from .config import settings
from .limiter import limiter

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


#: Deliberately excludes characters that are easy to misread out of a terminal
#: (i/l/1, o/O/0) — this password gets copied by hand from `docker logs`.
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password(groups: int = 4, size: int = 5) -> str:
    """A random password, hyphenated into groups so it can be read aloud.

    54 characters over 20 positions is ~115 bits, far past anything that
    matters here; the grouping is purely so a human can transcribe it.
    """
    import secrets

    return "-".join(
        "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(size))
        for _ in range(groups)
    )


def _announce_generated_password(username: str, password: str) -> None:
    """Print the generated password to stdout — and only stdout.

    Never `get_logger()`: that writes to /config/logs/libation-web.log, which
    lives on a mounted volume and would leave the password on disk long after
    it has been changed. Container stdout is where the user is already looking
    (`docker logs`), and it is not persisted by us.
    """
    line = "─" * 58
    print(
        f"\n┌{line}┐\n"
        f"│ Libation Web UI — first-run administrator account\n"
        f"│\n"
        f"│   Username:  {username}\n"
        f"│   Password:  {password}\n"
        f"│\n"
        f"│ You will be asked to change this the first time you sign in.\n"
        f"│ Until you do, a fresh password is generated on every restart —\n"
        f"│ so if you lose this one, restart the container and read again.\n"
        f"│ Set ADMIN_PASSWORD to choose the password yourself instead.\n"
        f"└{line}┘\n",
        flush=True,
    )


def _seed_admin(db: Session) -> None:
    # Use the same raw-SQL approach as _migrate_db so there are no ORM mapper
    # interactions with the dangling connection that _migrate_db may leave open.
    # `db.commit()` returns the DBAPI connection to the pool, so a handle held
    # across one is dead. Re-acquire after every commit rather than caching it.
    try:
        row = db.connection().execute(
            text("SELECT id, must_change_password FROM users WHERE username = :u"),
            {"u": settings.ADMIN_USERNAME},
        ).first()
        supplied = settings.admin_password_supplied

        if row is None:
            password = settings.ADMIN_PASSWORD if supplied else generate_password()
            db.connection().execute(
                text(
                    "INSERT INTO users (username, hashed_password, totp_enabled, is_active,"
                    " is_admin, must_change_password, created_at)"
                    " VALUES (:u, :pw, 0, 1, 1, :must, :ts)"
                ),
                {"u": settings.ADMIN_USERNAME, "pw": hash_password(password),
                 "must": 0 if supplied else 1, "ts": datetime.now(timezone.utc)},
            )
            db.commit()
            print(f"[Libation] Created admin user: {settings.ADMIN_USERNAME!r}", flush=True)
            if not supplied:
                _announce_generated_password(settings.ADMIN_USERNAME, password)
            return

        db.connection().execute(
            text("UPDATE users SET is_admin = 1 WHERE username = :u AND is_admin = 0"),
            {"u": settings.ADMIN_USERNAME},
        )
        db.commit()

        # The generated password has still not been replaced. Roll a new one and
        # print it again: a bcrypt hash cannot be read back, so without this the
        # only recovery from "I closed the terminal" is deleting app.db.
        if row[1] and not supplied:
            password = generate_password()
            db.connection().execute(
                text("UPDATE users SET hashed_password = :pw WHERE username = :u"),
                {"pw": hash_password(password), "u": settings.ADMIN_USERNAME},
            )
            db.commit()
            _announce_generated_password(settings.ADMIN_USERNAME, password)
    except Exception as exc:
        import traceback
        print(f"[Libation] ERROR seeding admin user: {exc}", flush=True)
        traceback.print_exc()
        get_logger().error("[startup] Failed to seed admin user: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_db_directory()
    run_migrations()
    logger = get_logger()
    logger.info("[startup] Libation Web UI starting up")
    with SessionLocal() as db:
        seed_system_settings(db)
        _seed_admin(db)
        now = datetime.now(timezone.utc)
        stuck_scans = db.query(Scan).filter(Scan.status == "running").all()
        if stuck_scans:
            for s in stuck_scans:
                s.status = "error"
                s.completed_at = now
                s.error_message = "Interrupted by server restart"
            db.commit()
            print(f"[Libation] Reset {len(stuck_scans)} stuck scan(s) to error")
        stuck_downloads = db.query(Download).filter(
            Download.status.in_(["queued", "running"])
        ).all()
        if stuck_downloads:
            for d in stuck_downloads:
                d.status = "error"
                d.completed_at = now
                d.error_message = "Interrupted by server restart"
            db.commit()
            print(f"[Libation] Reset {len(stuck_downloads)} stuck download(s) to error")
            logger.warning("[startup] Reset %d stuck download(s) to error (server was restarted)", len(stuck_downloads))
    logger.info("[startup] Ready")
    yield


app = FastAPI(title="Libation API", version="0.4.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(library_router.router)
app.include_router(accounts_router.router)
app.include_router(downloads_router.router)
app.include_router(users_router.router)
app.include_router(settings_router.router)
app.include_router(liberate_router.router)
app.include_router(updates_router.router)
app.include_router(logs_router.router)
app.include_router(chaptarr_router.router)


@app.get("/api/health", include_in_schema=False)
def health():
    return JSONResponse({"status": "ok", "version": "0.4.0"})


# Serve React build — must come after API routes
STATIC_DIR = "/app/static"
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=f"{STATIC_DIR}/assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        candidate = Path(STATIC_DIR) / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(f"{STATIC_DIR}/index.html")
