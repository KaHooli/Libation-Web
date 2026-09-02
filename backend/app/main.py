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

from .database import engine, SessionLocal, Base, ensure_db_directory
from .models import user as user_models  # noqa: F401 — registers models
from .models import download as download_models  # noqa: F401 — registers models
from .models import chaptarr as chaptarr_models  # noqa: F401 — registers models
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
from .services.chaptarr import SETTING_KEYS as CHAPTARR_SETTING_KEYS
from .services.logger import get_logger
from .models.user import User
from .config import settings
from .limiter import limiter

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


def _migrate_db(db: Session) -> None:
    """Add columns that were introduced after initial deployment."""
    conn = db.connection()
    users_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
    if "is_admin" not in users_cols:
        conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
        conn.execute(text("UPDATE users SET is_admin = 1 WHERE username = :u"), {"u": settings.ADMIN_USERNAME})
        db.commit()
        print("[Libation] Migrated: added is_admin column")
    if "permissions" not in users_cols:
        conn.execute(text("ALTER TABLE users ADD COLUMN permissions TEXT"))
        db.commit()
        print("[Libation] Migrated: added permissions column")
    if "download_cap" not in users_cols:
        conn.execute(text("ALTER TABLE users ADD COLUMN download_cap INTEGER"))
        db.commit()
        print("[Libation] Migrated: added download_cap column")
    if "audible_account_id" not in users_cols:
        conn.execute(text("ALTER TABLE users ADD COLUMN audible_account_id TEXT"))
        db.commit()
        print("[Libation] Migrated: added audible_account_id column")
    if "owner_name" not in users_cols:
        conn.execute(text("ALTER TABLE users ADD COLUMN owner_name TEXT"))
        db.commit()
        print("[Libation] Migrated: added owner_name column")

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS audible_account_settings "
        "(account_id TEXT PRIMARY KEY, added_by_user_id INTEGER, auto_download INTEGER NOT NULL DEFAULT 0)"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS system_settings "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
    ))
    conn.execute(text(
        "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('last_auto_download_at', '')"
    ))
    for key, default in CHAPTARR_SETTING_KEYS.items():
        conn.execute(
            text("INSERT OR IGNORE INTO system_settings (key, value) VALUES (:k, :v)"),
            {"k": key, "v": default},
        )
    db.commit()


def _seed_admin(db: Session) -> None:
    # Use the same raw-SQL approach as _migrate_db so there are no ORM mapper
    # interactions with the dangling connection that _migrate_db may leave open.
    try:
        conn = db.connection()
        row = conn.execute(
            text("SELECT id FROM users WHERE username = :u"),
            {"u": settings.ADMIN_USERNAME},
        ).first()
        if row is None:
            conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, totp_enabled, is_active, is_admin, created_at)"
                    " VALUES (:u, :pw, 0, 1, 1, :ts)"
                ),
                {"u": settings.ADMIN_USERNAME, "pw": hash_password(settings.ADMIN_PASSWORD),
                 "ts": datetime.now(timezone.utc)},
            )
            db.commit()
            print(f"[Libation] Created admin user: {settings.ADMIN_USERNAME!r}", flush=True)
        else:
            conn.execute(
                text("UPDATE users SET is_admin = 1 WHERE username = :u AND is_admin = 0"),
                {"u": settings.ADMIN_USERNAME},
            )
            db.commit()
    except Exception as exc:
        import traceback
        print(f"[Libation] ERROR seeding admin user: {exc}", flush=True)
        traceback.print_exc()
        get_logger().error("[startup] Failed to seed admin user: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_db_directory()
    Base.metadata.create_all(bind=engine)
    logger = get_logger()
    logger.info("[startup] Libation Web UI starting up")
    with SessionLocal() as db:
        _migrate_db(db)
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
