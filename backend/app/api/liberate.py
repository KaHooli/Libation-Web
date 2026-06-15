import asyncio
import re
from datetime import datetime, timedelta, timezone

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models.download import Download, Scan
from ..models.user import DEFAULT_PERMISSIONS
from ..schemas.liberate import LiberateResponse, DownloadCapStatus
from ..schemas.downloads import ScanResponse
from ..services import libation as lib_svc
from ..services import cli as cli_svc
from .auth import get_current_user

router = APIRouter(prefix="/api/liberate", tags=["liberate"])


def _require_permission(flag: str, user) -> None:
    if user.is_admin:
        return
    perms = user.permissions or DEFAULT_PERMISSIONS
    if not perms.get(flag, DEFAULT_PERMISSIONS.get(flag, True)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Permission denied: {flag}")


def _cap_status(user, db: Session) -> dict:
    """Returns cap accounting info for the current 12h window."""
    if user.is_admin or user.download_cap is None:
        return {"cap": None, "used": 0, "remaining": None, "resets_at": None}
    window_start = datetime.now(timezone.utc) - timedelta(hours=12)
    used = db.query(func.count(Download.id)).filter(
        Download.user_id == user.id,
        Download.created_at > window_start,
    ).scalar() or 0
    remaining = max(0, user.download_cap - used)
    # resets_at = oldest download in window + 12h
    oldest = db.query(func.min(Download.created_at)).filter(
        Download.user_id == user.id,
        Download.created_at > window_start,
    ).scalar()
    resets_at = None
    if oldest and used >= user.download_cap:
        oldest_utc = oldest.replace(tzinfo=timezone.utc) if oldest.tzinfo is None else oldest
        resets_at = (oldest_utc + timedelta(hours=12)).isoformat()
    return {"cap": user.download_cap, "used": used, "remaining": remaining, "resets_at": resets_at}


@router.get("/cap", response_model=DownloadCapStatus)
def get_cap_status(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _cap_status(current_user, db)


class BookStatusUpdate(BaseModel):
    liberated: bool


@router.patch("/books/{book_id}")
def update_book_status(
    book_id: str,
    body: BookStatusUpdate,
    current_user=Depends(get_current_user),
):
    _require_permission("can_liberate", current_user)
    ok = lib_svc.set_book_status(book_id, body.liberated)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return {"book_id": book_id, "liberated": body.liberated}


@router.get("/book-ids")
def list_liberate_book_ids(
    filter_status: str = "all",
    account_id: Optional[str] = None,
    search: str = "",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_permission("can_liberate", current_user)
    active_rows = db.query(Download).filter(Download.status.in_(["queued", "running"])).all()
    active_ids = {r.book_id for r in active_rows}
    ids = lib_svc.get_liberate_book_ids(
        filter_status=filter_status,
        account_id=account_id or None,
        active_download_ids=active_ids,
        search=search,
    )
    return {"ids": ids, "total": len(ids)}


@router.get("/books", response_model=LiberateResponse)
def list_liberate_books(
    filter_status: str = "all",
    page: int = 1,
    page_size: int = 48,
    account_id: Optional[str] = None,
    search: str = "",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_permission("can_liberate", current_user)

    # Build active download map: book_id → (status, progress)
    active_rows = (
        db.query(Download)
        .filter(Download.status.in_(["queued", "running"]))
        .all()
    )
    active = {r.book_id: (r.status, r.progress) for r in active_rows}

    result = lib_svc.get_liberate_books(
        active_downloads=active,
        filter_status=filter_status,
        page=page,
        page_size=page_size,
        account_id=account_id or None,
        search=search,
    )
    return result


async def _run_liberate_all(scan_id: int) -> None:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        if scan:
            scan.started_at = datetime.now(timezone.utc)
            db.commit()
    try:
        exit_code, output = await cli_svc.run_liberate()
    except Exception as e:
        exit_code, output = 1, str(e)

    books_added = 0
    m = re.search(r"(\d+)\s+book", output, re.IGNORECASE)
    if m:
        books_added = int(m.group(1))

    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        if scan:
            scan.status = "complete" if exit_code == 0 else "error"
            scan.completed_at = datetime.now(timezone.utc)
            scan.books_added = books_added
            scan.output = output[:4000]
            if exit_code != 0:
                scan.error_message = output[-500:]
            db.commit()


@router.post("/download-all", response_model=ScanResponse, status_code=202)
def download_all(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Download every unliberated book. Only available when user has no download cap."""
    _require_permission("can_liberate", current_user)

    if not current_user.is_admin and current_user.download_cap is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Download cap is set on your account. Use individual downloads.",
        )

    # Reuse Scan model to track bulk liberate progress
    scan = Scan(status="running", started_at=datetime.now(timezone.utc))
    db.add(scan)
    db.commit()
    db.refresh(scan)
    asyncio.create_task(_run_liberate_all(scan.id))
    return scan
