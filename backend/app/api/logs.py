from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse

from ..api.users import require_admin
from ..services.logger import log_file_path

router = APIRouter(prefix="/api/logs", tags=["logs"])

_LOG_FILE = log_file_path()
_MAX_LINES = 2000


@router.get("", dependencies=[Depends(require_admin)])
def get_logs(
    lines: int = Query(default=200, ge=1, le=_MAX_LINES),
    level: str = Query(default="all"),
):
    if not _LOG_FILE.exists():
        return JSONResponse({"lines": [], "total": 0, "truncated": False})

    with _LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    level_upper = level.upper()
    if level_upper != "ALL":
        all_lines = [l for l in all_lines if f"[{level_upper}" in l]

    total = len(all_lines)
    tail = all_lines[-lines:]
    return JSONResponse({
        "lines": [l.rstrip("\n") for l in tail],
        "total": total,
        "truncated": total > lines,
    })


@router.get("/download", dependencies=[Depends(require_admin)])
def download_log():
    if not _LOG_FILE.exists():
        return JSONResponse({"detail": "No log file found"}, status_code=404)
    return FileResponse(
        path=str(_LOG_FILE),
        filename="libation-web.log",
        media_type="text/plain",
    )
