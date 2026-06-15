import asyncio
import re
import uuid
from typing import Callable, Awaitable, Optional

from ..config import settings

_PENDING_LOGINS: dict[str, dict] = {}  # session_id → {email, locale}


def _cmd(*args: str) -> list[str]:
    return [settings.LIBATION_CLI, *args, "--libationFiles", settings.LIBATION_CONFIG]


# ── Accounts ─────────────────────────────────────────────────────────────────

async def list_accounts() -> list[dict]:
    proc = await asyncio.create_subprocess_exec(
        *_cmd("list-accounts", "--bare"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    accounts = []
    for line in stdout.decode().strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 5:
            accounts.append({
                "account_id": parts[0].strip(),
                "name": parts[1].strip(),
                "locale": parts[2].strip(),
                "scan_library": parts[3].strip().lower() == "true",
                "authenticated": parts[4].strip().lower() == "true",
            })
    return accounts


async def start_login(email: str, locale: str) -> dict:
    """Run login-external to get the Audible login URL.

    LibationCli v13+ detects non-TTY stdin and exits after printing the URL,
    expecting a second invocation with --response-url to complete login.
    """
    proc = await asyncio.create_subprocess_exec(
        *_cmd("login-external", "-a", email, "-l", locale),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Timed out waiting for LibationCli login URL")

    output = stdout_bytes.decode()
    match = re.search(r"https://www\.amazon\.[^\s]+", output)
    if not match:
        raise RuntimeError(
            f"LibationCli exited ({proc.returncode}) without producing a login URL.\n"
            + output
        )

    login_url = match.group(0).rstrip(".")
    session_id = str(uuid.uuid4())
    _PENDING_LOGINS[session_id] = {"email": email, "locale": locale}

    # Auto-expire session after 10 minutes
    async def _expire():
        await asyncio.sleep(600)
        _PENDING_LOGINS.pop(session_id, None)

    asyncio.create_task(_expire())

    return {"session_id": session_id, "login_url": login_url}


async def complete_login(session_id: str, response_url: str) -> str:
    """Complete login by re-invoking login-external with --response-url."""
    session = _PENDING_LOGINS.pop(session_id, None)
    if session is None:
        raise KeyError("Login session not found or expired")

    proc = await asyncio.create_subprocess_exec(
        *_cmd("login-external",
              "-a", session["email"],
              "-l", session["locale"],
              "--response-url", response_url),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Timed out completing login")

    output = stdout_bytes.decode()
    if proc.returncode != 0:
        raise RuntimeError(f"Login failed (exit {proc.returncode}).\n{output}")

    return output


# ── Library scan ─────────────────────────────────────────────────────────────

async def run_scan(
    on_line: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *_cmd("scan"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    lines: list[str] = []
    async for raw in proc.stdout:
        text = raw.decode().rstrip()
        lines.append(text)
        if on_line:
            await on_line(text)
    await proc.wait()
    return proc.returncode, "\n".join(lines)


# ── Downloads ─────────────────────────────────────────────────────────────────

async def run_liberate(
    book_ids: Optional[list[str]] = None,
    on_progress: Optional[Callable[[int, str], Awaitable[None]]] = None,
) -> tuple[int, str]:
    extra: list[str] = []
    if book_ids:
        for bid in book_ids:
            extra += ["--id", bid]

    proc = await asyncio.create_subprocess_exec(
        *_cmd("liberate", *extra),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    lines: list[str] = []
    async for raw in proc.stdout:
        text = raw.decode().rstrip()
        lines.append(text)
        if on_progress:
            m = re.search(r"(\d+)\s*%", text)
            if m:
                await on_progress(int(m.group(1)), text)
    await proc.wait()
    return proc.returncode, "\n".join(lines)
