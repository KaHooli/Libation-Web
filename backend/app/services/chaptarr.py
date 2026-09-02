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
}

IMPORT_MODES = ("auto", "copy", "move")

MEDIA_TYPE = "audiobook"  # Libation only ever produces audiobooks

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_COMMAND_POLL_INTERVAL = 2.0
_COMMAND_POLL_TIMEOUT = 300.0


@dataclass(frozen=True)
class ChaptarrConfig:
    enabled: bool = False
    url: str = ""
    api_key: str = ""
    import_mode: str = "auto"
    auto_import: bool = False
    path_from: str = ""
    path_to: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")


def load_config(db: Session) -> ChaptarrConfig:
    conn = db.connection()
    rows = conn.execute(
        text("SELECT key, value FROM system_settings WHERE key LIKE 'chaptarr_%'")
    ).fetchall()
    raw = {r[0]: r[1] for r in rows}
    mode = (raw.get("chaptarr_import_mode") or "auto").lower()
    return ChaptarrConfig(
        enabled=raw.get("chaptarr_enabled") == "1",
        url=(raw.get("chaptarr_url") or "").strip(),
        api_key=(raw.get("chaptarr_api_key") or "").strip(),
        import_mode=mode if mode in IMPORT_MODES else "auto",
        auto_import=raw.get("chaptarr_auto_import") == "1",
        path_from=(raw.get("chaptarr_path_from") or "").strip(),
        path_to=(raw.get("chaptarr_path_to") or "").strip(),
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
