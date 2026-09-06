import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from ..config import settings


# ── DB connection ─────────────────────────────────────────────────────────────

def _db_path() -> Optional[Path]:
    config = Path(settings.LIBATION_CONFIG)
    for name in ("LibationContext.db", "LibationData.db"):
        p = config / name
        if p.exists():
            return p
    return None


def db_exists() -> bool:
    return _db_path() is not None


def _connect():
    path = _db_path()
    if not path:
        raise FileNotFoundError("Libation database not found in /config")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _schema(conn) -> dict[str, set[str]]:
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    return {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()} for t in tables}


def _is_v13(sc: dict) -> bool:
    """v13 uses integer BookId as PK + separate AudibleProductId in Books."""
    bc = sc.get("Books", set())
    lbc = sc.get("LibraryBooks", set())
    return "BookId" in bc and "AudibleProductId" in bc and "BookId" in lbc


# ── Cover art ─────────────────────────────────────────────────────────────────

def get_cover_image_hash(book_id: str) -> Optional[str]:
    """Return the PictureLarge hash for v13 (used to build Amazon CDN URL)."""
    if not db_exists():
        return None
    try:
        conn = _connect()
        sc = _schema(conn)
        if "Books" not in sc:
            conn.close()
            return None
        if _is_v13(sc):
            row = conn.execute(
                "SELECT PictureLarge FROM Books WHERE AudibleProductId = ?", (book_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT PictureLarge FROM Books WHERE AudibleProductId = ?", (book_id,)
            ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def get_book_cover_path(book_id: str) -> Optional[Path]:
    """Return local cover file path if it exists (v12 style)."""
    if not db_exists():
        return None
    try:
        conn = _connect()
        sc = _schema(conn)
        if "Books" not in sc or _is_v13(sc):
            conn.close()
            return None
        row = conn.execute(
            "SELECT PictureLarge FROM Books WHERE AudibleProductId = ?", (book_id,)
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        p = Path(row[0])
        if p.is_absolute() and p.exists():
            return p
        return None
    except Exception:
        return None


def count_books() -> int:
    if not db_exists():
        return 0
    try:
        conn = _connect()
        n = conn.execute("SELECT COUNT(*) FROM Books").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


# ── V13 query helpers ─────────────────────────────────────────────────────────

def _v13_selects(include_description=True, date_added_account: Optional[str] = None) -> list[str]:
    """Standard SELECT expressions for v13 schema."""
    selects = [
        "b.AudibleProductId AS book_id",
        "b.Title AS title",
        "b.LengthInMinutes AS length_minutes",
        "b.Subtitle AS subtitle",
        "b.Language AS language",
        "b.IsAbridged AS is_abridged",
        "b.ContentType AS content_type",
        "b.PictureLarge AS picture_id",
        "b.DatePublished AS date_published",
        "b.Rating_OverallRating AS community_rating",
        # Authors (Role=1)
        "(SELECT GROUP_CONCAT(c.Name, ', ') FROM BookContributor bc "
        " JOIN Contributors c ON bc.ContributorId=c.ContributorId "
        " WHERE bc.BookId=b.BookId AND bc.Role=1 AND c.Name != '') AS authors",
        # Narrators (Role=2)
        "(SELECT GROUP_CONCAT(c.Name, ', ') FROM BookContributor bc "
        " JOIN Contributors c ON bc.ContributorId=c.ContributorId "
        " WHERE bc.BookId=b.BookId AND bc.Role=2 AND c.Name != '') AS narrators",
        # Series name
        "(SELECT s.Name FROM SeriesBook sb JOIN Series s ON sb.SeriesId=s.SeriesId "
        " WHERE sb.BookId=b.BookId LIMIT 1) AS series_name",
        # Series index ("Order" is a reserved word — must be quoted)
        '(SELECT sb."Order" FROM SeriesBook sb WHERE sb.BookId=b.BookId LIMIT 1) AS series_index',
    ]
    if include_description:
        selects.append("b.Description AS description")
    if date_added_account is not None:
        selects.append(
            "(SELECT lb.DateAdded FROM LibraryBooks lb "
            " WHERE lb.BookId=b.BookId AND lb.Account=? LIMIT 1) AS date_added"
        )
    else:
        selects.append(
            "(SELECT MAX(lb.DateAdded) FROM LibraryBooks lb WHERE lb.BookId=b.BookId) AS date_added"
        )
    selects.append(
        "(SELECT lb.IsAudiblePlus FROM LibraryBooks lb WHERE lb.BookId=b.BookId LIMIT 1) AS is_audible_plus"
    )
    return selects


def _v13_row_to_book(r: sqlite3.Row) -> dict:
    d = dict(r)
    ct_map = {1: "Product", 2: "Episode", 3: "Parent"}
    ct = d.get("content_type")
    d["content_type"] = ct_map.get(ct, "Product") if ct is not None else "Product"
    d["is_abridged"] = bool(d.get("is_abridged"))
    d["is_audible_plus"] = bool(d.get("is_audible_plus"))
    return d


# ── Liberate filtering ────────────────────────────────────────────────────────
#
# The status shown on the Liberate page is derived, not stored: it comes from
# UserDefinedItem.BookStatus, overlaid with any download that is currently
# queued or running. The filter has to reproduce that derivation *in SQL*, in
# one place shared by the grid and by "Select All" — filtering a page in Python
# after LIMIT/OFFSET leaves `total` counting the books the filter removed, so
# the count, the page links and the selection all disagree.

# Both mirror the corresponding expressions in `_v13_selects` / the raw_status
# select below, so a filter can never disagree with the badge on the row.
_RAW_STATUS = (
    "COALESCE((SELECT u.BookStatus FROM UserDefinedItem u WHERE u.BookId=b.BookId), 0)"
)
_IS_AUDIBLE_PLUS = (
    "COALESCE((SELECT lb.IsAudiblePlus FROM LibraryBooks lb "
    "WHERE lb.BookId=b.BookId LIMIT 1), 0)"
)

# `_v13_row_to_book` renders BookStatus 1 as liberated and 2 as error; every
# other value falls through to not_liberated.
_STATUS_LIBERATED, _STATUS_ERROR = 1, 2

# Tabs that name a download state, and so have to yield to a download in flight.
# Ownership tabs (audible_plus / purchased) are a different axis and must not.
_DOWNLOAD_STATE_TABS = frozenset({"liberated", "not_liberated", "error"})


def _liberate_filter(
    filter_status: str, active_ids: Optional[Iterable[str]] = None
) -> list[tuple[str, list]]:
    """WHERE fragments for one Liberate filter tab, each with its own params.

    Returned in order so a caller can append fragments and extend its params
    list in lockstep. An unrecognised tab matches nothing rather than silently
    matching everything — the latter is how a bad filter would hand the whole
    library to a bulk download.
    """
    # Drop blanks: these are `downloads.book_id` values, and one NULL row would
    # otherwise fail the sort and take the whole query down with it.
    active = sorted({i for i in (active_ids or ()) if i})
    marks = ", ".join(["?"] * len(active))

    if filter_status == "all":
        return []
    if filter_status == "downloading":
        # No active downloads means nothing is downloading, so match nothing.
        return [(f"b.AudibleProductId IN ({marks})", active)] if active else [("1 = 0", [])]

    if filter_status == "liberated":
        parts = [(f"{_RAW_STATUS} = {_STATUS_LIBERATED}", [])]
    elif filter_status == "not_liberated":
        parts = [(f"{_RAW_STATUS} NOT IN ({_STATUS_LIBERATED}, {_STATUS_ERROR})", [])]
    elif filter_status == "error":
        parts = [(f"{_RAW_STATUS} = {_STATUS_ERROR}", [])]
    elif filter_status == "audible_plus":
        parts = [(f"{_IS_AUDIBLE_PLUS} = 1", [])]
    elif filter_status == "purchased":
        parts = [(f"{_IS_AUDIBLE_PLUS} = 0", [])]
    else:
        return [("1 = 0", [])]

    if active and filter_status in _DOWNLOAD_STATE_TABS:
        parts.append((f"b.AudibleProductId NOT IN ({marks})", active))
    return parts


# ── Public query functions ────────────────────────────────────────────────────

def get_library(
    search: str = "",
    sort_by: str = "date_added",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
    account_id: Optional[str] = None,
) -> dict:
    if not db_exists():
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": "no_accounts"}
    try:
        conn = _connect()
        sc = _schema(conn)
        if "Books" not in sc:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "no_library"}

        if _is_v13(sc):
            selects = _v13_selects(include_description=True)
            where_parts: list[str] = []
            params: list = []
            if account_id:
                where_parts.append(
                    "EXISTS (SELECT 1 FROM LibraryBooks lb WHERE lb.BookId=b.BookId AND lb.Account=?)"
                )
                params.append(account_id)
            if search:
                where_parts.append("b.Title LIKE ?")
                params.append(f"%{search}%")
            where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

            sort_map = {
                "title": "b.Title",
                "date_added": "date_added",
                "length": "b.LengthInMinutes",
            }
            order_col = sort_map.get(sort_by, "date_added")
            order_dir = "DESC" if sort_dir == "desc" else "ASC"

            total = conn.execute(
                f"SELECT COUNT(*) FROM Books b {where}", params
            ).fetchone()[0]
            offset = (page - 1) * page_size
            sql = (
                f"SELECT {', '.join(selects)} FROM Books b {where} "
                f"ORDER BY {order_col} {order_dir} NULLS LAST LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, params + [page_size, offset]).fetchall()
            conn.close()
            books = [_v13_row_to_book(r) for r in rows]
        else:
            conn.close()
            return _legacy_library(search, sort_by, sort_dir, page, page_size)

        return {"books": books, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": str(e)}


def get_liberate_books(
    active_downloads: Optional[dict] = None,
    filter_status: str = "all",
    page: int = 1,
    page_size: int = 48,
    account_id: Optional[str] = None,
    search: str = "",
) -> dict:
    if not db_exists():
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": "no_accounts"}
    active = active_downloads or {}
    try:
        conn = _connect()
        sc = _schema(conn)
        if "Books" not in sc:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "no_library"}

        if _is_v13(sc):
            selects = _v13_selects(include_description=False)
            selects.append(f"{_RAW_STATUS} AS raw_status")
            where_parts: list[str] = []
            base_params: list = []
            if account_id:
                where_parts.append("EXISTS (SELECT 1 FROM LibraryBooks lb WHERE lb.BookId=b.BookId AND lb.Account=?)")
                base_params.append(account_id)
            if search:
                where_parts.append("b.Title LIKE ?")
                base_params.append(f"%{search}%")
            for fragment, fragment_params in _liberate_filter(filter_status, active):
                where_parts.append(fragment)
                base_params.extend(fragment_params)
            where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
            total = conn.execute(f"SELECT COUNT(*) FROM Books b {where}", base_params).fetchone()[0]
            offset = (page - 1) * page_size
            sql = (
                f"SELECT {', '.join(selects)} FROM Books b "
                f"{where} ORDER BY date_added DESC NULLS LAST LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, base_params + [page_size, offset]).fetchall()
            conn.close()

            books = []
            for r in rows:
                d = _v13_row_to_book(r)
                raw = d.pop("raw_status", 0)
                bid = d["book_id"]
                if bid in active:
                    _, progress = active[bid]
                    d["liberate_status"] = "downloading"
                    d["download_progress"] = progress
                else:
                    d["liberate_status"] = {
                        _STATUS_LIBERATED: "liberated", _STATUS_ERROR: "error",
                    }.get(raw, "not_liberated")
                    d["download_progress"] = None
                books.append(d)

            return {"books": books, "total": total, "page": page, "page_size": page_size}
        else:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "legacy_db_not_supported"}
    except Exception as e:
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": str(e)}


def get_books_by_account(
    account_id: str,
    search: str = "",
    page: int = 1,
    page_size: int = 48,
) -> dict:
    if not db_exists():
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": "no_accounts"}
    try:
        conn = _connect()
        sc = _schema(conn)
        if "Books" not in sc:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "no_library"}

        if _is_v13(sc):
            selects = _v13_selects(include_description=True, date_added_account=account_id)
            params: list = [account_id]  # for date_added subquery

            where_parts = [
                "EXISTS (SELECT 1 FROM LibraryBooks lb WHERE lb.BookId=b.BookId AND lb.Account=?)"
            ]
            params.append(account_id)

            if search:
                where_parts.append("b.Title LIKE ?")
                params.append(f"%{search}%")

            where = "WHERE " + " AND ".join(where_parts)
            total = conn.execute(
                f"SELECT COUNT(*) FROM Books b {where}", params[1:]
            ).fetchone()[0]
            offset = (page - 1) * page_size
            sql = (
                f"SELECT {', '.join(selects)} FROM Books b {where} "
                f"ORDER BY date_added DESC NULLS LAST LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, params + [page_size, offset]).fetchall()
            conn.close()
            books = [_v13_row_to_book(r) for r in rows]
            return {"books": books, "total": total, "page": page, "page_size": page_size}
        else:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "legacy_db_not_supported"}
    except Exception as e:
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": str(e)}


# ── Bulk ID query (for select-all across pages) ───────────────────────────────

def get_liberate_book_ids(
    filter_status: str = "all",
    account_id: Optional[str] = None,
    active_download_ids: Optional[set] = None,
    search: str = "",
) -> list:
    """Return every AudibleProductId matching the filter, with no pagination.

    Shares `_liberate_filter` with `get_liberate_books`, so "Select All (N)"
    cannot select a different set from the one the grid is showing.
    """
    if not db_exists():
        return []
    try:
        conn = _connect()
        sc = _schema(conn)
        if "Books" not in sc or not _is_v13(sc):
            conn.close()
            return []

        where_parts: list[str] = []
        params: list = []

        if account_id:
            where_parts.append(
                "EXISTS (SELECT 1 FROM LibraryBooks lb WHERE lb.BookId=b.BookId AND lb.Account=?)"
            )
            params.append(account_id)

        if search:
            where_parts.append("b.Title LIKE ?")
            params.append(f"%{search}%")

        for fragment, fragment_params in _liberate_filter(filter_status, active_download_ids):
            where_parts.append(fragment)
            params.extend(fragment_params)

        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        rows = conn.execute(
            f"SELECT b.AudibleProductId FROM Books b {where}", params
        ).fetchall()
        conn.close()

        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


# ── Book status mutation ──────────────────────────────────────────────────────

def set_book_status(book_id: str, liberated: bool) -> bool:
    """
    Write BookStatus into UserDefinedItem so LibationCLI sees the book as
    already liberated (1) or resets it to not-liberated (0).
    Creates the row with safe defaults if it doesn't exist yet.
    """
    if not db_exists():
        return False
    try:
        conn = _connect()
        # Resolve AudibleProductId → integer BookId
        row = conn.execute(
            "SELECT BookId FROM Books WHERE AudibleProductId = ?", (book_id,)
        ).fetchone()
        if not row:
            conn.close()
            return False
        int_book_id = row[0]
        new_status = 1 if liberated else 0

        existing = conn.execute(
            "SELECT BookId FROM UserDefinedItem WHERE BookId = ?", (int_book_id,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE UserDefinedItem SET BookStatus = ? WHERE BookId = ?",
                (new_status, int_book_id),
            )
        else:
            # Provide all NOT NULL columns; nullable ones are omitted (default NULL)
            conn.execute(
                "INSERT INTO UserDefinedItem "
                "(BookId, BookStatus, IsFinished, Rating_OverallRating, "
                " Rating_PerformanceRating, Rating_StoryRating, Tags) "
                "VALUES (?, ?, 0, 0.0, 0.0, 0.0, '')",
                (int_book_id, new_status),
            )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# ── Legacy v12 library (kept for backwards compatibility) ─────────────────────

def _first(candidates: list[str], pool: set[str]) -> Optional[str]:
    return next((c for c in candidates if c in pool), None)


def _legacy_library(search, sort_by, sort_dir, page, page_size) -> dict:
    try:
        conn = _connect()
        sc = _schema(conn)
        bc = sc.get("Books", set())
        id_col = _first(["AudibleProductId"], bc)
        title_col = _first(["Title"], bc)
        length_col = _first(["LengthInMinutes", "RunTime"], bc)
        desc_col = _first(["Description", "Summary"], bc)
        pic_col = _first(["PictureLarge"], bc)
        pub_col = _first(["DatePublished", "ReleaseDate"], bc)
        if not id_col:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "schema_error"}

        def c(col, alias, default="NULL"):
            return f"{'b.' + col if col else default} AS {alias}"

        selects = [
            c(id_col, "book_id"), c(title_col, "title", "''"),
            c(length_col, "length_minutes"), c(desc_col, "description"),
            c(pic_col, "picture_id"), c(pub_col, "date_published"),
            "NULL AS authors", "NULL AS narrators",
            "NULL AS series_name", "NULL AS series_index", "NULL AS date_added",
        ]

        params: list = []
        where = ""
        if search and title_col:
            where = f"WHERE b.{title_col} LIKE ?"
            params.append(f"%{search}%")

        order_dir = "DESC" if sort_dir == "desc" else "ASC"
        total = conn.execute(f"SELECT COUNT(*) FROM Books b {where}", params).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT {', '.join(selects)} FROM Books b {where} "
            f"ORDER BY b.{title_col or 'rowid'} {order_dir} NULLS LAST LIMIT ? OFFSET ?",
            params + [page_size, offset]
        ).fetchall()
        conn.close()
        return {"books": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        return {"books": [], "total": 0, "page": page, "page_size": page_size, "empty_reason": str(e)}


# ── Downloaded file locations ─────────────────────────────────────────────────
#
# Libation records where it wrote each file in a JSON cache next to its config
# (`LibationFileManager.FilePathCache`). Reading that is far more reliable than
# guessing at the file naming template, which is user-configurable.
#
# Shape:  {"Dictionary": {"<ASIN>": [{"Id","FileType","Path"}, ...]}}
# FileType is the `LibationFileManager.FileType` enum. Newtonsoft writes it as
# its ordinal, but tolerate the name too in case that ever changes.

_FILE_CACHE_NAMES = ("FileLocationsV2.json", "FileLocations.json")
_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".m4b", ".mp3", ".mp4", ".ogg"}
_FILE_TYPE_AUDIO = (1, "Audio", "audio")


def _file_cache_path() -> Optional[Path]:
    config = Path(settings.LIBATION_CONFIG)
    for name in _FILE_CACHE_NAMES:
        p = config / name
        if p.exists():
            return p
    return None


def _cache_entries(raw: dict, book_id: str) -> list[dict]:
    """Pull the entry list for one book id out of the cache, whatever it's nested under."""
    if not isinstance(raw, dict):
        return []
    buckets = [raw.get("Dictionary"), raw]
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        for key, entries in bucket.items():
            if key.lower() == book_id.lower() and isinstance(entries, list):
                return [e for e in entries if isinstance(e, dict)]
    return []


def _is_audio(entry: dict) -> bool:
    if entry.get("FileType") in _FILE_TYPE_AUDIO:
        return True
    path = entry.get("Path")
    return bool(path) and Path(path).suffix.lower() in _AUDIO_EXTENSIONS


_DIGIT_RUN = re.compile(r"(\d+)")


def _natural_key(path: str) -> tuple:
    """Sort key that reads runs of digits as numbers.

    Neither the file cache nor the directory walk carries a part index, so the
    order has to come out of the path — and plain lexical order puts "Part 10"
    before "Part 2". Chaptarr imports the list in the order it is given, so a
    multi-part or chapter-split book lands out of sequence.

    The leading flag makes every key element the same shape, so a numeric run is
    never compared against a text one.
    """
    return tuple(
        (1, int(token), "") if token.isdigit() else (0, 0, token.lower())
        for token in _DIGIT_RUN.split(path)
    )


def get_audio_file_paths(book_id: str) -> list[str]:
    """Absolute paths of the audio files Libation has written for a book.

    Returns [] when the book has never been downloaded (or the files have since
    been deleted — paths that no longer exist are dropped).
    """
    cache = _file_cache_path()
    paths: list[str] = []
    if cache is not None:
        try:
            raw = json.loads(cache.read_text(encoding="utf-8-sig"))
        except Exception:
            raw = {}
        for entry in _cache_entries(raw, book_id):
            path = entry.get("Path")
            if path and _is_audio(entry) and Path(path).is_file():
                paths.append(str(path))

    if not paths:
        paths = _scan_books_dir_for(book_id)

    # Stable order so repeated imports send the same file list, and part order
    # so a book that arrived in pieces is not reassembled out of sequence.
    return sorted(dict.fromkeys(paths), key=_natural_key)


def _scan_books_dir_for(book_id: str) -> list[str]:
    """Fallback for books downloaded before the cache existed (or after it was pruned).

    Libation's default file template embeds the ASIN in the file name, so a
    bounded glob over the books directory usually finds it.
    """
    books_dir = Path(settings.AUDIOBOOKS_DIR)
    if not book_id or not books_dir.is_dir():
        return []
    try:
        return [
            str(p) for p in books_dir.rglob(f"*{book_id}*")
            if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS
        ]
    except OSError:
        return []


def get_book_metadata(book_id: str) -> Optional[dict]:
    """Title/authors/narrators/series for one book — the details sent to Chaptarr."""
    if not db_exists():
        return None
    try:
        conn = _connect()
        sc = _schema(conn)
        if "Books" not in sc or not _is_v13(sc):
            conn.close()
            return None
        selects = _v13_selects(include_description=False)
        row = conn.execute(
            f"SELECT {', '.join(selects)} FROM Books b WHERE b.AudibleProductId = ?",
            (book_id,),
        ).fetchone()
        conn.close()
        return _v13_row_to_book(row) if row else None
    except Exception:
        return None
