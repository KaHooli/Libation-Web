from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from .auth import get_current_user
from ..services.update import (
    download_and_stage,
    get_installed_version,
    get_latest_release,
    get_rollback_deb,
    parse_version,
    trigger_restart,
    UPDATES_DIR,
    RESTART_SENTINEL,
)

router = APIRouter(prefix="/api/updates", tags=["updates"])


def _build_status(installed: str | None, latest: dict) -> dict:
    up_to_date = (
        parse_version(installed) >= parse_version(latest["version"])
        if installed and latest.get("version") else None
    )
    return {
        "installed_version": installed,
        "latest_version": latest.get("version"),
        "up_to_date": up_to_date,
        "asset_found": latest.get("asset_found", False),
        "published_at": latest.get("published_at"),
        "release_notes_url": latest.get("release_notes_url"),
        "has_rollback": get_rollback_deb() is not None,
    }


@router.get("/status")
async def update_status(_=Depends(get_current_user)):
    installed = get_installed_version()
    try:
        latest = await get_latest_release()
        return _build_status(installed, latest)
    except Exception as exc:
        return {
            "installed_version": installed,
            "latest_version": None,
            "up_to_date": None,
            "error": f"Could not reach GitHub: {exc}",
            "has_rollback": get_rollback_deb() is not None,
        }


@router.post("/check")
async def check_for_updates(_=Depends(get_current_user)):
    """Force-refresh the cached GitHub release data."""
    installed = get_installed_version()
    try:
        latest = await get_latest_release(force=True)
        return _build_status(installed, latest)
    except Exception as exc:
        return {
            "installed_version": installed,
            "latest_version": None,
            "up_to_date": None,
            "error": f"Could not reach GitHub: {exc}",
            "has_rollback": get_rollback_deb() is not None,
        }


@router.post("/install")
async def install_update(background_tasks: BackgroundTasks, user=Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(403, "Admin required")

    installed = get_installed_version()
    try:
        latest = await get_latest_release(force=True)
    except Exception as exc:
        raise HTTPException(502, f"Could not reach GitHub: {exc}")

    if not latest["asset_found"]:
        raise HTTPException(400, f"No compatible .deb found ({latest['asset_name']})")

    if installed and parse_version(installed) >= parse_version(latest["version"]):
        raise HTTPException(400, "Already on the latest version")

    await download_and_stage(latest["download_url"], latest["asset_name"])
    background_tasks.add_task(trigger_restart)

    return {"message": f"Update to v{latest['version']} downloaded. Restarting…"}


@router.post("/rollback")
async def rollback_update(background_tasks: BackgroundTasks, user=Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(403, "Admin required")

    previous = get_rollback_deb()
    if not previous:
        raise HTTPException(400, "No previous version available for rollback")

    (UPDATES_DIR / "pending").write_text(previous)
    RESTART_SENTINEL.touch()
    background_tasks.add_task(trigger_restart)

    return {"message": f"Rolling back to {previous.replace('.deb', '')}. Restarting…"}
