import asyncio
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .auth import get_current_user
from ..database import SessionLocal, get_db
from ..models.download import Download, Scan
from ..schemas.downloads import DownloadRequest, DownloadResponse, ScanResponse
from ..services import cli

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


# ── Background tasks ──────────────────────────────────────────────────────────

async def _run_download(download_id: int, book_id: str) -> None:
    with SessionLocal() as db:
        dl = db.get(Download, download_id)
        if dl:
            dl.status = "running"
            dl.started_at = datetime.now(timezone.utc)
            db.commit()

    async def _on_progress(pct: int, _line: str) -> None:
        with SessionLocal() as db:
            dl = db.get(Download, download_id)
            if dl:
                dl.progress = pct
                db.commit()

    try:
        exit_code, output = await cli.run_liberate([book_id], on_progress=_on_progress)
    except Exception as e:
        exit_code, output = 1, str(e)

    with SessionLocal() as db:
        dl = db.get(Download, download_id)
        if dl:
            dl.status = "complete" if exit_code == 0 else "error"
            dl.progress = 100 if exit_code == 0 else dl.progress
            dl.completed_at = datetime.now(timezone.utc)
            if exit_code != 0:
                dl.error_message = output[-500:]
            db.commit()


async def _run_scan(scan_id: int) -> None:
    try:
        exit_code, output = await cli.run_scan()
    except Exception as e:
        exit_code, output = 1, str(e)

    books_added = 0
    m = re.search(r"(\d+)\s+new\s+book", output, re.IGNORECASE)
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


# ── Scan endpoints ────────────────────────────────────────────────────────────

@router.post("/scan", response_model=ScanResponse, tags=["library"])
def start_scan(db: Session = Depends(get_db), _=Depends(get_current_user)):
    scan = Scan(status="running", started_at=datetime.now(timezone.utc))
    db.add(scan)
    db.commit()
    db.refresh(scan)
    asyncio.create_task(_run_scan(scan.id))
    return scan


@router.get("/scan/latest", response_model=ScanResponse, tags=["library"])
def latest_scan(db: Session = Depends(get_db), _=Depends(get_current_user)):
    scan = db.query(Scan).order_by(Scan.id.desc()).first()
    if not scan:
        raise HTTPException(status_code=404, detail="No scans yet")
    return scan


# ── Download endpoints ────────────────────────────────────────────────────────

@router.post("", response_model=DownloadResponse, status_code=201)
def queue_download(
    body: DownloadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Prevent duplicate active downloads for the same book
    existing = (
        db.query(Download)
        .filter(
            Download.book_id == body.book_id,
            Download.status.in_(["queued", "running"]),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This book is already queued or downloading",
        )

    dl = Download(
        book_id=body.book_id,
        book_title=body.book_title,
        user_id=current_user.id,
        status="queued",
    )
    db.add(dl)
    db.commit()
    db.refresh(dl)

    asyncio.create_task(_run_download(dl.id, body.book_id))
    return dl


@router.get("", response_model=list[DownloadResponse])
def list_downloads(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(Download).order_by(Download.created_at.desc()).limit(200).all()


@router.get("/{download_id}", response_model=DownloadResponse)
def get_download(
    download_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    dl = db.get(Download, download_id)
    if not dl:
        raise HTTPException(status_code=404, detail="Download not found")
    return dl


@router.delete("/{download_id}", status_code=204)
def delete_download(
    download_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    dl = db.get(Download, download_id)
    if not dl:
        raise HTTPException(status_code=404, detail="Download not found")
    if dl.status in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete an active download",
        )
    db.delete(dl)
    db.commit()
