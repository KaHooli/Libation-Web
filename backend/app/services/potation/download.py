"""Fetch one licensed file from Audible's CDN, cancellably and resumably.

This is the stage the current engine cannot do anything with. LibationBridge
fires `ProcessAsync` into a detached `Task` and keeps no handle, so there is
nothing to cancel, nothing to pause, and no progress beyond one conflated
percentage covering both download and decrypt. Cancellation is therefore
threaded through this module from the start rather than added later: retrofitting
it onto a fire-and-forget coroutine is precisely the mistake being replaced.

Three things shape the design.

**The whole file is downloaded before ffmpeg sees it.** AAXC is MP4, ffmpeg
needs the `moov` atom and seeks around the file, and Audible's CDN files are not
reliably faststart — Libation has a `MoveMoovToBeginning` option for exactly this
reason. So this is not a streaming pipe into a decoder; it is a complete file on
disk, which is also what makes resume possible.

**Cancellation and progress are polled on a clock, not per chunk.** The caller's
`should_cancel` will usually read a database column and `on_progress` will
usually write one; at 64 KiB a chunk a 500 MB book would do that eight thousand
times. Both are called at most once per `POLL_INTERVAL`, which lets the caller
be honest about doing real work in them.

**A partial file is kept, not deleted.** It is written to `<name>.part` and only
renamed on success, so a partial download can never be mistaken for a finished
one, and an interrupted transfer resumes with an HTTP `Range` request instead of
starting again. A cancelled *job* cleans up after itself; a crashed process
leaves the part file for the next attempt.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from ...models.potation import (
    ERR_DISK_FULL, ERR_NETWORK, ERR_UNKNOWN,
)
from ..logger import get_logger

#: Big enough that the syscall overhead is irrelevant, small enough that a
#: cancel is honoured promptly on a slow connection.
CHUNK_BYTES = 64 * 1024

#: How often `should_cancel` and `on_progress` may be called. The caller does
#: real work in these, so they are rate-limited rather than per-chunk.
POLL_INTERVAL = 0.5

#: Decrypting writes a second copy alongside the download, so the transient
#: requirement is roughly twice the file, plus room to not fill the disk.
SPACE_FACTOR = 2.2
SPACE_FLOOR_BYTES = 64 * 1024 * 1024

_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=60.0, pool=15.0)


class DownloadCancelled(Exception):
    """The caller asked for this download to stop. Not a failure."""


class DownloadError(Exception):
    """A download failed. `error_code` classifies it for retry."""

    def __init__(self, message: str, error_code: str = ERR_UNKNOWN):
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class Progress:
    bytes_done: int
    bytes_total: Optional[int]

    @property
    def fraction(self) -> Optional[float]:
        if not self.bytes_total:
            return None
        return min(1.0, self.bytes_done / self.bytes_total)

    @property
    def percent(self) -> Optional[int]:
        f = self.fraction
        return None if f is None else int(f * 100)


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    bytes_written: int
    resumed_from: int = 0

    @property
    def resumed(self) -> bool:
        return self.resumed_from > 0


def part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def check_disk_space(dest: Path, expected_bytes: Optional[int]) -> None:
    """Refuse before downloading rather than filling the disk and failing late.

    A download that runs out of space part-way has already spent the bandwidth
    and, on a shared volume, may have taken the rest of the system down with it.
    """
    if not expected_bytes:
        return
    needed = int(expected_bytes * SPACE_FACTOR) + SPACE_FLOOR_BYTES
    target = dest.parent
    try:
        free = shutil.disk_usage(target).free
    except OSError:
        # Cannot tell — proceed rather than refusing a download over a stat that
        # may fail for reasons unrelated to space.
        return
    if free < needed:
        raise DownloadError(
            f"Not enough free space in {target}: need about "
            f"{needed // (1024 * 1024)} MB, {free // (1024 * 1024)} MB available.",
            ERR_DISK_FULL,
        )


def _resume_offset(part: Path, resume: bool) -> int:
    if not resume or not part.exists():
        return 0
    try:
        return part.stat().st_size
    except OSError:
        return 0


async def download_to(
    url: str,
    dest: Path,
    *,
    on_progress: Optional[Callable[[Progress], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    resume: bool = True,
    client: Optional[httpx.AsyncClient] = None,
    expected_bytes: Optional[int] = None,
) -> DownloadResult:
    """Download `url` to `dest`, resuming and cancelling as asked.

    `should_cancel` is polled at most every `POLL_INTERVAL` seconds; returning
    True raises `DownloadCancelled` and leaves the part file in place, so the
    caller decides whether a cancel means "stop for now" or "throw it away".
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(dest)

    if dest.exists():
        # Already finished. Re-downloading would spend quota and bandwidth to
        # produce a file we have.
        return DownloadResult(path=dest, bytes_written=dest.stat().st_size)

    check_disk_space(dest, expected_bytes)

    start_at = _resume_offset(part, resume)
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)

    try:
        return await _stream(
            client, url, dest, part, start_at,
            on_progress=on_progress, should_cancel=should_cancel,
        )
    finally:
        if owns_client:
            await client.aclose()


async def _stream(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    part: Path,
    start_at: int,
    *,
    on_progress: Optional[Callable[[Progress], None]],
    should_cancel: Optional[Callable[[], bool]],
) -> DownloadResult:
    logger = get_logger()
    headers = {"Range": f"bytes={start_at}-"} if start_at else {}

    try:
        async with client.stream("GET", url, headers=headers) as response:
            if start_at and response.status_code == 200:
                # The server ignored the Range header and is sending the whole
                # file. Start over rather than appending to what we have, which
                # would produce a corrupt file with a plausible size.
                logger.info("[potation-download] range ignored; restarting from 0")
                start_at = 0
            elif start_at and response.status_code != 206:
                raise DownloadError(
                    f"Resume rejected with HTTP {response.status_code}.", ERR_NETWORK
                )

            if response.status_code >= 400:
                raise DownloadError(
                    f"CDN returned HTTP {response.status_code}.", ERR_NETWORK
                )

            total = _total_bytes(response, start_at)
            check_disk_space(dest, total)

            mode = "ab" if start_at else "wb"
            done = start_at
            last_poll = 0.0

            with open(part, mode) as handle:
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    try:
                        handle.write(chunk)
                    except OSError as exc:
                        raise DownloadError(
                            f"Writing to {part} failed: {exc}", _classify_os_error(exc)
                        ) from exc
                    done += len(chunk)

                    now = time.monotonic()
                    if now - last_poll >= POLL_INTERVAL:
                        last_poll = now
                        if should_cancel is not None and should_cancel():
                            handle.flush()
                            raise DownloadCancelled(
                                f"Cancelled after {done} bytes; partial file kept."
                            )
                        if on_progress is not None:
                            on_progress(Progress(done, total))

            if total and done < total:
                raise DownloadError(
                    f"Transfer ended early: {done} of {total} bytes.", ERR_NETWORK
                )
    except httpx.HTTPError as exc:
        raise DownloadError(f"Network error: {exc}", ERR_NETWORK) from exc

    os.replace(part, dest)
    if on_progress is not None:
        on_progress(Progress(done, total or done))
    return DownloadResult(path=dest, bytes_written=done, resumed_from=start_at)


def _total_bytes(response: httpx.Response, start_at: int) -> Optional[int]:
    """Full size of the file, not of this response.

    On a resumed request `Content-Length` is what remains, so it has to be read
    from `Content-Range` or added to the offset — otherwise progress reports a
    percentage of the wrong denominator and appears to finish early.
    """
    content_range = response.headers.get("Content-Range")
    if content_range and "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    length = response.headers.get("Content-Length")
    if length and length.isdigit():
        return int(length) + start_at
    return None


def _classify_os_error(exc: OSError) -> str:
    import errno
    if exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return ERR_DISK_FULL
    return ERR_UNKNOWN


def discard_partial(dest: Path) -> None:
    """Throw away a kept part file, for a cancel that means "not ever"."""
    try:
        part_path(Path(dest)).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        get_logger().warning("[potation-download] could not remove part file: %s", exc)
