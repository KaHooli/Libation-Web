"""The persistent download queue — where the blocked features come from.

Cancel, pause, resume, per-book status during a bulk run, retry that knows what
it is retrying, and progress that survives a restart are all one missing thing
today: a job row. LibationBridge holds progress in a `ConcurrentDictionary` and
fires the work into a detached `Task`, so a restart loses everything and there
is no handle to cancel. Here every job is a `download_jobs` row, and the row is
the truth.

**The pipeline is injected.** `run_job` takes the function that actually does
the work, defaulting to the real one. That seam is what lets the queue's own
behaviour — claiming, retry classification, cancellation, resume, concurrency —
be tested exhaustively without an Audible account, which is the majority of what
can go wrong here.

Three details that are easy to get wrong:

**Claiming is a conditional UPDATE, not a read-then-write.** `UPDATE ... WHERE
id = ? AND state = 'queued'` and check the row count. The entrypoint runs one
uvicorn worker today so a race should not arise — but "should not" is how two
workers end up downloading the same book twice, and the conditional form costs
nothing.

**Cancellation is read past SQLAlchemy's identity map.** The worker holds a
session for the length of a download; the API writes `cancel_requested` on
another. The hazard is *not* transaction isolation — a raw `SELECT` sees the
committed value on both backends — it is the identity map: an ORM re-query hands
back the instance already loaded in this session, with the value it was loaded
with. The cancel would be accepted by the API, stored in the database, and
silently ignored for the next forty minutes. `cancel_requested()` therefore
reads via raw SQL *and* rolls back first, which expires the identity map so the
whole session agrees. Either alone suffices today; together the function stays
correct if someone later rewrites the read in ORM terms, which is the likely
tidy-up.

**A crash leaves jobs mid-flight, and they are resumable.** `reset_orphans()`
puts anything left in a running state back to `queued` at startup; the download
stage then picks up from its `.part` file. Marking them failed instead would
throw away a mostly-finished transfer and spend the quota again.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...models.potation import (
    JOB_ACTIVE_STATES, JOB_CANCELLED, JOB_COMPLETE, JOB_DECRYPTING,
    JOB_DOWNLOADING, JOB_ERROR, JOB_LICENSING, JOB_QUEUED,
    ERR_UNKNOWN, RETRYABLE_ERRORS,
    DownloadJob,
)
from ..logger import get_logger

#: How many jobs run at once. One by default: Audible is not a CDN to hammer,
#: and a single book already saturates most connections.
DEFAULT_CONCURRENCY = 1

#: How long the worker sleeps when there is nothing queued.
IDLE_INTERVAL = 2.0


class QueueError(Exception):
    pass


@dataclass
class JobOutcome:
    """What one pipeline run produced. `path` is the finished audiobook."""

    path: Optional[Path] = None
    bytes_written: int = 0
    account_id: Optional[str] = None
    is_audible_plus: Optional[bool] = None


#: A pipeline takes the job and two callbacks and returns what it produced.
Pipeline = Callable[..., Awaitable[JobOutcome]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Enqueue ──────────────────────────────────────────────────────────────────

def active_job_for(db: Session, book_asin: str) -> Optional[DownloadJob]:
    return (
        db.query(DownloadJob)
        .filter(DownloadJob.book_asin == book_asin,
                DownloadJob.state.in_(tuple(JOB_ACTIVE_STATES)))
        .order_by(DownloadJob.id.desc())
        .first()
    )


def enqueue(
    db: Session,
    book_asin: str,
    *,
    user_id: Optional[int] = None,
    book_title: Optional[str] = None,
    priority: int = 100,
    max_retries: int = 3,
) -> DownloadJob:
    """Queue one book, or return the job already working on it.

    Deduplicating here rather than at the caller matters because there are
    several callers — a person clicking download, the post-scan sweep, and a
    bulk selection can all name the same book within a second of each other.
    """
    existing = active_job_for(db, book_asin)
    if existing is not None:
        return existing

    job = DownloadJob(
        book_asin=book_asin,
        book_title=book_title,
        user_id=user_id,
        state=JOB_QUEUED,
        priority=priority,
        max_retries=max_retries,
    )
    db.add(job)
    db.commit()
    return job


def enqueue_many(
    db: Session,
    book_asins: Sequence[str],
    *,
    user_id: Optional[int] = None,
    confirmed: bool = False,
    check_valve: bool = True,
) -> list[DownloadJob]:
    """Queue a batch, through the bulk-enqueue valve.

    The valve (`reconcile.check_bulk_enqueue`) refuses anything before a clean
    reconciliation pass and anything large without confirmation. It is checked
    on the *new* work, not the whole list: re-submitting a selection whose books
    are already queued should not be refused for its size.
    """
    from .reconcile import check_bulk_enqueue

    wanted = [a for a in dict.fromkeys(book_asins) if a]
    fresh = [a for a in wanted if active_job_for(db, a) is None]

    if check_valve and fresh:
        check_bulk_enqueue(db, len(fresh), confirmed=confirmed)

    return [enqueue(db, asin, user_id=user_id) for asin in wanted]


# ── Claiming and state ───────────────────────────────────────────────────────

def claim_next(db: Session) -> Optional[DownloadJob]:
    """Take the next queued job, atomically.

    A conditional UPDATE rather than read-then-write: with one uvicorn worker a
    race should not happen, but "should not" is how the same book gets
    downloaded twice, and the conditional form is free.
    """
    while True:
        candidate = (
            db.query(DownloadJob)
            .filter(DownloadJob.state == JOB_QUEUED)
            .order_by(DownloadJob.priority.asc(), DownloadJob.id.asc())
            .first()
        )
        if candidate is None:
            return None

        result = db.connection().execute(
            text(
                "UPDATE download_jobs SET state = :running, started_at = :now "
                "WHERE id = :id AND state = :queued"
            ),
            {"running": JOB_LICENSING, "now": _utcnow(),
             "id": candidate.id, "queued": JOB_QUEUED},
        )
        db.commit()
        if result.rowcount == 1:
            db.refresh(candidate)
            return candidate
        # Somebody else took it; look again rather than returning nothing.


def reset_orphans(db: Session) -> int:
    """Put jobs left mid-flight by a crash back in the queue.

    Back to `queued`, not `error`: the download stage resumes from its `.part`
    file, so failing them would throw away a mostly-finished transfer and spend
    the daily allowance a second time to redo it.
    """
    running = tuple(JOB_ACTIVE_STATES - {JOB_QUEUED})
    count = (
        db.query(DownloadJob)
        .filter(DownloadJob.state.in_(running))
        .update({DownloadJob.state: JOB_QUEUED, DownloadJob.stage_progress: 0},
                synchronize_session=False)
    )
    db.commit()
    if count:
        get_logger().info("[potation-queue] Requeued %d job(s) after restart", count)
    return int(count or 0)


def request_cancel(db: Session, job_id: int) -> bool:
    """Ask a job to stop. Returns False when there is nothing to stop.

    A queued job is cancelled outright — no worker will ever look at the flag —
    while a running one is flagged and stops at its next poll.
    """
    job = db.query(DownloadJob).filter(DownloadJob.id == job_id).first()
    if job is None or job.state not in JOB_ACTIVE_STATES:
        return False

    job.cancel_requested = True
    if job.state == JOB_QUEUED:
        job.state = JOB_CANCELLED
        job.completed_at = _utcnow()
    db.commit()
    return True


def cancel_requested(db: Session, job_id: int) -> bool:
    """Whether a cancel has been asked for, read past the identity map.

    The worker holds this session for the length of a download while the API
    writes the flag on another. A raw `SELECT` sees the committed value on both
    backends; what does *not* is an ORM re-query, which returns the instance
    already in this session's identity map with the value it was loaded with.
    The rollback expires that map, so the whole session — not just this
    statement — sees the truth. Belt and braces on purpose: rewriting this read
    in ORM terms is the obvious tidy-up, and on its own it would silently
    reintroduce a cancel that is accepted and then ignored.
    """
    db.rollback()
    value = db.connection().execute(
        text("SELECT cancel_requested FROM download_jobs WHERE id = :id"),
        {"id": job_id},
    ).scalar()
    return bool(value)


def set_state(
    db: Session, job: DownloadJob, state: str, *, progress: Optional[int] = None
) -> None:
    job.state = state
    if progress is not None:
        job.stage_progress = progress
    if state in (JOB_COMPLETE, JOB_ERROR, JOB_CANCELLED):
        job.completed_at = _utcnow()
    db.commit()


def record_progress(db: Session, job: DownloadJob, percent: int,
                    *, bytes_done: Optional[int] = None,
                    bytes_total: Optional[int] = None) -> None:
    job.stage_progress = max(0, min(100, int(percent)))
    if bytes_done is not None:
        job.bytes_done = bytes_done
    if bytes_total is not None:
        job.bytes_total = bytes_total
    db.commit()


# ── Running one job ──────────────────────────────────────────────────────────

def _classify(exc: Exception) -> tuple[str, str]:
    """(error_code, message) for anything a pipeline stage can raise."""
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code:
        return code, str(exc)
    return ERR_UNKNOWN, str(exc) or exc.__class__.__name__


async def run_job(
    db: Session,
    job: DownloadJob,
    *,
    pipeline: Optional[Pipeline] = None,
) -> str:
    """Work one claimed job to a terminal state. Returns that state.

    Never raises for an ordinary failure: a job that fails is a row with an
    `error_code`, not an exception escaping into the worker loop and taking the
    queue with it.
    """
    from .quota import record_download

    logger = get_logger()
    runner = pipeline or run_default_pipeline

    def should_cancel() -> bool:
        return cancel_requested(db, job.id)

    def on_progress(percent: int, **kw) -> None:
        record_progress(db, job, percent, **kw)

    if should_cancel():
        set_state(db, job, JOB_CANCELLED)
        return JOB_CANCELLED

    try:
        outcome = await runner(db, job, should_cancel=should_cancel, on_progress=on_progress)
    except Exception as exc:
        if _is_cancellation(exc):
            set_state(db, job, JOB_CANCELLED)
            logger.info("[potation-queue] Job %s cancelled", job.id)
            return JOB_CANCELLED

        code, message = _classify(exc)
        job.error_code = code
        job.error_message = message[:2000]

        if code in RETRYABLE_ERRORS and job.retry_count < job.max_retries:
            job.retry_count += 1
            job.state = JOB_QUEUED
            job.stage_progress = 0
            db.commit()
            logger.warning(
                "[potation-queue] Job %s failed (%s), retry %d/%d",
                job.id, code, job.retry_count, job.max_retries,
            )
            return JOB_QUEUED

        set_state(db, job, JOB_ERROR)
        logger.error("[potation-queue] Job %s failed permanently (%s)", job.id, code)
        return JOB_ERROR

    # Recorded on success, limit configured or not — see services/potation/quota.
    record_download(
        db,
        book_asin=job.book_asin,
        account_id=outcome.account_id,
        size_bytes=outcome.bytes_written or None,
        is_audible_plus=outcome.is_audible_plus,
    )
    job.error_code = None
    job.error_message = None
    set_state(db, job, JOB_COMPLETE, progress=100)
    return JOB_COMPLETE


def _is_cancellation(exc: Exception) -> bool:
    from .decrypt import DecryptCancelled
    from .download import DownloadCancelled

    return isinstance(exc, (DownloadCancelled, DecryptCancelled, asyncio.CancelledError))


# ── The real pipeline ────────────────────────────────────────────────────────

async def run_default_pipeline(
    db: Session,
    job: DownloadJob,
    *,
    should_cancel: Callable[[], bool],
    on_progress: Callable[..., None],
) -> JobOutcome:
    """Licence → download → decrypt, for one book.

    Kept deliberately thin: the branching that can go wrong lives in `drm.py`,
    `download.py` and `decrypt.py`, each tested on its own. What this adds is the
    order and the state transitions, and those are what `run_job`'s checks cover.
    """
    from ...config import settings
    from .client import active_accounts, get_account
    from .decrypt import safe_filename, transcode
    from .download import download_to
    from .drm import plan_delivery
    from .license import license_for_download
    from ...models.potation import Book

    book = db.query(Book).filter(Book.asin == job.book_asin).first()
    if book is None:
        raise QueueError(f"{job.book_asin} is not in the library.")

    account = (
        get_account(db, book.account_id) if book.account_id
        else next(iter(active_accounts(db)), None)
    )
    if account is None:
        raise QueueError(f"No usable Audible account for {job.book_asin}.")

    set_state(db, job, JOB_LICENSING, progress=0)
    info = license_for_download(db, account, job.book_asin)
    plan = plan_delivery(info.drm_type, key=info.key, iv=info.iv)
    if not plan.usable:
        from ...models.potation import ERR_DRM_UNSUPPORTED
        raise _coded(QueueError(plan.reason), ERR_DRM_UNSUPPORTED)

    staging = Path(settings.AUDIOBOOKS_DIR) / ".potation-staging"
    source = staging / f"{job.book_asin}.download"

    set_state(db, job, JOB_DOWNLOADING, progress=0)
    result = await download_to(
        info.download_url, source,
        on_progress=lambda p: on_progress(
            p.percent or 0, bytes_done=p.bytes_done, bytes_total=p.bytes_total
        ),
        should_cancel=should_cancel,
    )

    set_state(db, job, JOB_DECRYPTING, progress=0)
    dest = Path(settings.AUDIOBOOKS_DIR) / safe_filename(
        book.title or job.book_asin, job.book_asin
    )
    await transcode(
        source, dest, plan,
        _metadata_for(book),
        duration_ms=(book.length_minutes or 0) * 60_000 or None,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    source.unlink(missing_ok=True)

    return JobOutcome(
        path=dest,
        bytes_written=result.bytes_written,
        account_id=account.account_id,
        is_audible_plus=bool(getattr(book, "is_audible_plus", False)),
    )


def _metadata_for(book):
    from .decrypt import BookMetadata

    return BookMetadata(
        asin=book.asin,
        title=book.title or book.asin,
        subtitle=getattr(book, "subtitle", None),
        authors=list(getattr(book, "authors", None) or []),
        narrators=list(getattr(book, "narrators", None) or []),
        series_name=getattr(book, "series_name", None),
        series_sequence=getattr(book, "series_sequence", None),
        publisher=getattr(book, "publisher", None),
        description=getattr(book, "description", None),
    )


def _coded(exc: Exception, code: str) -> Exception:
    exc.error_code = code  # type: ignore[attr-defined]
    return exc


# ── The worker loop ──────────────────────────────────────────────────────────

async def work_forever(
    session_factory,
    *,
    stop: Optional[asyncio.Event] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    pipeline: Optional[Pipeline] = None,
    idle_interval: float = IDLE_INTERVAL,
) -> None:
    """Drain the queue until `stop` is set.

    Each in-flight job gets its own session: they commit independently, and one
    long download must not hold a transaction open across another job's writes.
    """
    stop = stop or asyncio.Event()
    running: set[asyncio.Task] = set()

    async def run_one() -> None:
        db = session_factory()
        try:
            job = claim_next(db)
            if job is None:
                return
            await run_job(db, job, pipeline=pipeline)
        finally:
            db.close()

    while not stop.is_set():
        while len(running) < concurrency and not stop.is_set():
            probe = session_factory()
            try:
                has_work = (
                    probe.query(DownloadJob)
                    .filter(DownloadJob.state == JOB_QUEUED).first() is not None
                )
            finally:
                probe.close()
            if not has_work:
                break
            task = asyncio.create_task(run_one())
            running.add(task)
            task.add_done_callback(running.discard)

        if not running:
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle_interval)
            except asyncio.TimeoutError:
                pass
            continue

        await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)

    if running:
        await asyncio.gather(*running, return_exceptions=True)
