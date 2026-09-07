"""Daily-download accounting — the thing Libation gives us for free today.

Audible enforces a daily download cap of its own. **This ledger is not that
cap**, and must not be mistaken for it: Audible's limit is enforced upstream and
shows up as a refused licence (`license_denied`), whatever this table says. What
the ledger is for is three narrower things:

1. **Reporting.** "How many have I pulled today" is otherwise unanswerable once
   LibationCli stops keeping the count.
2. **A self-imposed guard.** Somebody who would rather not go near the ceiling
   can set a limit below it, and the queue will stop before Audible does.
3. **History that predates the limit.** Every success is recorded even when no
   limit is configured — Libation's own reasoning, and the right one: switching
   a limit on tomorrow should reflect what was downloaded today, not start the
   count from zero and hand out a free extra allowance.

**The window is a rolling 24 hours, deliberately.** Audible describes its cap as
daily; whether that means a calendar day, and in which timezone, is not
something this codebase knows. A rolling window is the conservative reading —
it never permits more than `limit` downloads in *any* 24-hour span, so it cannot
be wrong in the direction that gets an account throttled. If Audible's real rule
is a calendar day, this is merely stricter near a boundary.

No limit is configured by default. Guessing Audible's number and enforcing the
guess would cost real downloads for no benefit, since Audible refuses on its own
account when the true limit is reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ...models.potation import DownloadQuotaEntry

#: Rolling, not calendar — see the module docstring.
DEFAULT_WINDOW = timedelta(hours=24)

#: `system_settings` key. Empty or absent means no self-imposed limit.
SETTING_KEY = "potation_daily_download_limit"


class QuotaExceeded(Exception):
    """The self-imposed daily limit would be passed by this download."""

    def __init__(self, message: str, resets_at: Optional[datetime] = None):
        super().__init__(message)
        self.resets_at = resets_at


@dataclass(frozen=True)
class QuotaUsage:
    used: int
    limit: Optional[int]
    #: When the oldest download in the window ages out, freeing one slot.
    #: None when nothing is recorded, or when no limit makes it meaningless.
    resets_at: Optional[datetime] = None

    @property
    def unlimited(self) -> bool:
        return self.limit is None

    @property
    def remaining(self) -> Optional[int]:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Older rows may be naive — Postgres strips tzinfo from a naive column."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── The configured limit ─────────────────────────────────────────────────────

def daily_limit(db: Session) -> Optional[int]:
    """The self-imposed limit, or None for unlimited.

    A stored value that is not a positive integer reads as unlimited rather than
    raising: a malformed setting must not be able to block every download.
    """
    row = db.connection().execute(
        text("SELECT value FROM system_settings WHERE key = :k"), {"k": SETTING_KEY}
    ).first()
    if row is None or not row[0]:
        return None
    try:
        value = int(str(row[0]).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def set_daily_limit(db: Session, limit: Optional[int]) -> None:
    """Set or clear the limit. Zero and negative both mean unlimited."""
    stored = "" if not limit or limit <= 0 else str(int(limit))
    db.connection().execute(
        text(
            "INSERT INTO system_settings (key, value) VALUES (:k, :v) "
            "ON CONFLICT(key) DO UPDATE SET value = :v"
        ),
        {"k": SETTING_KEY, "v": stored},
    )
    db.commit()


# ── Recording ────────────────────────────────────────────────────────────────

def record_download(
    db: Session,
    *,
    book_asin: str,
    account_id: Optional[str] = None,
    size_bytes: Optional[int] = None,
    is_audible_plus: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> DownloadQuotaEntry:
    """Record one completed download.

    Called on **every** success, limit configured or not. `is_audible_plus` is
    recorded but never branched on: subscription titles may or may not count
    against the same allowance, and recording lets that be answered from data
    later instead of guessed at now.
    """
    entry = DownloadQuotaEntry(
        account_id=account_id,
        book_asin=book_asin,
        size_bytes=size_bytes,
        is_audible_plus=is_audible_plus,
        recorded_at=now or _utcnow(),
    )
    db.add(entry)
    db.commit()
    return entry


# ── Reading ──────────────────────────────────────────────────────────────────

def usage(
    db: Session,
    *,
    account_id: Optional[str] = None,
    window: timedelta = DEFAULT_WINDOW,
    now: Optional[datetime] = None,
) -> QuotaUsage:
    """Downloads inside the window, and when a slot next frees up.

    `account_id=None` counts every account. Audible's cap is per account, so a
    caller enforcing a limit should pass one; the unscoped total is for display.
    """
    moment = now or _utcnow()
    since = moment - window

    query = db.query(DownloadQuotaEntry).filter(DownloadQuotaEntry.recorded_at > since)
    if account_id is not None:
        query = query.filter(DownloadQuotaEntry.account_id == account_id)

    used = query.with_entities(func.count(DownloadQuotaEntry.id)).scalar() or 0
    limit = daily_limit(db)

    resets_at = None
    if used:
        oldest = query.with_entities(func.min(DownloadQuotaEntry.recorded_at)).scalar()
        oldest = _aware(oldest)
        if oldest is not None:
            resets_at = oldest + window

    return QuotaUsage(used=used, limit=limit, resets_at=resets_at)


def check_quota(
    db: Session,
    *,
    account_id: Optional[str] = None,
    additional: int = 1,
    window: timedelta = DEFAULT_WINDOW,
    now: Optional[datetime] = None,
) -> QuotaUsage:
    """Raise if `additional` more downloads would pass the limit.

    Returns the current usage when it would not, so a caller can report it
    without asking twice.
    """
    current = usage(db, account_id=account_id, window=window, now=now)
    if current.limit is None:
        return current
    if current.used + additional > current.limit:
        raise QuotaExceeded(
            f"Daily download limit reached: {current.used} of {current.limit} "
            f"in the last {int(window.total_seconds() // 3600)} hours.",
            resets_at=current.resets_at,
        )
    return current


def prune(db: Session, *, keep: timedelta = timedelta(days=30),
          now: Optional[datetime] = None) -> int:
    """Drop ledger rows older than `keep`.

    The window only ever looks back 24 hours, but a month of history makes the
    reporting useful and keeps the table from growing without bound on a library
    that downloads daily for years.
    """
    cutoff = (now or _utcnow()) - keep
    deleted = (
        db.query(DownloadQuotaEntry)
        .filter(DownloadQuotaEntry.recorded_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted or 0)
