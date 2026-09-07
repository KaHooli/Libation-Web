#!/usr/bin/env python3
"""The persistent download queue: claiming, retry, cancel, resume, concurrency.

Phase B5, the last of Phase B. The pipeline is injected here, so everything the
queue itself decides gets exercised without an Audible account — which is the
majority of what can go wrong. The stages it drives are each tested on their own
(`test-drm.py`, `test-download.py`, `test-decrypt.py`).

The failures these guard, in rough order of how bad they are:

  * **A cancel that is accepted and then ignored.** The worker holds a session
    for the length of a download; the API writes `cancel_requested` on another.
    Inside an open transaction the worker keeps its snapshot and never sees the
    flag — so the API returns 200, the row says cancelled, and the download runs
    for another forty minutes. There is a check that writes the flag from a
    genuinely different session.
  * **A crash marking work failed instead of resumable.** Jobs left mid-flight
    go back to `queued`, so the download resumes from its `.part` file. Failing
    them throws away a mostly-finished transfer and spends the daily allowance
    again to redo it.
  * **Retrying what will never work.** `license_denied` and `drm_unsupported`
    are not transient; retrying them three times spends quota to fail three
    times. Only `RETRYABLE_ERRORS` come back.
  * **The same book downloaded twice.** Several callers can name one book within
    a second — a person clicking, the post-scan sweep, a bulk selection.

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-queue.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="queue-test-"))
CONFIG = WORKDIR / "config"
DATA = WORKDIR / "data"
BOOKS = WORKDIR / "audiobooks"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "queue-test-only-not-a-real-secret")

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}

DB_PATH = DATA / "queue.db"
Session = None


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


def make_sessionmaker():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import chaptarr, download, potation, user
    assert all((chaptarr, download, potation, user))

    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS system_settings "
            "(key TEXT PRIMARY KEY, value TEXT DEFAULT '')"
        ))
    return sessionmaker(bind=engine)


def wipe(db) -> None:
    from app.models.potation import DownloadJob, DownloadQuotaEntry
    db.query(DownloadJob).delete()
    db.query(DownloadQuotaEntry).delete()
    db.commit()


def ok_pipeline(bytes_written=1234):
    from app.services.potation.queue import JobOutcome

    async def _run(db, job, *, should_cancel, on_progress):
        on_progress(50)
        return JobOutcome(bytes_written=bytes_written, account_id="ACCT")
    return _run


def failing_pipeline(exc):
    async def _run(db, job, *, should_cancel, on_progress):
        raise exc
    return _run


# ── Enqueue ──────────────────────────────────────────────────────────────────

def test_enqueue_deduplicates(db) -> None:
    from app.services.potation import queue

    wipe(db)
    first = queue.enqueue(db, "B0DEDUPE01", book_title="A Book")
    again = queue.enqueue(db, "B0DEDUPE01")
    assert again.id == first.id, (
        "the same book was queued twice — a click, the post-scan sweep and a "
        "bulk selection can all name it within a second"
    )

    # Once it is finished, the book can be queued again deliberately.
    queue.set_state(db, first, "complete")
    third = queue.enqueue(db, "B0DEDUPE01")
    assert third.id != first.id, "a finished book could not be re-queued"
    print("✓ an in-flight book is not queued twice; a finished one can be re-queued")


def test_bulk_enqueue_goes_through_the_valve(db) -> None:
    """The valve refuses before reconciliation; the queue must not bypass it."""
    from app.services.potation import queue
    from app.services.potation.reconcile import BulkEnqueueRefused

    wipe(db)
    try:
        queue.enqueue_many(db, [f"B0BULK{i:05d}" for i in range(30)])
        raise AssertionError("a bulk enqueue skipped the reconciliation gate")
    except BulkEnqueueRefused as exc:
        assert "reconcil" in str(exc).lower(), str(exc)

    # With the valve switched off for a caller that has its own guard, it works.
    jobs = queue.enqueue_many(db, ["B0OK000001", "B0OK000002"], check_valve=False)
    assert len(jobs) == 2
    print("✓ a bulk enqueue is refused before reconciliation, and the valve is not bypassed")


def test_the_valve_sees_only_new_work(db) -> None:
    """Re-submitting an already-queued selection must not be refused for size."""
    from app.services.potation import queue

    wipe(db)
    asins = [f"B0AGAIN{i:04d}" for i in range(30)]
    queue.enqueue_many(db, asins, check_valve=False)

    # Everything is already queued, so there is no new work — no valve check.
    again = queue.enqueue_many(db, asins)
    assert len(again) == 30
    assert len({j.id for j in again}) == 30, "re-submitting created duplicate jobs"
    print("✓ re-submitting an already-queued selection is not refused for its size")


# ── Claiming ─────────────────────────────────────────────────────────────────

def test_claim_takes_one_job_at_a_time_in_priority_order(db) -> None:
    from app.services.potation import queue

    wipe(db)
    queue.enqueue(db, "B0NORMAL01", priority=100)
    queue.enqueue(db, "B0URGENT01", priority=10)
    queue.enqueue(db, "B0LATER001", priority=200)

    order = []
    while True:
        job = queue.claim_next(db)
        if job is None:
            break
        order.append(job.book_asin)
        queue.set_state(db, job, "complete")

    assert order == ["B0URGENT01", "B0NORMAL01", "B0LATER001"], order
    assert queue.claim_next(db) is None, "an empty queue handed back a job"
    print("✓ jobs are claimed one at a time, lowest priority number first")


def test_a_claimed_job_is_not_claimable_again(db) -> None:
    """The conditional UPDATE: two workers must not take the same book."""
    from sqlalchemy import text
    from app.services.potation import queue

    wipe(db)
    queue.enqueue(db, "B0ONCE0001")
    first = queue.claim_next(db)
    assert first is not None

    state = db.connection().execute(
        text("SELECT state FROM download_jobs WHERE id = :id"), {"id": first.id}
    ).scalar()
    assert state == "licensing", state
    assert queue.claim_next(db) is None, (
        "a job already claimed was handed out again — two workers would "
        "download the same book twice"
    )
    print("✓ a claimed job cannot be claimed again")


# ── The failure that hides: a cancel nobody sees ─────────────────────────────

def test_a_cancel_written_by_another_session_is_seen(db) -> None:
    """The worker's session must see a cancel the API wrote on a different one.

    The hazard is SQLAlchemy's identity map, not transaction isolation: a raw
    SELECT sees the committed value on both backends, but an ORM re-query hands
    back the instance already loaded in this session, with the value it was
    loaded with. `cancel_requested` rolls back to expire that map, so the whole
    session agrees — checked below, because rewriting the read in ORM terms is
    the obvious tidy-up and would reintroduce a cancel that is silently ignored.
    """
    from app.models.potation import DownloadJob
    from app.services.potation import queue

    wipe(db)
    queue.enqueue(db, "B0CANCEL01")
    claimed = queue.claim_next(db)
    assert claimed is not None

    # Load the row through the ORM, as a worker touching the job would.
    loaded = db.query(DownloadJob).filter(DownloadJob.id == claimed.id).first()
    assert loaded.cancel_requested is False
    assert queue.cancel_requested(db, claimed.id) is False

    other = Session()
    try:
        assert queue.request_cancel(other, claimed.id) is True
    finally:
        other.close()

    assert queue.cancel_requested(db, claimed.id) is True, (
        "the worker's session did not see a cancel written by another session — "
        "the API would report success and the download would keep going"
    )
    # And the session's ORM view agrees, which is what the rollback buys: an
    # identity map left unexpired hands back the stale instance forever.
    fresh = db.query(DownloadJob).filter(DownloadJob.id == claimed.id).first()
    assert fresh.cancel_requested is True, (
        "the identity map was not expired: an ORM read still reports the job as "
        "not cancelled, so any code reading the job object would ignore the cancel"
    )
    print("✓ a cancel from another session is seen by both the raw read and the ORM view")


def test_cancelling_a_queued_job_stops_it_outright(db) -> None:
    from app.services.potation import queue

    wipe(db)
    job = queue.enqueue(db, "B0QCANCEL1")
    assert queue.request_cancel(db, job.id) is True
    db.refresh(job)
    assert job.state == "cancelled", (
        f"state={job.state}: a queued job was only flagged, but no worker will "
        "ever look at the flag, so it would sit queued forever"
    )
    assert queue.claim_next(db) is None, "a cancelled job was still claimable"

    # Cancelling something already finished is a no-op, not an error.
    done = queue.enqueue(db, "B0DONE0001")
    queue.set_state(db, done, "complete")
    assert queue.request_cancel(db, done.id) is False
    assert queue.request_cancel(db, 999999) is False
    print("✓ a queued job is cancelled outright; a finished or absent one is a no-op")


async def test_a_cancelled_job_ends_cancelled_not_failed(db) -> None:
    from app.services.potation import queue
    from app.services.potation.download import DownloadCancelled

    wipe(db)
    queue.enqueue(db, "B0MIDCAN01")
    job = queue.claim_next(db)
    state = await queue.run_job(db, job, pipeline=failing_pipeline(
        DownloadCancelled("stopped at 40%; partial file kept")
    ))
    assert state == "cancelled", state
    db.refresh(job)
    assert job.state == "cancelled" and job.error_code is None, (
        "a cancel was recorded as a failure — it would then be retried"
    )
    print("✓ a cancellation ends as cancelled, with no error code and no retry")


# ── Retry classification ─────────────────────────────────────────────────────

async def test_retryable_failures_go_back_to_the_queue(db) -> None:
    from app.models.potation import ERR_NETWORK
    from app.services.potation import queue
    from app.services.potation.download import DownloadError

    wipe(db)
    queue.enqueue(db, "B0RETRY001", max_retries=2)

    for expected_retry in (1, 2):
        job = queue.claim_next(db)
        assert job is not None, f"job was not re-queued for attempt {expected_retry}"
        state = await queue.run_job(db, job, pipeline=failing_pipeline(
            DownloadError("connection reset", ERR_NETWORK)
        ))
        assert state == "queued", state
        db.refresh(job)
        assert job.retry_count == expected_retry, job.retry_count

    # Third failure exhausts max_retries.
    job = queue.claim_next(db)
    state = await queue.run_job(db, job, pipeline=failing_pipeline(
        DownloadError("connection reset", ERR_NETWORK)
    ))
    assert state == "error", state
    assert queue.claim_next(db) is None, "an exhausted job was queued again"
    print("✓ a network failure retries up to max_retries, then fails for good")


async def test_permanent_failures_are_not_retried(db) -> None:
    """Retrying a licence denial spends quota to fail again."""
    from app.models.potation import ERR_DRM_UNSUPPORTED, ERR_LICENSE_DENIED
    from app.services.potation import queue
    from app.services.potation.decrypt import DecryptError

    for code in (ERR_LICENSE_DENIED, ERR_DRM_UNSUPPORTED):
        wipe(db)
        queue.enqueue(db, "B0PERM0001", max_retries=3)
        job = queue.claim_next(db)
        state = await queue.run_job(db, job, pipeline=failing_pipeline(
            DecryptError("no", code)
        ))
        assert state == "error", (code, state)
        db.refresh(job)
        assert job.retry_count == 0 and job.error_code == code, (job.retry_count, job.error_code)
    print("✓ a licence denial and unsupported DRM fail immediately, without burning retries")


async def test_an_unexpected_exception_does_not_take_the_queue_down(db) -> None:
    from app.models.potation import ERR_UNKNOWN
    from app.services.potation import queue

    wipe(db)
    queue.enqueue(db, "B0BOOM0001", max_retries=0)
    job = queue.claim_next(db)
    state = await queue.run_job(db, job, pipeline=failing_pipeline(
        RuntimeError("something nobody anticipated")
    ))
    assert state == "error", state
    db.refresh(job)
    assert job.error_code == ERR_UNKNOWN
    assert "nobody anticipated" in (job.error_message or "")
    print("✓ an unclassified exception becomes an error row, not an escaped exception")


# ── Success, and the quota hand-off ──────────────────────────────────────────

async def test_success_completes_and_records_quota(db) -> None:
    from app.services.potation import queue, quota

    wipe(db)
    queue.enqueue(db, "B0GOOD0001")
    job = queue.claim_next(db)
    state = await queue.run_job(db, job, pipeline=ok_pipeline(bytes_written=987))

    assert state == "complete", state
    db.refresh(job)
    assert job.stage_progress == 100 and job.completed_at is not None
    assert job.error_code is None

    usage = quota.usage(db, account_id="ACCT")
    assert usage.used == 1, (
        "a completed download was not recorded in the ledger — the count would "
        "understate what has been pulled today"
    )
    print("✓ a successful job completes at 100% and lands in the quota ledger")


# ── Resume after a crash ─────────────────────────────────────────────────────

def test_orphans_are_requeued_not_failed(db) -> None:
    from app.services.potation import queue

    wipe(db)
    for asin, state in (("B0ORPH0001", "downloading"), ("B0ORPH0002", "decrypting"),
                        ("B0ORPH0003", "licensing")):
        job = queue.enqueue(db, asin)
        job.state = state
        job.stage_progress = 60
        db.commit()
    finished = queue.enqueue(db, "B0KEEP0001")
    queue.set_state(db, finished, "complete")
    failed = queue.enqueue(db, "B0KEEP0002")
    queue.set_state(db, failed, "error")

    assert queue.reset_orphans(db) == 3

    from app.models.potation import DownloadJob
    states = {j.book_asin: j.state for j in db.query(DownloadJob).all()}
    for asin in ("B0ORPH0001", "B0ORPH0002", "B0ORPH0003"):
        assert states[asin] == "queued", (
            f"{asin} came back as {states[asin]} — failing an interrupted job "
            "throws away a mostly-finished transfer and spends the quota again"
        )
    assert states["B0KEEP0001"] == "complete" and states["B0KEEP0002"] == "error", states
    print("✓ a crash requeues in-flight jobs and leaves finished ones alone")


# ── The worker loop ──────────────────────────────────────────────────────────

async def test_the_worker_drains_the_queue_and_stops(db) -> None:
    from app.services.potation import queue

    wipe(db)
    for i in range(5):
        queue.enqueue(db, f"B0WORK{i:05d}")

    stop = asyncio.Event()
    worker = asyncio.create_task(queue.work_forever(
        Session, stop=stop, concurrency=2, pipeline=ok_pipeline(), idle_interval=0.05
    ))

    for _ in range(200):
        await asyncio.sleep(0.05)
        db.rollback()
        from app.models.potation import DownloadJob
        left = db.query(DownloadJob).filter(DownloadJob.state == "queued").count()
        if left == 0:
            break

    stop.set()
    await asyncio.wait_for(worker, timeout=10)

    db.rollback()
    from app.models.potation import DownloadJob
    done = db.query(DownloadJob).filter(DownloadJob.state == "complete").count()
    assert done == 5, f"{done} of 5 jobs completed"
    print("✓ the worker drains the queue at the configured concurrency and stops on demand")


async def test_the_worker_survives_a_failing_job(db) -> None:
    """One bad book must not stop everything behind it."""
    from app.models.potation import DownloadJob, ERR_LICENSE_DENIED
    from app.services.potation import queue
    from app.services.potation.decrypt import DecryptError

    wipe(db)
    queue.enqueue(db, "B0BAD00001", max_retries=0)
    queue.enqueue(db, "B0FINE0001", max_retries=0)

    async def mixed(db_, job, *, should_cancel, on_progress):
        if job.book_asin == "B0BAD00001":
            raise DecryptError("nope", ERR_LICENSE_DENIED)
        return queue.JobOutcome(bytes_written=1, account_id="ACCT")

    stop = asyncio.Event()
    worker = asyncio.create_task(queue.work_forever(
        Session, stop=stop, concurrency=1, pipeline=mixed, idle_interval=0.05
    ))
    for _ in range(200):
        await asyncio.sleep(0.05)
        db.rollback()
        if db.query(DownloadJob).filter(DownloadJob.state == "queued").count() == 0:
            break
    stop.set()
    await asyncio.wait_for(worker, timeout=10)

    db.rollback()
    states = {j.book_asin: j.state for j in db.query(DownloadJob).all()}
    assert states["B0BAD00001"] == "error", states
    assert states["B0FINE0001"] == "complete", (
        "a failing job stopped the queue; everything behind it would stall"
    )
    print("✓ a failing job does not stall the ones behind it")


# ── Runner ───────────────────────────────────────────────────────────────────

async def run_async(db) -> None:
    await test_a_cancelled_job_ends_cancelled_not_failed(db)
    await test_retryable_failures_go_back_to_the_queue(db)
    await test_permanent_failures_are_not_retried(db)
    await test_an_unexpected_exception_does_not_take_the_queue_down(db)
    await test_success_completes_and_records_quota(db)
    await test_the_worker_drains_the_queue_and_stops(db)
    await test_the_worker_survives_a_failing_job(db)


def main() -> None:
    global Session
    Session = make_sessionmaker()

    with Session() as db:
        test_enqueue_deduplicates(db)
        test_bulk_enqueue_goes_through_the_valve(db)
        test_the_valve_sees_only_new_work(db)
        test_claim_takes_one_job_at_a_time_in_priority_order(db)
        test_a_claimed_job_is_not_claimable_again(db)
        test_a_cancel_written_by_another_session_is_seen(db)
        test_cancelling_a_queued_job_stops_it_outright(db)
        test_orphans_are_requeued_not_failed(db)
        asyncio.run(run_async(db))

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll queue checks passed.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
