import asyncio
import fcntl
import os
import pty
import re
import time
import uuid
from typing import Callable, Awaitable, Optional

import httpx

from ..config import settings
from .logger import get_logger, log_cli

# ── PTY state (kept for login-external flow) ──────────────────────────────────

_PENDING_LOGINS: dict[str, dict] = {}

# Valid values for `libationcli login-external --locale`. These are AudibleApi
# `Locale.Name` values, NOT ISO country codes. `Localization.Get()` matches on
# Name and silently returns `Locale.Empty` for anything unrecognised, which
# yields a malformed sign-in URL (`https://www.amazon./ap/signin`) instead of an
# error — so validate up front.
VALID_LOCALES = frozenset({
    "us", "uk", "germany", "france", "canada", "australia",
    "japan", "italy", "spain", "india", "brazil",
})


def _cmd(*args: str) -> list[str]:
    return [settings.LIBATION_CLI, *args, "--libationFiles", settings.LIBATION_CONFIG]


def _env() -> dict:
    env = os.environ.copy()
    env["HOME"] = "/home/libation"
    return env


async def _read_fd_until(fd: int, pattern: str, timeout: float) -> str:
    """Non-blocking read from a PTY master fd until pattern matches or EOF."""
    loop = asyncio.get_event_loop()
    chunks: list[str] = []
    done = asyncio.Event()

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


# ── Bridge helpers ────────────────────────────────────────────────────────────

def _bridge(path: str) -> str:
    return f"{settings.BRIDGE_URL}{path}"


_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
_SCAN_TIMEOUT    = httpx.Timeout(connect=5.0, read=600.0, write=10.0, pool=5.0)
_PROGRESS_INTERVAL = 2.0  # seconds between progress polls


# ── Accounts ──────────────────────────────────────────────────────────────────

async def list_accounts() -> list[dict]:
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        resp = await client.get(_bridge("/accounts"))
        resp.raise_for_status()
    accounts = resp.json()
    log_cli("bridge/accounts", 0, f"{len(accounts)} account(s)", time.monotonic() - t0)
    return accounts


async def start_login(email: str, locale: str) -> dict:
    """Start login-external via PTY. Kept in Python — PTY allocation is simpler here."""
    logger = get_logger()
    locale = (locale or "").strip().lower()
    if locale not in VALID_LOCALES:
        logger.error("[login] Rejected unknown locale %r for %s", locale, email)
        raise ValueError(
            f"Unknown Audible marketplace {locale!r}. "
            f"Expected one of: {', '.join(sorted(VALID_LOCALES))}."
        )

    logger.info("[login] Starting login-external for %s (%s)", email, locale)
    t0 = time.monotonic()
    master_fd, slave_fd = pty.openpty()
    proc = await asyncio.create_subprocess_exec(
        *_cmd("login-external", "-a", email, "-l", locale),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=_env(),
    )
    os.close(slave_fd)

    try:
        # The trailing \s matters. _read_fd_until re-tests this pattern after
        # every read, so an unterminated pattern matches the moment the URL
        # straddles a chunk boundary and returns a truncated link. LibationCli
        # emits the URL with Console.WriteLine, so requiring the newline proves
        # the whole URL has arrived.
        output = await _read_fd_until(
            master_fd,
            pattern=r"https://www\.amazon\.\S+\s",
            timeout=30,
        )
    except asyncio.TimeoutError:
        proc.kill()
        try:
            os.close(master_fd)
        except OSError:
            pass
        logger.error("[login] Timed out waiting for login URL for %s (%.1fs)", email, time.monotonic() - t0)
        raise RuntimeError("Timed out waiting for LibationCli login URL")

    match = re.search(r"https://www\.amazon\.[^\s]+", output)
    if not match:
        await proc.wait()
        try:
            os.close(master_fd)
        except OSError:
            pass
        logger.error("[login] login-external exited (%s) without a login URL for %s", proc.returncode, email)
        raise RuntimeError(
            f"LibationCli exited ({proc.returncode}) without producing a login URL.\n" + output
        )

    login_url = match.group(0).rstrip(".")

    # Never hand back a URL that cannot work. Two distinct failure modes:
    problem: str | None = None

    # 1. `Locale.Empty` renders an empty top-level domain, e.g.
    #    "https://www.amazon./ap/signin?...".
    if re.match(r"https://www\.amazon\.(?:[/?]|$)", login_url):
        problem = (
            f"empty Amazon domain — marketplace {locale!r} was not recognised by LibationCli"
        )
    else:
        # 2. A short read. Both parameters are always present in a complete
        #    sign-in URL, so either one missing means the URL was cut off.
        missing = [
            p for p in ("openid.assoc_handle=", "openid.oa2.code_challenge=")
            if p not in login_url
        ]
        if missing:
            problem = f"truncated login URL, missing {' and '.join(missing)}"

    if problem:
        proc.kill()
        try:
            os.close(master_fd)
        except OSError:
            pass
        logger.error("[login] Malformed login URL for locale %r: %s — %s", locale, problem, login_url)
        raise RuntimeError(f"LibationCli produced a malformed login URL: {problem}.")

    logger.info("[login] Login URL generated for %s (%.1fs) — waiting for user response", email, time.monotonic() - t0)
    session_id = str(uuid.uuid4())
    _PENDING_LOGINS[session_id] = {
        "email": email,
        "locale": locale,
        "master_fd": master_fd,
        "proc": proc,
        "t0": t0,
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
    logger = get_logger()
    session = _PENDING_LOGINS.pop(session_id, None)
    if session is None:
        raise KeyError("Login session not found or expired")

    master_fd: int = session["master_fd"]
    proc = session["proc"]
    email: str = session.get("email", "unknown")
    t0: float = session.get("t0", time.monotonic())

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
        logger.error("[login] complete_login FAILED for %s → exit %s (%.1fs)\n%s",
                     email, proc.returncode, time.monotonic() - t0, output)
        raise RuntimeError(f"Login failed (exit {proc.returncode}).\n{output}")

    logger.info("[login] complete_login OK for %s (%.1fs)", email, time.monotonic() - t0)
    return output


# ── Library scan ──────────────────────────────────────────────────────────────

async def run_scan(
    on_line: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[int, str]:
    logger = get_logger()
    logger.info("[scan] Starting library scan via bridge")
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=_SCAN_TIMEOUT) as client:
        resp = await client.post(_bridge("/scan"))
        resp.raise_for_status()
    data = resp.json()
    exit_code: int = data.get("exit_code", 0)
    output: str = data.get("output", "")
    log_cli("bridge/scan", exit_code, output, time.monotonic() - t0)
    if on_line:
        for line in output.splitlines():
            await on_line(line)
    return exit_code, output


# ── Downloads ─────────────────────────────────────────────────────────────────

async def run_liberate(
    book_ids: Optional[list[str]] = None,
    on_progress: Optional[Callable[[int, str], Awaitable[None]]] = None,
    force: bool = True,
) -> tuple[int, str]:
    logger = get_logger()
    ids_label = " ".join(book_ids) if book_ids else "(all)"
    logger.info("[liberate] Starting liberate for %s", ids_label)
    t0 = time.monotonic()

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        if not book_ids:
            # Bulk: fire and forget via /download-all
            resp = await client.post(_bridge("/download-all"))
            if resp.status_code not in (200, 202, 409):
                body = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                raise RuntimeError(body.get("error") or resp.text)
            log_cli("bridge/liberate all", 0, "started", time.monotonic() - t0)
            return 0, "Bulk download started"

        # Start download(s) — 409 means already running, which is fine
        for asin in book_ids:
            resp = await client.post(_bridge(f"/download/{asin}"))
            if resp.status_code not in (200, 202, 409):
                body = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                raise RuntimeError(body.get("error") or resp.text)

        if len(book_ids) == 1:
            # Single book: poll /progress/{asin} until complete
            asin = book_ids[0]
            last_pct = 0
            while True:
                await asyncio.sleep(_PROGRESS_INTERVAL)
                prog_resp = await client.get(_bridge(f"/progress/{asin}"))
                if prog_resp.status_code == 404:
                    # Should not happen; treat as error
                    return 1, "Progress record disappeared"
                prog = prog_resp.json()
                pct: int = prog.get("progress", 0)
                status: str = prog.get("status", "running")
                output: str = prog.get("output", "")

                if on_progress and pct != last_pct:
                    await on_progress(pct, output)
                    last_pct = pct

                if status in ("complete", "error"):
                    exit_code = 0 if status == "complete" else 1
                    if exit_code == 0 and on_progress and last_pct < 100:
                        await on_progress(100, output)
                    log_cli(f"bridge/liberate {asin}", exit_code, output, time.monotonic() - t0)
                    return exit_code, output

        else:
            # Multiple books: poll all until none are still running
            remaining = set(book_ids)
            while remaining:
                await asyncio.sleep(_PROGRESS_INTERVAL)
                done: set[str] = set()
                for asin in remaining:
                    pg = await client.get(_bridge(f"/progress/{asin}"))
                    if pg.status_code == 200 and pg.json().get("status") in ("complete", "error"):
                        done.add(asin)
                remaining -= done

            exit_code = 0
            output_parts: list[str] = []
            for asin in book_ids:
                pg = await client.get(_bridge(f"/progress/{asin}"))
                if pg.status_code == 200:
                    d = pg.json()
                    if d.get("status") == "error":
                        exit_code = 1
                    if d.get("output"):
                        output_parts.append(d["output"])
            output = "\n".join(output_parts)
            log_cli(f"bridge/liberate {ids_label}", exit_code, output, time.monotonic() - t0)
            return exit_code, output

    return 0, ""  # unreachable
