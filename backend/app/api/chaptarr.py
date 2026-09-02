import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .auth import get_current_user
from .users import require_admin
from ..database import get_db
from ..models.chaptarr import ChaptarrImport
from ..models.user import DEFAULT_PERMISSIONS
from ..schemas.chaptarr import (
    ChaptarrConnectionTest,
    ChaptarrImportRequest,
    ChaptarrImportResponse,
    ChaptarrSettings,
    ChaptarrSettingsUpdate,
    ChaptarrStatus,
)
from ..services import chaptarr as chaptarr_svc
from ..services.chaptarr import ChaptarrError

router = APIRouter(prefix="/api/chaptarr", tags=["chaptarr"])


def _to_settings(cfg: chaptarr_svc.ChaptarrConfig, api_key_set: bool) -> ChaptarrSettings:
    return ChaptarrSettings(
        enabled=cfg.enabled,
        url=cfg.url,
        import_mode=cfg.import_mode,
        auto_import=cfg.auto_import,
        path_from=cfg.path_from,
        path_to=cfg.path_to,
        api_key_set=api_key_set,
    )


@router.get("/settings", response_model=ChaptarrSettings)
def get_settings(db: Session = Depends(get_db), _=Depends(require_admin)):
    cfg = chaptarr_svc.load_config(db)
    return _to_settings(cfg, bool(cfg.api_key))


@router.get("/status", response_model=ChaptarrStatus)
def get_status(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Whether Chaptarr is usable — safe for non-admins, leaks no connection details."""
    cfg = chaptarr_svc.load_config(db)
    return ChaptarrStatus(enabled=cfg.enabled, configured=cfg.configured)


@router.put("/settings", response_model=ChaptarrSettings)
def update_settings(
    body: ChaptarrSettingsUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    if body.import_mode is not None and body.import_mode not in chaptarr_svc.IMPORT_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"import_mode must be one of {', '.join(chaptarr_svc.IMPORT_MODES)}",
        )

    patch = {
        "chaptarr_enabled": body.enabled,
        "chaptarr_url": body.url,
        "chaptarr_import_mode": body.import_mode,
        "chaptarr_auto_import": body.auto_import,
        "chaptarr_path_from": body.path_from,
        "chaptarr_path_to": body.path_to,
    }
    # `api_key` omitted → keep the stored key; sent as "" → clear it.
    if body.api_key is not None:
        patch["chaptarr_api_key"] = body.api_key

    cfg = chaptarr_svc.save_config(db, {k: v for k, v in patch.items() if v is not None})
    return _to_settings(cfg, bool(cfg.api_key))


@router.post("/test", response_model=ChaptarrConnectionTest)
async def test_connection(db: Session = Depends(get_db), _=Depends(require_admin)):
    cfg = chaptarr_svc.load_config(db)
    try:
        return await chaptarr_svc.test_connection(cfg)
    except ChaptarrError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/import", response_model=list[ChaptarrImportResponse], status_code=202)
async def import_books(
    body: ChaptarrImportRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Push already-downloaded books to Chaptarr on demand.

    Returns immediately with one `running` record per book; the batch is worked
    sequentially in the background. Poll `GET /api/chaptarr/imports` for results.
    """
    if not current_user.is_admin:
        perms = current_user.permissions or DEFAULT_PERMISSIONS
        if not perms.get("can_download", DEFAULT_PERMISSIONS["can_download"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Permission denied: can_download")

    book_ids = list(dict.fromkeys(b.strip() for b in body.book_ids if b and b.strip()))
    if not book_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="No book IDs supplied")

    cfg = chaptarr_svc.load_config(db)
    if not cfg.configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chaptarr is not configured — set its URL and API key in Settings.",
        )

    record_ids = [
        chaptarr_svc.create_record(book_id, user_id=current_user.id)
        for book_id in book_ids
    ]
    asyncio.create_task(
        chaptarr_svc.import_books(book_ids, record_ids, cfg, current_user.id)
    )
    return (
        db.query(ChaptarrImport)
        .filter(ChaptarrImport.id.in_(record_ids))
        .order_by(ChaptarrImport.id)
        .all()
    )


@router.get("/imports", response_model=list[ChaptarrImportResponse])
def list_imports(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return (
        db.query(ChaptarrImport)
        .order_by(ChaptarrImport.created_at.desc(), ChaptarrImport.id.desc())
        .limit(limit)
        .all()
    )
