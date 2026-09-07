#!/usr/bin/env python3
"""The daily-download ledger.

Phase B4. Small surface, but three of its properties are easy to get wrong in a
direction that costs real downloads or lets an account get throttled:

  * **Every success is recorded, limit or no limit.** Switching a limit on
    tomorrow must reflect what was downloaded today. Recording only while a
    limit is set hands out a free extra allowance the first day someone sets one.
  * **The window is rolling, not calendar.** Audible calls its cap daily and does
    not say in which timezone. A rolling 24 hours never permits more than the
    limit in *any* 24-hour span, so it cannot err in the direction that gets an
    account throttled.
  * **A malformed limit reads as unlimited.** A bad `system_settings` value
    blocking every download is a far worse failure than not enforcing a
    self-imposed guard — Audible's own cap is still there underneath.

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-quota.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="quota-test-"))
CONFIG = WORKDIR / "config"
DATA = WORKDIR / "data"
BOOKS = WORKDIR / "audiobooks"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "quota-test-only-not-a-real-secret")

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import chaptarr, download, potation, user
    assert all((chaptarr, download, potation, user))

    engine = create_engine(
        f"sqlite:///{DATA / 'quota.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS system_settings "
            "(key TEXT PRIMARY KEY, value TEXT DEFAULT '')"
        ))
    return sessionmaker(bind=engine)


def _wipe(db) -> None:
    from app.models.potation import DownloadQuotaEntry
    db.query(DownloadQuotaEntry).delete()
    db.commit()


# ── Recording ────────────────────────────────────────────────────────────────

def test_records_without_a_limit_configured(db) -> None:
    """The whole reason Libation records unconditionally."""
    from app.services.potation import quota

    _wipe(db)
    quota.set_daily_limit(db, None)
    assert quota.daily_limit(db) is None

    for i in range(3):
        quota.record_download(
            db, book_asin=f"B0NOLIMIT{i}", account_id="ACCT",
            size_bytes=1000, now=NOW - timedelta(hours=i),
        )

    # Someone sets a limit tomorrow. Today's downloads must already be counted.
    quota.set_daily_limit(db, 5)
    now = quota.usage(db, account_id="ACCT", now=NOW)
    assert now.used == 3, (
        f"used={now.used}: downloads made before a limit existed were not "
        "counted, which hands out a free extra allowance the day one is set"
    )
    assert now.limit == 5 and now.remaining == 2
    print("✓ downloads are recorded with no limit configured, and count once one is set")


def test_window_is_rolling_and_excludes_older_downloads(db) -> None:
    from app.services.potation import quota

    _wipe(db)
    quota.set_daily_limit(db, 10)
    quota.record_download(db, book_asin="B0INSIDE01", account_id="ACCT",
                          now=NOW - timedelta(hours=23, minutes=59))
    quota.record_download(db, book_asin="B0OUTSIDE1", account_id="ACCT",
                          now=NOW - timedelta(hours=24, minutes=1))

    seen = quota.usage(db, account_id="ACCT", now=NOW)
    assert seen.used == 1, (
        f"used={seen.used}: the window is not 24 hours — a download just over a "
        "day old is still being counted, or one just under is not"
    )
    # And it ages out on its own as time passes, with no cron to run.
    later = quota.usage(db, account_id="ACCT", now=NOW + timedelta(hours=1))
    assert later.used == 0, later.used
    print("✓ the window rolls: entries age out by the clock, with nothing to run")


def test_resets_at_is_when_the_oldest_entry_ages_out(db) -> None:
    from app.services.potation import quota

    _wipe(db)
    quota.set_daily_limit(db, 2)
    oldest = NOW - timedelta(hours=20)
    quota.record_download(db, book_asin="B0OLDEST01", account_id="ACCT", now=oldest)
    quota.record_download(db, book_asin="B0NEWER001", account_id="ACCT",
                          now=NOW - timedelta(hours=2))

    seen = quota.usage(db, account_id="ACCT", now=NOW)
    assert seen.used == 2 and seen.remaining == 0
    assert seen.resets_at is not None
    delta = abs((seen.resets_at - (oldest + quota.DEFAULT_WINDOW)).total_seconds())
    assert delta < 2, (
        f"resets_at is {seen.resets_at}, not when the oldest entry ages out — "
        "a caller would tell someone to come back at the wrong time"
    )
    print("✓ resets_at is when the oldest download in the window ages out")


def test_accounts_are_counted_separately(db) -> None:
    """Audible's cap is per account; one busy account must not block another."""
    from app.services.potation import quota

    _wipe(db)
    quota.set_daily_limit(db, 2)
    for i in range(2):
        quota.record_download(db, book_asin=f"B0ACCTA{i:03d}", account_id="A", now=NOW)

    assert quota.usage(db, account_id="A", now=NOW).remaining == 0
    assert quota.usage(db, account_id="B", now=NOW).remaining == 2, (
        "a second account inherited the first one's usage"
    )
    # The unscoped total is for display, and sees everything.
    assert quota.usage(db, now=NOW).used == 2
    print("✓ usage is per account, with an unscoped total for display")


# ── Enforcement ──────────────────────────────────────────────────────────────

def test_check_quota_refuses_and_reports_when_to_retry(db) -> None:
    from app.services.potation import quota

    _wipe(db)
    quota.set_daily_limit(db, 2)
    quota.record_download(db, book_asin="B0ONE00001", account_id="ACCT", now=NOW)
    quota.check_quota(db, account_id="ACCT", now=NOW)  # one more fits

    quota.record_download(db, book_asin="B0TWO00001", account_id="ACCT", now=NOW)
    try:
        quota.check_quota(db, account_id="ACCT", now=NOW)
        raise AssertionError("a download past the limit was allowed")
    except quota.QuotaExceeded as exc:
        assert "2 of 2" in str(exc), str(exc)
        assert exc.resets_at is not None, "the refusal did not say when to retry"

    # A bulk ask is checked as a whole, not one at a time.
    _wipe(db)
    try:
        quota.check_quota(db, account_id="ACCT", additional=3, now=NOW)
        raise AssertionError("a bulk enqueue past the limit was allowed")
    except quota.QuotaExceeded:
        pass
    quota.check_quota(db, account_id="ACCT", additional=2, now=NOW)
    print("✓ the limit refuses single and bulk asks, and says when to retry")


def test_no_limit_never_refuses(db) -> None:
    from app.services.potation import quota

    _wipe(db)
    quota.set_daily_limit(db, None)
    for i in range(50):
        quota.record_download(db, book_asin=f"B0MANY{i:05d}", account_id="ACCT", now=NOW)

    seen = quota.check_quota(db, account_id="ACCT", additional=100, now=NOW)
    assert seen.unlimited and seen.remaining is None and seen.used == 50
    print("✓ with no limit configured nothing is refused, but usage is still counted")


def test_a_malformed_limit_reads_as_unlimited(db) -> None:
    """A bad setting must not be able to block every download."""
    from sqlalchemy import text
    from app.services.potation import quota

    _wipe(db)
    for bad in ("not-a-number", "-5", "0", "  ", "3.5"):
        db.connection().execute(
            text("INSERT INTO system_settings (key, value) VALUES (:k, :v) "
                 "ON CONFLICT(key) DO UPDATE SET value = :v"),
            {"k": quota.SETTING_KEY, "v": bad},
        )
        db.commit()
        assert quota.daily_limit(db) is None, (
            f"{bad!r} was read as a limit; a malformed setting would block "
            "every download while Audible's own cap is still underneath"
        )
        quota.check_quota(db, account_id="ACCT", additional=999, now=NOW)
    print("✓ a malformed, zero or negative limit reads as unlimited, five ways")


def test_setting_round_trip(db) -> None:
    from app.services.potation import quota

    quota.set_daily_limit(db, 7)
    assert quota.daily_limit(db) == 7
    quota.set_daily_limit(db, 0)
    assert quota.daily_limit(db) is None, "zero should clear the limit"
    quota.set_daily_limit(db, 12)
    assert quota.daily_limit(db) == 12
    quota.set_daily_limit(db, None)
    assert quota.daily_limit(db) is None
    print("✓ the limit round-trips through system_settings, and zero clears it")


def test_audible_plus_is_recorded_but_not_enforced_on(db) -> None:
    """Recorded so the question can be answered from data, not guessed now."""
    from app.models.potation import DownloadQuotaEntry
    from app.services.potation import quota

    _wipe(db)
    quota.set_daily_limit(db, 2)
    quota.record_download(db, book_asin="B0PLUS0001", account_id="ACCT",
                          is_audible_plus=True, now=NOW)
    quota.record_download(db, book_asin="B0OWNED001", account_id="ACCT",
                          is_audible_plus=False, now=NOW)

    rows = db.query(DownloadQuotaEntry).order_by(DownloadQuotaEntry.book_asin).all()
    assert [r.is_audible_plus for r in rows] == [False, True], rows
    # Both count: the subscription/purchase distinction is not acted on yet.
    assert quota.usage(db, account_id="ACCT", now=NOW).used == 2
    print("✓ Audible Plus is recorded on the row but does not change the count")


def test_prune_keeps_the_window_intact(db) -> None:
    from app.services.potation import quota

    _wipe(db)
    quota.record_download(db, book_asin="B0ANCIENT1", account_id="ACCT",
                          now=NOW - timedelta(days=40))
    quota.record_download(db, book_asin="B0RECENT01", account_id="ACCT",
                          now=NOW - timedelta(hours=1))

    removed = quota.prune(db, now=NOW)
    assert removed == 1, removed
    assert quota.usage(db, account_id="ACCT", now=NOW).used == 1, (
        "pruning removed something inside the 24-hour window"
    )
    print("✓ pruning drops old history without touching the counting window")


# ── Runner ───────────────────────────────────────────────────────────────────

def main() -> None:
    Session = _session()
    with Session() as db:
        test_records_without_a_limit_configured(db)
        test_window_is_rolling_and_excludes_older_downloads(db)
        test_resets_at_is_when_the_oldest_entry_ages_out(db)
        test_accounts_are_counted_separately(db)
        test_check_quota_refuses_and_reports_when_to_retry(db)
        test_no_limit_never_refuses(db)
        test_a_malformed_limit_reads_as_unlimited(db)
        test_setting_round_trip(db)
        test_audible_plus_is_recorded_but_not_enforced_on(db)
        test_prune_keeps_the_window_intact(db)

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll quota checks passed.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
