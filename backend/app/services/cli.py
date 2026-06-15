import asyncio
import re
import uuid
from typing import Callable, Awaitable, Optional

from ..config import settings

_PENDING_LOGINS: dict[str, asyncio.subprocess.Process] = {}


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
    """Start login-external, capture the login URL, keep process alive for step 2."""
    proc = await asyncio.create_subprocess_exec(
        *_cmd("login-external", "-a", email, "-l", locale),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    login_url: Optional[str] = None
    full_output: list[str] = []

    async def _read_until_url() -> None:
        nonlocal login_url
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode()
            full_output.append(text.strip())
            match = re.search(r"https://www\.amazon\.[^\s]+", text)
            if match:
                login_url = match.group(0).rstrip(".")
                break

    try:
        await asyncio.wait_for(_read_until_url(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Timed out waiting for login URL")

    if not login_url:
        code = await proc.wait()
        raise RuntimeError(
            f"LibationCli exited ({code}) without producing a login URL.\n"
            + "\n".join(full_output)
        )

    session_id = str(uuid.uuid4())
    _PENDING_LOGINS[session_id] = proc

    # Auto-expire session after 10 minutes
    async def _expire():
        await asyncio.sleep(600)
        p = _PENDING_LOGINS.pop(session_id, None)
        if p:
            try:
                p.kill()
            except Exception:
                pass

    asyncio.create_task(_expire())

    return {"session_id": session_id, "login_url": login_url}


async def complete_login(session_id: str, response_url: str) -> str:
    """Feed the response URL to the waiting process and collect output."""
    proc = _PENDING_LOGINS.pop(session_id, None)
    if proc is None:
        raise KeyError("Login session not found or expired")

    proc.stdin.write(f"{response_url}\n".encode())
    await proc.stdin.drain()

    try:
        stdout_remaining, _ = await asyncio.wait_for(
            proc.communicate(), timeout=60
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Timed out completing login")

    output = stdout_remaining.decode()
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
