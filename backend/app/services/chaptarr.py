"""Push downloaded audiobooks into a self-hosted Chaptarr instance.

Chaptarr (https://github.com/Chaptarr/chaptarr) is a Readarr fork for audiobook
and eBook libraries. It exposes a Readarr-compatible v1 API authenticated with
an ``X-Api-Key`` header.

The integration assumes Libation's books directory and Chaptarr's library are
the *same volume* mounted into both containers, so the file Libation just wrote
is already on disk from Chaptarr's point of view. All we have to do is tell
Chaptarr which book it is. If the two containers mount that volume at different
paths, ``path_from`` / ``path_to`` rewrites the prefix on the way out.

Matching is driven by the Audible ASIN, which Libation stores as the primary key
of every book. Chaptarr's canonical provider prefix for Amazon/Audible ids is
``az:``, so ``az:{ASIN}`` resolves a book through Chaptarr's metadata server
with no fuzzy title matching at all::

    GET /api/v1/book/lookup?term=az:B0XXXXXXXX&mediaType=audiobook

That returns the work id, the author id and the edition id. Those get handed to
Chaptarr's ManualImport command as *suggestions*::

    POST /api/v1/command
    {"name": "ManualImport", "importMode": "auto", "files": [{...}]}

``selectionSource: 1`` (``UserMetadataSuggestion``) tells Chaptarr the caller
picked this metadata deliberately, which makes it materialize the author, book
and edition from provider metadata even when nothing in the library is
monitoring them — that is what lets an unmonitored book import at all.

When the ASIN is unknown to Chaptarr's metadata server we fall back to
``DownloadedBooksScan`` over the containing folder, which lets Chaptarr do its
own tag/filename matching (also creating missing authors, via
``requireDefaultRootFolderForMissingAuthors``).

The traffic also runs the other way: before pulling a book down from Audible we
can ask Chaptarr whether it already has it, and skip the download if so. The
library index is read from::

    GET /api/v1/book/paged?mediaType=audiobook&includeUnmonitored=true

falling back to ``GET /api/v1/book?mediaType=audiobook`` on older builds. Every
Audible id Chaptarr knows for a book — ``asin``, ``audibleASIN``, the
``az:``-prefixed ``foreignBookId``/``foreignEditionId``, and the same fields on
each edition — is folded into one ASIN → entry index, cached briefly so a bulk
check costs a single round trip. ``mediaType=audiobook`` matters: Chaptarr keeps
separate audiobook and eBook rows, and owning the eBook is no reason to skip the
audiobook.

Two things can count as "already has it", per ``skip_when``:

``has_file``    Chaptarr has a file on disk for the book (the safe default — a
                book it merely tracks but has no file for is exactly the one we
                should still be downloading).
``in_library``  The book exists in Chaptarr's library at all.

The check always fails open. If Chaptarr is down or answers with nonsense we
download the book, because a metadata server being unreachable is not a reason
to lose an audiobook.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.chaptarr import ChaptarrImport
from . import libation as libation_svc
from .logger import get_logger

# ── Settings ──────────────────────────────────────────────────────────────────

SETTING_KEYS = {
    "chaptarr_enabled": "0",
    "chaptarr_url": "",
    "chaptarr_api_key": "",
    "chaptarr_import_mode": "auto",
    "chaptarr_auto_import": "0",
    "chaptarr_path_from": "",
    "chaptarr_path_to": "",
    "chaptarr_skip_existing": "0",
    "chaptarr_skip_when": "has_file",
}

IMPORT_MODES = ("auto", "copy", "move")
SKIP_MODES = ("has_file", "in_library")

MEDIA_TYPE = "audiobook"  # Libation only ever produces audiobooks

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_COMMAND_POLL_INTERVAL = 2.0
_COMMAND_POLL_TIMEOUT = 300.0

# The library index is one big response; give it room without hanging a download.
_LIBRARY_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)
_LIBRARY_PAGE_SIZE = 500
_LIBRARY_PAGE_LIMIT = 200          # 100k books is far past any real library
_LIBRARY_CACHE_TTL = 60.0          # seconds — long enough for one bulk sweep


@dataclass(frozen=True)
class ChaptarrConfig:
    enabled: bool = False
    url: str = ""
    api_key: str = ""
    import_mode: str = "auto"
    auto_import: bool = False
    path_from: str = ""
    path_to: str = ""
    skip_existing: bool = False
    skip_when: str = "has_file"

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")

    @property
    def skip_check_active(self) -> bool:
        """Whether a download should be checked against Chaptarr's library first."""
        return self.enabled and self.configured and self.skip_existing


def load_config(db: Session) -> ChaptarrConfig:
    conn = db.connection()
    rows = conn.execute(
        text("SELECT key, value FROM system_settings WHERE key LIKE 'chaptarr_%'")
    ).fetchall()
    raw = {r[0]: r[1] for r in rows}
    mode = (raw.get("chaptarr_import_mode") or "auto").lower()
    skip_when = (raw.get("chaptarr_skip_when") or "has_file").lower()
    return ChaptarrConfig(
        enabled=raw.get("chaptarr_enabled") == "1",
        url=(raw.get("chaptarr_url") or "").strip(),
        api_key=(raw.get("chaptarr_api_key") or "").strip(),
        import_mode=mode if mode in IMPORT_MODES else "auto",
        auto_import=raw.get("chaptarr_auto_import") == "1",
        path_from=(raw.get("chaptarr_path_from") or "").strip(),
        path_to=(raw.get("chaptarr_path_to") or "").strip(),
        skip_existing=raw.get("chaptarr_skip_existing") == "1",
        skip_when=skip_when if skip_when in SKIP_MODES else "has_file",
    )


def save_config(db: Session, patch: dict) -> ChaptarrConfig:
    """Persist the supplied keys. Values are stored as text, booleans as '0'/'1'."""
    conn = db.connection()
    for key, value in patch.items():
        if key not in SETTING_KEYS or value is None:
            continue
        stored = ("1" if value else "0") if isinstance(value, bool) else str(value).strip()
        conn.execute(
            text(
                "INSERT INTO system_settings (key, value) VALUES (:k, :v) "
                "ON CONFLICT(key) DO UPDATE SET value = :v"
            ),
            {"k": key, "v": stored},
        )
    db.commit()
    return load_config(db)


# ── HTTP plumbing ─────────────────────────────────────────────────────────────

class ChaptarrError(Exception):
    """Any failure talking to Chaptarr — network, auth, or a rejected request."""


def _check_configured(cfg: ChaptarrConfig) -> None:
    if not cfg.configured:
        raise ChaptarrError("Chaptarr is not configured — set its URL and API key in Settings.")


async def _request(cfg: ChaptarrConfig, method: str, path: str, **kwargs) -> httpx.Response:
    _check_configured(cfg)
    url = f"{cfg.base_url}/api/v1{path}"
    headers = {"X-Api-Key": cfg.api_key, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise ChaptarrError(f"Could not reach Chaptarr at {cfg.base_url}: {exc}") from exc

    if resp.status_code == 401:
        raise ChaptarrError("Chaptarr rejected the API key (401).")
    if resp.status_code >= 400:
        raise ChaptarrError(
            f"Chaptarr returned {resp.status_code} for {method} {path}: {resp.text[:300]}"
        )
    return resp


async def test_connection(cfg: ChaptarrConfig) -> dict:
    """Verify URL + API key and report back what Chaptarr says about itself."""
    status = (await _request(cfg, "GET", "/system/status")).json()
    try:
        folders = (await _request(cfg, "GET", "/rootfolder")).json()
    except ChaptarrError:
        folders = []
    return {
        "app_name": status.get("appName") or "Chaptarr",
        "version": status.get("version") or "unknown",
        "root_folders": [
            {"id": f.get("id"), "path": f.get("path"), "name": f.get("name")}
            for f in folders
            if isinstance(f, dict)
        ],
    }


# ── Path mapping ──────────────────────────────────────────────────────────────

def map_path(cfg: ChaptarrConfig, path: str) -> str:
    """Rewrite a Libation-side path into the path Chaptarr sees for the same file."""
    if not cfg.path_from or not cfg.path_to:
        return path
    src = cfg.path_from.rstrip("/")
    dst = cfg.path_to.rstrip("/")
    if path == src:
        return dst
    if path.startswith(src + "/"):
        return dst + path[len(src):]
    return path


# ── Metadata lookup ───────────────────────────────────────────────────────────

async def lookup_by_asin(cfg: ChaptarrConfig, asin: str) -> Optional[dict]:
    """Resolve an Audible ASIN to Chaptarr's provider ids, or None if unknown.

    Returns ``{foreign_book_id, foreign_author_id, foreign_edition_id,
    book_title, author_name}``. ``foreign_author_id`` is the one field
    ManualImport genuinely cannot work without, so a hit that lacks it is
    treated as a miss.
    """
    term = f"az:{asin.strip().upper()}"
    resp = await _request(
        cfg, "GET", f"/book/lookup?term={quote(term)}&mediaType={MEDIA_TYPE}"
    )
    try:
        results = resp.json()
    except ValueError:
        return None
    if not isinstance(results, list) or not results:
        return None

    book = results[0]
    author = book.get("author") or {}
    foreign_author_id = (author.get("foreignAuthorId") or "").strip()
    foreign_book_id = (book.get("foreignBookId") or "").strip()
    if not foreign_author_id or not foreign_book_id:
        return None

    return {
        "foreign_book_id": foreign_book_id,
        "foreign_author_id": foreign_author_id,
        "foreign_edition_id": _pick_edition_id(book, asin),
        "book_title": book.get("title") or "",
        "author_name": author.get("authorName") or "",
    }


def _pick_edition_id(book: dict, asin: str) -> str:
    """Prefer the edition whose ASIN is the one we downloaded, then the monitored one."""
    editions = [e for e in (book.get("editions") or []) if isinstance(e, dict)]
    wanted = asin.strip().upper()

    def edition_asin(e: dict) -> str:
        return (e.get("asin") or e.get("audibleASIN") or "").strip().upper()

    for candidate in (
        next((e for e in editions if edition_asin(e) == wanted), None),
        next((e for e in editions if e.get("monitored")), None),
        editions[0] if editions else None,
    ):
        if candidate and (candidate.get("foreignEditionId") or "").strip():
            return candidate["foreignEditionId"].strip()
    return (book.get("foreignEditionId") or "").strip()


# ── Library index ("does Chaptarr already have this?") ────────────────────────

@dataclass(frozen=True)
class LibraryEntry:
    """One book in Chaptarr's audiobook library, as far as we care about it."""

    chaptarr_book_id: Optional[int]
    title: str
    has_file: bool


# base_url → (fetched_at, {ASIN: LibraryEntry}). A bulk check walks hundreds of
# ASINs; without this each one would re-download the whole library index.
_LIBRARY_CACHE: dict[str, tuple[float, dict[str, LibraryEntry]]] = {}


def invalidate_library_cache(cfg: Optional[ChaptarrConfig] = None) -> None:
    """Drop the cached index — call after anything that changes Chaptarr's library."""
    if cfg is None:
        _LIBRARY_CACHE.clear()
    else:
        _LIBRARY_CACHE.pop(cfg.base_url, None)


def _strip_provider_prefix(value: object) -> str:
    """``az:B002V0QUOC`` → ``B002V0QUOC``. Non-Audible provider ids return "".

    Chaptarr prefixes provider ids with the provider that issued them, so only
    the ``az:`` ones are Amazon/Audible ASINs. A bare value is taken at face
    value — Readarr-facade responses emit ids without the prefix.
    """
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    provider, sep, rest = raw.partition(":")
    if sep:
        return rest.strip().upper() if provider.strip().lower() == "az" else ""
    return raw.upper()


def _asins_of(record: dict) -> set[str]:
    """Every Audible ASIN Chaptarr associates with one book resource."""
    found: set[str] = set()

    def take(value: object) -> None:
        asin = _strip_provider_prefix(value)
        # Audible ASINs are 10 chars; the guard keeps slugs and numeric row ids
        # from colliding with a real ASIN in the index.
        if len(asin) == 10 and asin.isalnum():
            found.add(asin)

    for key in ("asin", "audibleASIN", "foreignBookId", "foreignEditionId"):
        take(record.get(key))

    for edition in record.get("editions") or []:
        if not isinstance(edition, dict):
            continue
        for key in ("asin", "audibleASIN", "foreignEditionId"):
            take(edition.get(key))
        for extra in edition.get("asins") or []:
            take(extra)

    return found


def _record_has_file(record: dict) -> bool:
    if record.get("hasFiles"):
        return True
    stats = record.get("statistics")
    if isinstance(stats, dict) and (stats.get("bookFileCount") or 0) > 0:
        return True
    return any(
        isinstance(e, dict) and (e.get("bookFileCount") or 0) > 0
        for e in record.get("editions") or []
    )


def _index_records(records: list, index: dict[str, LibraryEntry]) -> None:
    """Fold Chaptarr book resources into the ASIN index, in place."""
    for record in records:
        if not isinstance(record, dict):
            continue
        # Chaptarr keeps separate audiobook and eBook rows. Owning the eBook is
        # no reason to skip the audiobook, so drop anything explicitly an eBook.
        # A record with no mediaType at all predates the split — keep it.
        media_type = (record.get("mediaType") or "").strip().lower()
        if media_type and media_type != MEDIA_TYPE:
            continue
        entry = LibraryEntry(
            chaptarr_book_id=record.get("id") if isinstance(record.get("id"), int) else None,
            title=(record.get("title") or "").strip(),
            has_file=_record_has_file(record),
        )
        for asin in _asins_of(record):
            # A book with a file wins over one without: the same ASIN can show up
            # on more than one row (a re-add, a split edition), and "we have it"
            # is the answer that matters.
            existing = index.get(asin)
            if existing is None or (entry.has_file and not existing.has_file):
                index[asin] = entry


async def _fetch_paged(cfg: ChaptarrConfig, index: dict[str, LibraryEntry]) -> bool:
    """Page through ``/book/paged``. Returns False if this Chaptarr has no such route."""
    offset = 0
    for _ in range(_LIBRARY_PAGE_LIMIT):
        resp = await _request(
            cfg, "GET",
            f"/book/paged?offset={offset}&pageSize={_LIBRARY_PAGE_SIZE}"
            f"&includeUnmonitored=true&mediaType={MEDIA_TYPE}",
            timeout=_LIBRARY_TIMEOUT,
        )
        try:
            body = resp.json()
        except ValueError:
            return False
        if not isinstance(body, dict) or not isinstance(body.get("records"), list):
            return False
        records = body["records"]
        _index_records(records, index)
        offset += len(records)
        if len(records) < _LIBRARY_PAGE_SIZE or offset >= (body.get("totalCount") or 0):
            return True
    return True


async def fetch_library(cfg: ChaptarrConfig) -> dict[str, LibraryEntry]:
    """Chaptarr's whole audiobook library, indexed by every ASIN it knows for it.

    Cached for ``_LIBRARY_CACHE_TTL`` seconds per Chaptarr instance so checking a
    few hundred books costs one round trip rather than a few hundred.
    """
    cached = _LIBRARY_CACHE.get(cfg.base_url)
    if cached and (time.monotonic() - cached[0]) < _LIBRARY_CACHE_TTL:
        return cached[1]

    index: dict[str, LibraryEntry] = {}
    paged_worked = False
    try:
        paged_worked = await _fetch_paged(cfg, index)
    except ChaptarrError:
        # Older Chaptarr builds have no /book/paged. Fall through to the index
        # route rather than treating a missing endpoint as an outage.
        pass

    if not paged_worked:
        # Whatever pages did land are half an answer; the index route returns
        # the whole library, so start it from scratch.
        index.clear()
        resp = await _request(
            cfg, "GET", f"/book?mediaType={MEDIA_TYPE}", timeout=_LIBRARY_TIMEOUT
        )
        try:
            records = resp.json()
        except ValueError as exc:
            raise ChaptarrError("Chaptarr's book list was not JSON") from exc
        if not isinstance(records, list):
            raise ChaptarrError("Chaptarr's book list was not a list of books")
        _index_records(records, index)

    _LIBRARY_CACHE[cfg.base_url] = (time.monotonic(), index)
    return index


def _counts_as_owned(entry: LibraryEntry, cfg: ChaptarrConfig) -> bool:
    return entry.has_file if cfg.skip_when == "has_file" else True


async def check_books(cfg: ChaptarrConfig, book_ids: list[str]) -> dict[str, dict]:
    """What Chaptarr knows about each ASIN, keyed by the ASIN we were asked about.

    Every requested id gets an entry. ``would_skip`` answers the only question the
    download paths care about, honouring ``skip_when``. Raises ``ChaptarrError``
    if the library can't be read — callers that must not block on Chaptarr use
    ``filter_new_books``, which swallows that.
    """
    index = await fetch_library(cfg)
    results: dict[str, dict] = {}
    for book_id in book_ids:
        asin = (book_id or "").strip().upper()
        entry = index.get(asin)
        results[book_id] = {
            "book_id": book_id,
            "in_chaptarr": entry is not None,
            "has_file": bool(entry and entry.has_file),
            "title": entry.title if entry else None,
            "chaptarr_book_id": entry.chaptarr_book_id if entry else None,
            "would_skip": bool(entry and _counts_as_owned(entry, cfg)),
        }
    return results


async def filter_new_books(
    cfg: ChaptarrConfig, book_ids: list[str]
) -> tuple[list[str], dict[str, dict]]:
    """Split book ids into (still worth downloading, already in Chaptarr).

    Fails open: if the check is switched off, or Chaptarr can't be reached, every
    book comes back in the download list. Losing an audiobook because a metadata
    server was down would be a far worse outcome than downloading a duplicate.
    """
    if not cfg.skip_check_active or not book_ids:
        return list(book_ids), {}

    logger = get_logger()
    try:
        results = await check_books(cfg, book_ids)
    except ChaptarrError as exc:
        logger.warning("[chaptarr] library check failed, downloading anyway: %s", exc)
        return list(book_ids), {}
    except Exception as exc:  # noqa: BLE001 — never block a download on this
        logger.warning("[chaptarr] library check failed, downloading anyway: %s", exc)
        return list(book_ids), {}

    wanted = [b for b in book_ids if not results[b]["would_skip"]]
    skipped = {b: results[b] for b in book_ids if results[b]["would_skip"]}
    if skipped:
        logger.info(
            "[chaptarr] skipping %d of %d book(s) already in Chaptarr (skip_when=%s)",
            len(skipped), len(book_ids), cfg.skip_when,
        )
    return wanted, skipped


def skip_reason(match: dict) -> str:
    """The human-readable 'why we didn't download it' line, from a check result."""
    title = match.get("title") or match.get("book_id")
    what = "already has a file for" if match.get("has_file") else "already tracks"
    return f"Chaptarr {what} “{title}” — skipped downloading it from Audible."



# ── Commands ──────────────────────────────────────────────────────────────────

async def _start_command(cfg: ChaptarrConfig, payload: dict) -> int:
    resp = await _request(cfg, "POST", "/command", json=payload)
    try:
        return int(resp.json().get("id"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ChaptarrError(f"Chaptarr did not return a command id: {resp.text[:200]}") from exc


async def _await_command(cfg: ChaptarrConfig, command_id: int) -> dict:
    """Poll a Chaptarr command to completion. Times out rather than blocking forever."""
    deadline = time.monotonic() + _COMMAND_POLL_TIMEOUT
    body: dict = {}
    while time.monotonic() < deadline:
        body = (await _request(cfg, "GET", f"/command/{command_id}")).json()
        status = (body.get("status") or "").lower()
        if status in ("completed", "failed", "aborted", "cancelled"):
            return body
        await asyncio.sleep(_COMMAND_POLL_INTERVAL)
    return body or {"status": "queued"}


def _summarize(command: dict) -> tuple[str, str]:
    """Map a finished Chaptarr command onto our own (status, message).

    Chaptarr's ``CommandResult`` is ``Unknown`` until a handler says otherwise,
    and ``Complete()`` promotes it to ``Successful``, so only an explicit
    ``Unsuccessful`` means the work failed.

    Caveat: ManualImport completes successfully even when every file was
    rejected — it never reports Unsuccessful. "complete" therefore means
    Chaptarr ran the import, not that it necessarily accepted the file;
    Chaptarr's own History/Activity view has the per-file detail.
    """
    status = (command.get("status") or "").lower()
    result = (command.get("result") or "").lower()
    message = (
        command.get("exception")
        or command.get("message")
        or command.get("commandName")
        or ""
    )
    if status == "completed":
        if result == "unsuccessful":
            return "error", message or "Chaptarr could not import the file"
        return "complete", message or "Imported"
    if status in ("failed", "aborted", "cancelled"):
        return "error", message or f"Chaptarr command {status}"
    return "running", message or "Still running in Chaptarr"


def _manual_import_payload(cfg: ChaptarrConfig, paths: list[str], match: dict) -> dict:
    files = [
        {
            "path": path,
            "foreignAuthorId": match["foreign_author_id"],
            "foreignAuthorName": match["author_name"],
            "foreignBookId": match["foreign_book_id"],
            "foreignBookTitle": match["book_title"],
            "foreignEditionId": match["foreign_edition_id"],
            "foreignEditionTitle": match["book_title"],
            # UserMetadataSuggestion — makes Chaptarr materialize the author,
            # book and edition from provider metadata instead of requiring a
            # library entry that already monitors them.
            "selectionSource": 1,
            "disableReleaseSwitching": False,
        }
        for path in paths
    ]
    return {
        "name": "ManualImport",
        "importMode": cfg.import_mode,
        "replaceExistingFiles": False,
        "files": files,
    }


def _scan_payload(cfg: ChaptarrConfig, folder: str) -> dict:
    return {
        "name": "DownloadedBooksScan",
        "path": folder,
        # Chaptarr's ImportMode enum is serialized camelCase, so these
        # single-word values match its members as-is.
        "importMode": cfg.import_mode,
        # Import even when the author is not in the library yet.
        "requireDefaultRootFolderForMissingAuthors": True,
    }


# ── Import orchestration ──────────────────────────────────────────────────────

def create_record(
    book_id: str,
    *,
    book_title: Optional[str] = None,
    user_id: Optional[int] = None,
    status: str = "running",
    message: Optional[str] = None,
) -> int:
    """Open a row for this attempt so the UI can show it while Chaptarr works."""
    with SessionLocal() as db:
        row = ChaptarrImport(
            book_id=book_id,
            book_title=book_title,
            status=status,
            message=message,
            user_id=user_id,
            completed_at=datetime.now(timezone.utc) if status != "running" else None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _finish(record_id: int, **fields) -> dict:
    with SessionLocal() as db:
        row = db.get(ChaptarrImport, record_id)
        if row is None:
            return {}
        for key, value in fields.items():
            if key == "message" and value:
                value = str(value)[:1000]
            setattr(row, key, value)
        if row.status != "running":
            row.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        return {
            "id": row.id,
            "book_id": row.book_id,
            "book_title": row.book_title,
            "status": row.status,
            "message": row.message,
            "matched_by": row.matched_by,
            "command_id": row.command_id,
            "file_path": row.file_path,
        }


def record_skipped_download(
    book_id: str,
    match: dict,
    *,
    book_title: Optional[str] = None,
    user_id: Optional[int] = None,
) -> int:
    """Log a download we didn't make, so the skip is visible rather than silent.

    Refreshes the existing row when the same book is skipped again — an
    auto-download sweep re-skips every book Chaptarr owns on every run, and one
    row per book per sweep would bury the history it is meant to explain.
    """
    title = book_title or match.get("title") or None
    with SessionLocal() as db:
        existing_id = (
            db.query(ChaptarrImport.id)
            .filter(
                ChaptarrImport.book_id == book_id,
                ChaptarrImport.matched_by == "already_in_chaptarr",
            )
            .order_by(ChaptarrImport.id.desc())
            .limit(1)
            .scalar()
        )

    if existing_id is not None:
        _finish(existing_id, status="skipped", message=skip_reason(match),
                book_title=title)
        return existing_id

    record_id = create_record(
        book_id,
        book_title=title,
        user_id=user_id,
        status="skipped",
        message=skip_reason(match),
    )
    _finish(record_id, matched_by="already_in_chaptarr", status="skipped")
    return record_id


async def import_book(
    book_id: str,
    *,
    book_title: Optional[str] = None,
    user_id: Optional[int] = None,
    cfg: Optional[ChaptarrConfig] = None,
    record_id: Optional[int] = None,
) -> dict:
    """Hand one downloaded book to Chaptarr and record the outcome.

    Never raises: every failure path is written to ``chaptarr_imports`` and
    returned, so a misconfigured Chaptarr can't break a download.
    """
    logger = get_logger()
    if cfg is None:
        with SessionLocal() as db:
            cfg = load_config(db)

    if not book_title:
        meta = libation_svc.get_book_metadata(book_id) or {}
        book_title = meta.get("title") or None

    if record_id is None:
        record_id = create_record(book_id, book_title=book_title, user_id=user_id)
    elif book_title:
        _finish(record_id, book_title=book_title, status="running")

    if not cfg.configured:
        return _finish(record_id, status="skipped",
                       message="Chaptarr is not configured.")

    paths = libation_svc.get_audio_file_paths(book_id)
    if not paths:
        logger.warning("[chaptarr] %s: no downloaded audio file found", book_id)
        return _finish(record_id, status="error",
                       message="No downloaded audio file found for this book.")

    mapped = [map_path(cfg, p) for p in paths]
    logger.info("[chaptarr] %s: importing %d file(s) → %s", book_id, len(mapped), cfg.base_url)

    try:
        match = await lookup_by_asin(cfg, book_id)
        if match:
            matched_by = "asin"
            payload = _manual_import_payload(cfg, mapped, match)
            logger.info(
                "[chaptarr] %s: matched %s by ASIN (author %s)",
                book_id, match["foreign_book_id"], match["foreign_author_id"],
            )
        else:
            # Chaptarr's metadata server doesn't know this ASIN. Fall back to
            # letting Chaptarr match the folder from tags/filenames itself.
            matched_by = "folder_scan"
            folder = mapped[0].rsplit("/", 1)[0] if "/" in mapped[0] else mapped[0]
            payload = _scan_payload(cfg, folder)
            logger.info(
                "[chaptarr] %s: ASIN unknown to Chaptarr, falling back to folder scan of %s",
                book_id, folder,
            )

        command_id = await _start_command(cfg, payload)
        _finish(record_id, status="running", matched_by=matched_by,
                command_id=command_id, file_path=mapped[0])
        command = await _await_command(cfg, command_id)
        status, message = _summarize(command)
    except ChaptarrError as exc:
        logger.error("[chaptarr] %s: %s", book_id, exc)
        return _finish(record_id, status="error", message=str(exc), file_path=mapped[0])
    except Exception as exc:  # noqa: BLE001 — never let this break a download
        logger.error("[chaptarr] %s: unexpected failure: %s", book_id, exc, exc_info=True)
        return _finish(record_id, status="error",
                       message=f"Unexpected failure: {exc}", file_path=mapped[0])

    logger.info("[chaptarr] %s: command %s → %s (%s)", book_id, command_id, status, message)
    if status == "complete":
        # Chaptarr's library just changed; the next skip-check must not answer
        # "no" for a book we have this second handed it.
        invalidate_library_cache(cfg)
    return _finish(record_id, status=status, message=message)


async def import_books(book_ids: list[str], record_ids: list[int],
                       cfg: ChaptarrConfig, user_id: Optional[int]) -> None:
    """Work a batch sequentially in the background — Chaptarr imports one at a time."""
    for book_id, record_id in zip(book_ids, record_ids):
        await import_book(book_id, user_id=user_id, cfg=cfg, record_id=record_id)


async def import_after_download(book_id: str, book_title: Optional[str],
                                user_id: Optional[int]) -> None:
    """Auto-import hook fired after a successful download. Silent when disabled."""
    with SessionLocal() as db:
        cfg = load_config(db)
    if not (cfg.enabled and cfg.auto_import and cfg.configured):
        return
    await import_book(book_id, book_title=book_title, user_id=user_id, cfg=cfg)
