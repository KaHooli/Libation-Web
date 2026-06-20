import re
import subprocess

from fastapi import APIRouter, Depends

from .auth import get_current_user

router = APIRouter(prefix="/api/updates", tags=["updates"])


@router.get("/version")
def get_cli_version(_=Depends(get_current_user)):
    """Return the installed LibationCLI version. Read-only — updating is done via image rebuild."""
    try:
        result = subprocess.run(
            ["libationcli", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        output = (result.stderr or "") + (result.stdout or "")
        match = re.search(r"(\d+\.\d+\.\d+)", output)
        return {"cli_version": match.group(1) if match else None}
    except Exception:
        return {"cli_version": None}
