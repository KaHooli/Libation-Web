#!/usr/bin/env python3
"""The streaming download: progress, cancellation, resume, and disk space.

Phase B2. Run against a real HTTP server in-process — the behaviours that matter
here are protocol behaviours (`Range`, `Content-Range`, a server that ignores a
range request, a connection that dies mid-body), and a mock would only assert
that the code does what it already does.

What these guard:

  * **Cancellation is honoured, and the partial file is kept.** The engine being
    replaced cannot cancel at all: LibationBridge fires the download into a
    detached Task with no handle. A cancel that also threw away 400 MB would be
    a poor replacement.
  * **Resume appends to the right place.** A server that ignores `Range` and
    sends the whole file again is the dangerous case: appending would produce a
    file of plausible size and corrupt content.
  * **Progress is a percentage of the whole file.** On a resumed request
    `Content-Length` is what remains, so the naive reading finishes at 40%%.
  * **Disk space is checked before, not discovered during.** A download that
    fills the volume has already spent the bandwidth, and on a shared mount may
    take the rest of the system with it.

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-download.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="download-test-"))
CONFIG = WORKDIR / "config"
DATA = WORKDIR / "data"
BOOKS = WORKDIR / "audiobooks"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "download-test-only-not-a-real-secret")

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}

#: Deterministic, and big enough to span many chunks so cancellation has
#: somewhere to land.
PAYLOAD = bytes((i * 7 + 13) % 256 for i in range(512 * 1024))


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


# ── A CDN that behaves, and several that do not ──────────────────────────────

class Handler(BaseHTTPRequestHandler):
    """Serves PAYLOAD. The path selects a misbehaviour."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the test output readable
        pass

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's interface
        path = self.path
        self.server.requests.append((path, self.headers.get("Range")))

        if path == "/notfound":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == "/truncated":
            # Claims more than it sends, then closes: a transfer that ends early.
            body = PAYLOAD[: len(PAYLOAD) // 4]
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return

        rng = self.headers.get("Range")
        # /ignoresrange answers a Range request with the whole file, which some
        # CDNs really do. Appending to a partial file here corrupts it.
        if rng and path != "/ignoresrange":
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            body = PAYLOAD[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/nolength":
            # Chunked, so there is no Content-Length to compute progress from.
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(0, len(PAYLOAD), 65536):
                piece = PAYLOAD[i:i + 65536]
                self.wfile.write(f"{len(piece):X}\r\n".encode())
                self.wfile.write(piece + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


# ── Checks ───────────────────────────────────────────────────────────────────

async def test_plain_download(base, tmp) -> None:
    from app.services.potation import download as dl

    seen = []
    dest = tmp / "book.aaxc"
    result = await dl.download_to(f"{base}/ok", dest, on_progress=seen.append)

    assert dest.exists() and dest.read_bytes() == PAYLOAD
    assert result.bytes_written == len(PAYLOAD) and not result.resumed
    assert not dl.part_path(dest).exists(), "the .part file outlived the download"
    assert seen and seen[-1].percent == 100, seen[-1:]
    print("✓ a download completes, renames off .part, and finishes at 100%")


async def test_progress_denominator_is_the_whole_file(base, tmp) -> None:
    """The bug this pins: on a resume, Content-Length is only what remains."""
    from app.services.potation import download as dl

    dest = tmp / "resumed.aaxc"
    part = dl.part_path(dest)
    already = len(PAYLOAD) // 2
    part.write_bytes(PAYLOAD[:already])

    seen = []
    result = await dl.download_to(f"{base}/ok", dest, on_progress=seen.append)

    assert dest.read_bytes() == PAYLOAD, "the resumed file does not match the original"
    assert result.resumed and result.resumed_from == already
    assert seen[-1].bytes_total == len(PAYLOAD), (
        f"progress denominator was {seen[-1].bytes_total}, not the whole "
        f"{len(PAYLOAD)} — a resumed download would appear to finish early"
    )
    assert seen[-1].percent == 100
    print("✓ a resumed download appends correctly and reports the whole file's size")


async def test_server_ignoring_range_restarts_rather_than_corrupting(base, tmp) -> None:
    """The dangerous case: appending a whole file to a partial one."""
    from app.services.potation import download as dl

    dest = tmp / "ignored.aaxc"
    dl.part_path(dest).write_bytes(PAYLOAD[: len(PAYLOAD) // 2])

    result = await dl.download_to(f"{base}/ignoresrange", dest)
    assert dest.read_bytes() == PAYLOAD, (
        "a server that ignored Range produced a corrupt file — the partial "
        "bytes were kept and the whole file appended to them"
    )
    assert result.resumed_from == 0, "restart was not reported as starting from 0"
    print("✓ a server that ignores Range restarts cleanly instead of corrupting")


async def test_cancellation_keeps_the_partial_file(base, tmp) -> None:
    from app.services.potation import download as dl

    dest = tmp / "cancelled.aaxc"
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] >= 1  # cancel at the first poll

    try:
        await dl.download_to(f"{base}/ok", dest, should_cancel=should_cancel)
        raise AssertionError("the download was not cancelled")
    except dl.DownloadCancelled as exc:
        assert "partial file kept" in str(exc)

    assert not dest.exists(), "a cancelled download produced a finished file"
    assert dl.part_path(dest).exists(), (
        "the partial file was discarded — a cancel should not also throw away "
        "everything already transferred"
    )
    print("✓ a cancel stops the transfer, keeps the partial file, and writes no final file")


async def test_a_cancelled_download_resumes(base, tmp) -> None:
    """Cancel then restart is the restart-mid-download recovery path."""
    from app.services.potation import download as dl

    dest = tmp / "cancel-resume.aaxc"
    polls = {"n": 0}
    try:
        await dl.download_to(
            f"{base}/ok", dest,
            should_cancel=lambda: polls.__setitem__("n", polls["n"] + 1) or polls["n"] >= 1,
        )
    except dl.DownloadCancelled:
        pass
    kept = dl.part_path(dest).stat().st_size
    assert kept > 0

    result = await dl.download_to(f"{base}/ok", dest)
    assert dest.read_bytes() == PAYLOAD
    assert result.resumed_from == kept
    print("✓ a cancelled download resumes from exactly where it stopped")


async def test_discard_partial(tmp) -> None:
    from app.services.potation import download as dl

    dest = tmp / "throwaway.aaxc"
    dl.part_path(dest).write_bytes(b"abc")
    dl.discard_partial(dest)
    assert not dl.part_path(dest).exists()
    dl.discard_partial(dest)  # idempotent: a cancel may be processed twice
    print("✓ a partial file can be discarded explicitly, idempotently")


async def test_an_existing_file_is_not_refetched(base, tmp) -> None:
    from app.services.potation import download as dl

    dest = tmp / "already.aaxc"
    dest.write_bytes(PAYLOAD)
    before = len(SERVER.requests)
    result = await dl.download_to(f"{base}/ok", dest)
    assert len(SERVER.requests) == before, (
        "a finished file was downloaded again — that spends quota and bandwidth "
        "to produce a file we already have"
    )
    assert result.bytes_written == len(PAYLOAD)
    print("✓ a file that is already complete is not fetched again")


async def test_network_failures_are_classified(base, tmp) -> None:
    from app.models.potation import ERR_NETWORK, RETRYABLE_ERRORS
    from app.services.potation import download as dl

    for path, label in (("/notfound", "a 404"), ("/truncated", "a short transfer")):
        try:
            await dl.download_to(f"{base}{path}", tmp / f"fail{label[2:5]}.aaxc")
            raise AssertionError(f"{label} did not raise")
        except dl.DownloadError as exc:
            assert exc.error_code == ERR_NETWORK, (label, exc.error_code)
            assert exc.error_code in RETRYABLE_ERRORS, (
                f"{label} was classified unretryable; the queue would give up on "
                "a transient failure"
            )
    print("✓ a 404 and a truncated transfer are both classified as retryable network errors")


async def test_missing_content_length_still_downloads(base, tmp) -> None:
    """A chunked response has no total; progress is unknown, not wrong."""
    from app.services.potation import download as dl

    seen = []
    dest = tmp / "chunked.aaxc"
    await dl.download_to(f"{base}/nolength", dest, on_progress=seen.append)
    assert dest.read_bytes() == PAYLOAD
    assert seen[-1].bytes_total == len(PAYLOAD), seen[-1]
    mid = [p for p in seen[:-1]]
    assert all(p.fraction is None for p in mid), (
        "progress invented a denominator for a response with no Content-Length"
    )
    print("✓ a response with no Content-Length downloads, reporting unknown progress")


def test_disk_space_is_checked_before_downloading(tmp) -> None:
    from app.models.potation import ERR_DISK_FULL
    from app.services.potation import download as dl

    free = shutil.disk_usage(tmp).free
    try:
        dl.check_disk_space(tmp / "huge.aaxc", free * 10)
        raise AssertionError("a download far larger than the disk was allowed")
    except dl.DownloadError as exc:
        assert exc.error_code == ERR_DISK_FULL
        assert "MB" in str(exc), "the message should say how much is needed"

    # And decrypting needs a second copy, so the requirement is not 1x.
    try:
        dl.check_disk_space(tmp / "snug.aaxc", int(free / 1.5))
        raise AssertionError(
            "a file that fits once but not twice was allowed; decrypting writes "
            "a second copy and would fill the volume"
        )
    except dl.DownloadError:
        pass

    dl.check_disk_space(tmp / "small.aaxc", 1024)      # comfortably fits
    dl.check_disk_space(tmp / "unknown.aaxc", None)    # unknown size cannot be refused
    print("✓ disk space is checked up front, allowing for the decrypted copy")


def test_progress_arithmetic() -> None:
    from app.services.potation.download import Progress

    assert Progress(0, 100).percent == 0
    assert Progress(50, 100).percent == 50
    assert Progress(100, 100).percent == 100
    assert Progress(5, None).fraction is None and Progress(5, None).percent is None
    assert Progress(5, 0).fraction is None, "a zero total must not divide"
    # A server that under-reports its own length must not produce 140%.
    assert Progress(140, 100).percent == 100
    print("✓ progress arithmetic handles unknown, zero and over-run totals")


# ── Runner ───────────────────────────────────────────────────────────────────

SERVER = None


async def run_async(base, tmp) -> None:
    await test_plain_download(base, tmp)
    await test_progress_denominator_is_the_whole_file(base, tmp)
    await test_server_ignoring_range_restarts_rather_than_corrupting(base, tmp)
    await test_cancellation_keeps_the_partial_file(base, tmp)
    await test_a_cancelled_download_resumes(base, tmp)
    await test_discard_partial(tmp)
    await test_an_existing_file_is_not_refetched(base, tmp)
    await test_network_failures_are_classified(base, tmp)
    await test_missing_content_length_still_downloads(base, tmp)


def main() -> None:
    global SERVER
    test_progress_arithmetic()

    tmp = WORKDIR / "downloads"
    tmp.mkdir(parents=True, exist_ok=True)
    test_disk_space_is_checked_before_downloading(tmp)

    SERVER, base = serve()
    try:
        asyncio.run(run_async(base, tmp))
    finally:
        SERVER.shutdown()

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll download checks passed.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
