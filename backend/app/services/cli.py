import asyncio
import fcntl
import os
import pty
import re
import uuid
from typing import Callable, Awaitable, Optional

from ..config import settings

_PENDING_LOGINS: dict[str, dict] = {}  # session_id → {email, locale, master_fd, proc}


def _cmd(*args: str) -> list[str]:
    return [settings.LIBATION_CLI, *args, "--libationFiles", settings.LIBATION_CONFIG]


async def _read_fd_until(fd: int, pattern: str, timeout: float) -> str:
    """Non-blocking read from a PTY master fd until pattern matches or EOF."""
    loop = asyncio.get_event_loop()
    chunks: list[str] = []
    done = asyncio.Event()

    # Make fd non-blocking so add_reader works correctly
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def _on_readable() -> None:
        try:
            data = os.read(fd, 4096)
            if data:
                chunks.append(data.decode("utf-8", errors="replace"))
                if re.search(pattern, "".join(chunks)):
                    loop.remove_reader(fd)
                    done.set()
        except BlockingIOError:
            pass
        except OSError:
            loop.remove_reader(fd)
            done.set()

    loop.add_reader(fd, _on_readable)
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        loop.remove_reader(fd)
        raise
    return "".join(chunks)


async def _drain_fd(fd: int, timeout: float) -> str:
    """Read everything remaining from a PTY master fd until EOF."""
    loop = asyncio.get_event_loop()
    chunks: list[str] = []
    done = asyncio.Event()

    def _on_readable() -> None:
        try:
            data = os.read(fd, 4096)
            if data:
                chunks.append(data.decode("utf-8", errors="replace"))
        except BlockingIOError:
            pass
        except OSError:
            loop.remove_reader(fd)
            done.set()

    loop.add_reader(fd, _on_readable)
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        loop.remove_reader(fd)
    return "".join(chunks)


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
    """Start login-external via a PTY so LibationCli sees a real terminal.

    LibationCli v13 refuses to print the login URL when stdin is not a TTY.
    We allocate a pty pair, attach the slave end to the process, and read
    the URL from the master end without blocking the event loop.
    """
    master_fd, slave_fd = pty.openpty()
    proc = await asyncio.create_subprocess_exec(
        *_cmd("login-external", "-a", email, "-l", locale),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
    )
    os.close(slave_fd)

    try:
        output = await _read_fd_until(
            master_fd,
            pattern=r"https://www\.amazon\.[^\s]+",
            timeout=30,
        )
    except asyncio.TimeoutError:
        proc.kill()
        try:
            os.close(master_fd)
        except OSError:
            pass
        raise RuntimeError("Timed out waiting for LibationCli login URL")

    match = re.search(r"https://www\.amazon\.[^\s]+", output)
    if not match:
        await proc.wait()
        try:
            os.close(master_fd)
        except OSError:
            pass
        raise RuntimeError(
            f"LibationCli exited ({proc.returncode}) without producing a login URL.\n"
            + output
        )

    login_url = match.group(0).rstrip(".")
    session_id = str(uuid.uuid4())
    _PENDING_LOGINS[session_id] = {
        "email": email,
        "locale": locale,
        "master_fd": master_fd,
        "proc": proc,
    }

    async def _expire() -> None:
        await asyncio.sleep(600)
        s = _PENDING_LOGINS.pop(session_id, None)
        if s:
            try:
                s["proc"].kill()
            except Exception:
                pass
            try:
                os.close(s["master_fd"])
            except OSError:
                pass

    asyncio.create_task(_expire())
    return {"session_id": session_id, "login_url": login_url}


async def complete_login(session_id: str, response_url: str) -> str:
    """Write the response URL to LibationCli's PTY stdin to complete login."""
    session = _PENDING_LOGINS.pop(session_id, None)
    if session is None:
        raise KeyError("Login session not found or expired")

    master_fd: int = session["master_fd"]
    proc = session["proc"]

    try:
        os.write(master_fd, f"{response_url}\n".encode())
    except OSError as exc:
        raise RuntimeError(f"Could not send response URL to LibationCli: {exc}")

    output = await _drain_fd(master_fd, timeout=60)
    await proc.wait()

    try:
        os.close(master_fd)
    except OSError:
        pass

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
