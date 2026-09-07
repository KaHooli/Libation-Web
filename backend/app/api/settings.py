import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.backup import RestoreReportResponse, RestoreRequest
from ..schemas.settings import LibationSettings, AppStats, DownloadsPerUser
from ..models.download import Download
from ..models.user import User
from ..services import appsettings, backup as backup_svc
from ..services import cli as cli_svc
from ..services.backup import BackupFormatError
from ..services.libation import count_books
from .auth import get_current_user
from .users import require_admin

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/libation", response_model=LibationSettings)
def get_libation_settings(_=Depends(get_current_user)):
    return LibationSettings(**appsettings.parse(appsettings.read_raw()))


@router.put("/libation", response_model=LibationSettings)
def update_libation_settings(body: LibationSettings, _=Depends(get_current_user)):
    return LibationSettings(**appsettings.apply(body.model_dump()))


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


# ── Backup and restore ───────────────────────────────────────────────────────
# Admin-only in both directions: with secrets included the file holds the
# Chaptarr API key and the OIDC client secret, and a restore rewrites how the
# whole deployment signs in.

@router.get("/backup")
def download_backup(
    include_secrets: bool = Query(
        default=True,
        description="Secrets are included by default so the restore actually "
                    "works. Turn this off for a copy that is safe to share.",
    ),
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    doc = backup_svc.export_settings(db, include_secrets=include_secrets)
    return Response(
        content=json.dumps(doc, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{backup_svc.suggested_filename()}"',
            # The file carries live credentials; keep it out of shared caches.
            "Cache-Control": "no-store",
        },
    )


@router.post("/restore", response_model=RestoreReportResponse)
def restore_backup(
    body: RestoreRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    # `exclude_unset` keeps "the file did not carry this" distinguishable from
    # "the file said null", which is what lets the report name the secrets it
    # could not set. The stored values are safe either way — the restore treats
    # null as absent — but a report that quietly omits them reads as a complete
    # restore when it was not.
    doc = body.backup.model_dump(exclude_unset=True)
    try:
        report = backup_svc.restore_settings(db, doc, sections=body.sections)
    except BackupFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    return RestoreReportResponse(**vars(report))
