import json
import os
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..schemas.settings import LibationSettings, AppStats, DownloadsPerUser
from ..schemas.auth import MessageResponse
from ..models.download import Download
from ..models.user import User
from ..config import settings as app_settings
from ..services import cli as cli_svc
from ..services.libation import count_books
from .auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])

APPSETTINGS_PATH = os.path.join(app_settings.LIBATION_CONFIG, "appsettings.json")

# Maps our schema field names to known Libation appsettings.json key variants
_FIELD_MAP = {
    "decrypt_to_lossy": ["DecryptToLossy"],
    "split_files_by_chapter": ["SplitFilesByChapter"],
    "download_episodes": ["DownloadEpisodes"],
    "create_cue_sheet": ["CreateCueSheet"],
    "save_cover_art_to_file": ["SaveCoverArtToFile"],
    "allow_audiobook_overwrite": ["AllowAudiobookOverwrite"],
    "strip_audible_brand_audio": ["StripAudibleBrandAudio"],
    "strip_unabridged": ["StripUnabridged"],
    "books_directory": ["Books"],
}


def _read_raw() -> dict:
    if not os.path.exists(APPSETTINGS_PATH):
        return {}
    try:
        with open(APPSETTINGS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_raw(data: dict) -> None:
    os.makedirs(os.path.dirname(APPSETTINGS_PATH), exist_ok=True)
    with open(APPSETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _parse_settings(raw: dict) -> LibationSettings:
    result = {}
    for field, keys in _FIELD_MAP.items():
        for key in keys:
            if key in raw:
                result[field] = raw[key]
                break
    return LibationSettings(**result)


@router.get("/libation", response_model=LibationSettings)
def get_libation_settings(_=Depends(get_current_user)):
    return _parse_settings(_read_raw())


@router.put("/libation", response_model=LibationSettings)
def update_libation_settings(body: LibationSettings, _=Depends(get_current_user)):
    raw = _read_raw()
    for field, keys in _FIELD_MAP.items():
        value = getattr(body, field)
        if value is not None:
            raw[keys[0]] = value
    _write_raw(raw)
    return _parse_settings(raw)


@router.get("/stats", response_model=AppStats)
async def get_stats(db: Session = Depends(get_db), _=Depends(get_current_user)):
    total_books = count_books()

    total_downloads = db.query(func.count(Download.id)).scalar() or 0

    try:
        accounts = await cli_svc.list_accounts()
        accounts_count = len(accounts)
    except Exception:
        accounts_count = 0

    rows = (
        db.query(User.username, func.count(Download.id).label("cnt"))
        .outerjoin(Download, Download.user_id == User.id)
        .group_by(User.id, User.username)
        .order_by(func.count(Download.id).desc())
        .all()
    )
    downloads_per_user = [DownloadsPerUser(username=r.username, count=r.cnt) for r in rows]

    return AppStats(
        total_books=total_books,
        total_downloads=total_downloads,
        accounts_count=accounts_count,
        downloads_per_user=downloads_per_user,
    )
