import asyncio
import os
import platform
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

UPDATES_DIR = Path("/config/updates")
RESTART_SENTINEL = Path("/tmp/libation-restart")

# Cache GitHub response for 1 hour to avoid rate limiting
_release_cache: dict = {"data": None, "fetched_at": 0.0}
_CACHE_TTL = 3600.0


def parse_version(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except Exception:
        return (0,)


def get_installed_version() -> Optional[str]:
    try:
        result = subprocess.run(
            ["libationcli", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout + result.stderr
        match = re.search(r"LibationCli v(\d+\.\d+\.\d+)", output)
        return match.group(1) if match else None
    except Exception:
        return None


def _detect_arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in ("aarch64", "arm64") else "amd64"


async def get_latest_release(force: bool = False) -> dict:
    now = time.monotonic()
    if not force and _release_cache["data"] and (now - _release_cache["fetched_at"]) < _CACHE_TTL:
        return _release_cache["data"]

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(
            "https://api.github.com/repos/rmcrackan/Libation/releases/latest",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "libation-web-ui/1.0",
            },
        )
        r.raise_for_status()
        data = r.json()

    version = data["tag_name"].lstrip("v")
    arch = _detect_arch()
    asset_name = f"Libation.{version}-linux-chardonnay-{arch}.deb"
    asset = next((a for a in data["assets"] if a["name"] == asset_name), None)

    result = {
        "version": version,
        "tag_name": data["tag_name"],
        "published_at": data.get("published_at"),
        "release_notes_url": data.get("html_url"),
        "asset_name": asset_name,
        "download_url": asset["browser_download_url"] if asset else None,
        "asset_found": asset is not None,
    }
    _release_cache["data"] = result
    _release_cache["fetched_at"] = now
    return result


def get_rollback_deb() -> Optional[str]:
    rollback_file = UPDATES_DIR / "rollback"
    if rollback_file.exists():
        name = rollback_file.read_text().strip()
        if name and (UPDATES_DIR / name).exists():
            return name
    return None


async def download_and_stage(download_url: str, asset_name: str) -> None:
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPDATES_DIR / asset_name

    # Preserve current .deb name for rollback before overwriting
    current_file = UPDATES_DIR / "current"
    if current_file.exists():
        current_name = current_file.read_text().strip()
        if current_name and (UPDATES_DIR / current_name).exists():
            (UPDATES_DIR / "rollback").write_text(current_name)

    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", download_url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

    (UPDATES_DIR / "pending").write_text(asset_name)


async def trigger_restart() -> None:
    """Write restart sentinel then SIGTERM uvicorn so the entrypoint loop restarts it."""
    RESTART_SENTINEL.touch()
    await asyncio.sleep(1)
    os.kill(os.getpid(), signal.SIGTERM)
