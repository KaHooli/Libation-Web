"""Tables Potation owns.

These replace everything currently read out of Libation's own SQLite database
(`LibationContext.db`), its `AccountsSettings.json`, and its
`FileLocationsV2.json` file cache. Nothing reads from them until Phase C; they
are added alongside the existing tables so Phase A changes no API behaviour.

Timestamps here are `DateTime(timezone=True)`. The pre-existing tables store
tz-aware values into naive columns — which SQLite tolerates but PostgreSQL
silently strips, and which is why `api/downloads.py` has to re-attach
`timezone.utc` when reading `last_auto_download_at` back. Retrofitting the old
tables is deliberately left until Phase D deletes them.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)

from ..database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── download_jobs.state ───────────────────────────────────────────────────────
JOB_QUEUED = "queued"
JOB_LICENSING = "licensing"
JOB_DOWNLOADING = "downloading"
JOB_DECRYPTING = "decrypting"
JOB_TAGGING = "tagging"
JOB_IMPORTING = "importing"
JOB_COMPLETE = "complete"
JOB_ERROR = "error"
JOB_CANCELLED = "cancelled"
JOB_PAUSED = "paused"

#: States in which a job is still owed work by the queue.
JOB_ACTIVE_STATES = frozenset({
    JOB_QUEUED, JOB_LICENSING, JOB_DOWNLOADING,
    JOB_DECRYPTING, JOB_TAGGING, JOB_IMPORTING,
})

# ── download_jobs.error_code ──────────────────────────────────────────────────
# Classified so retry can tell "try again" from "never going to work". The
# current engine collapses all of these into "Error processing book. Skipping."
ERR_NOT_OWNED = "not_owned"
ERR_LICENSE_DENIED = "license_denied"
ERR_DRM_UNSUPPORTED = "drm_unsupported"
ERR_NETWORK = "network"
ERR_DISK_FULL = "disk_full"
ERR_FFMPEG = "ffmpeg"
ERR_QUOTA = "quota_exceeded"
ERR_UNKNOWN = "unknown"

#: Error codes worth retrying automatically.
RETRYABLE_ERRORS = frozenset({ERR_NETWORK, ERR_UNKNOWN})


class AudibleLoginState(Base):
    """One in-flight Audible device-registration flow.

    The PKCE verifier and device serial have to survive the round trip through
    Amazon's sign-in page, which spans two separate HTTP requests to us. The old
    implementation kept a live `libationcli login-external` subprocess in a
    module-level dict for up to ten minutes; a row is strictly better — it
    survives a restart, works across workers, and holds no file descriptors.
    """

    __tablename__ = "audible_login_states"

    id = Column(Integer, primary_key=True)
    state = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, nullable=True)
    marketplace = Column(String, nullable=False)
    country_code = Column(String, nullable=False)
    code_verifier = Column(Text, nullable=False)
    serial = Column(String, nullable=False)
    domain = Column(String, nullable=False)
    #: Which web-UI user started this, so the account can be attributed.
    started_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)


class AudibleAccount(Base):
    """A connected Audible account. Replaces `AccountsSettings.json` and the
    hand-rolled `audible_account_settings` table."""

    __tablename__ = "audible_accounts"

    account_id = Column(String, primary_key=True)
    locale = Column(String, nullable=False, default="us")
    marketplace_id = Column(String, nullable=True)
    account_name = Column(String, nullable=True)
    email = Column(String, nullable=True)

    #: Fernet ciphertext of the registration blob (refresh token, adp_token,
    #: device private key). Text, not binary — Fernet output is base64 ASCII.
    auth_blob = Column(Text, nullable=True)

    added_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    auto_download = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    #: Set when the stored blob cannot be decrypted, or Audible rejected it.
    needs_reauth = Column(Boolean, nullable=False, default=False)

    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Book(Base):
    """One title from an Audible library. Replaces reads of `LibationContext.db`."""

    __tablename__ = "books"

    asin = Column(String, primary_key=True)
    account_id = Column(
        String, ForeignKey("audible_accounts.account_id"), nullable=True, index=True
    )

    #: A MultiPartBook parent has no downloadable content of its own; the parts
    #: are separate rows pointing back at it.
    parent_asin = Column(String, ForeignKey("books.asin"), nullable=True, index=True)
    part_index = Column(Integer, nullable=True)
    is_multipart_parent = Column(Boolean, nullable=False, default=False)

    title = Column(String, nullable=False, default="")
    subtitle = Column(String, nullable=True)
    authors = Column(JSON, nullable=True)
    narrators = Column(JSON, nullable=True)
    series_name = Column(String, nullable=True)
    series_sequence = Column(String, nullable=True)

    length_minutes = Column(Integer, nullable=True)
    language = Column(String, nullable=True)
    is_abridged = Column(Boolean, nullable=True)
    content_type = Column(String, nullable=True)
    #: Useful proxy for Widevine exposure before any license request is made.
    content_delivery_type = Column(String, nullable=True)
    is_audible_plus = Column(Boolean, nullable=True)

    purchase_date = Column(DateTime(timezone=True), nullable=True)
    release_date = Column(DateTime(timezone=True), nullable=True)
    publisher = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    cover_url = Column(String, nullable=True)

    #: Tri-state. NULL means "derive from book_files"; 1/0 is an explicit user
    #: override, which is what keeps `PATCH /api/liberate/books/{id}` meaningful.
    liberated_override = Column(Integer, nullable=True)

    synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class BookFile(Base):
    """A file on disk belonging to a book.

    Replaces Libation's `FileLocationsV2.json` and doubles as the reconciliation
    cache. `path` goes stale as soon as Chaptarr moves a file in `move` import
    mode, so readers must tolerate a row whose path no longer exists.
    """

    __tablename__ = "book_files"

    id = Column(Integer, primary_key=True)
    book_asin = Column(String, ForeignKey("books.asin"), nullable=False, index=True)
    path = Column(String, nullable=False, unique=True)
    kind = Column(String, nullable=False, default="audio")  # audio | pdf | cover

    #: Ordering for multi-part and chapter-split books. Sorting paths lexically
    #: is what puts "Part 10" before "Part 2" in the current implementation.
    part_index = Column(Integer, nullable=True)

    size = Column(Integer, nullable=True)
    mtime = Column(Float, nullable=True)

    #: Which root the file was found under: "audiobooks" or "chaptarr".
    root = Column(String, nullable=True)
    #: How the ASIN was established: tag | path | metadata_json | fuzzy | download.
    asin_source = Column(String, nullable=True)
    #: False for a fuzzy match awaiting confirmation. Never treat as liberated.
    confirmed = Column(Boolean, nullable=False, default=True)

    discovered_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    verified_at = Column(DateTime(timezone=True), nullable=True)


class AudibleLicense(Base):
    """A license response, persisted so retry does not re-license.

    Audible enforces a daily download cap, so re-requesting a license on every
    retry burns quota. `drm_type` is also the column that answers the Widevine
    question once syncs are running.
    """

    __tablename__ = "audible_licenses"

    id = Column(Integer, primary_key=True)
    book_asin = Column(String, ForeignKey("books.asin"), nullable=False, index=True)

    drm_type = Column(String, nullable=True)  # Adrm | Widevine | none
    key_ciphertext = Column(Text, nullable=True)
    iv_ciphertext = Column(Text, nullable=True)

    download_url = Column(Text, nullable=True)
    url_expires_at = Column(DateTime(timezone=True), nullable=True)
    refresh_date = Column(DateTime(timezone=True), nullable=True)

    acr = Column(String, nullable=True)
    version = Column(String, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class DownloadJob(Base):
    """A unit of acquisition work. Replaces the `downloads` table in Phase C."""

    __tablename__ = "download_jobs"

    id = Column(Integer, primary_key=True)
    book_asin = Column(String, nullable=False, index=True)
    book_title = Column(String, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    state = Column(String, nullable=False, default=JOB_QUEUED, index=True)
    stage_progress = Column(Integer, nullable=False, default=0)
    bytes_done = Column(Integer, nullable=False, default=0)
    bytes_total = Column(Integer, nullable=True)

    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)

    #: Cooperative cancellation. The worker checks this between stages.
    cancel_requested = Column(Boolean, nullable=False, default=False)
    #: Lower runs first, so the queue can be reordered without touching rows.
    priority = Column(Integer, nullable=False, default=100)
    license_id = Column(Integer, ForeignKey("audible_licenses.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class DownloadQuotaEntry(Base):
    """One recorded download, for daily-cap accounting.

    Written on every success even when no limit is configured, mirroring
    Libation's own reasoning: turning a limit on later should reflect downloads
    already performed.
    """

    __tablename__ = "download_quota"

    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=True, index=True)
    book_asin = Column(String, nullable=False, index=True)
    size_bytes = Column(Integer, nullable=True)
    is_audible_plus = Column(Boolean, nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class ReconciliationRun(Base):
    """A pass over the audiobook roots matching files on disk to books.

    The most recent completed row is the marker that gates bulk enqueueing: an
    auto-download that fires before reconciliation has finished would re-download
    a library that is already on disk.
    """

    __tablename__ = "reconciliation_runs"

    id = Column(Integer, primary_key=True)
    status = Column(String, nullable=False, default="running")  # running|complete|error
    roots = Column(JSON, nullable=True)
    files_scanned = Column(Integer, nullable=False, default=0)
    books_matched = Column(Integer, nullable=False, default=0)
    unmatched = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
