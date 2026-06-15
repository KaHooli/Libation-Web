import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .database import engine, SessionLocal, Base
from .models import user as user_models  # noqa: F401 — registers models
from .api import auth as auth_router
from .api import library as library_router
from .api import accounts as accounts_router
from .api import downloads as downloads_router
from .models import download as download_models  # noqa: F401 — registers models
from .services.auth import hash_password, get_user_by_username
from .models.user import User
from .config import settings


def _seed_admin(db: Session) -> None:
    if not get_user_by_username(db, settings.ADMIN_USERNAME):
        db.add(User(
            username=settings.ADMIN_USERNAME,
            hashed_password=hash_password(settings.ADMIN_PASSWORD),
        ))
        db.commit()
        print(f"[Libation] Created admin user: {settings.ADMIN_USERNAME!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("/data", exist_ok=True)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        _seed_admin(db)
    yield


app = FastAPI(title="Libation API", version="0.1.0", lifespan=lifespan)

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

# Serve React build — must come after API routes
STATIC_DIR = "/app/static"
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=f"{STATIC_DIR}/assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        return FileResponse(f"{STATIC_DIR}/index.html")
