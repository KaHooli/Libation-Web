import sqlite3
from pathlib import Path
from typing import Optional

from ..config import settings

def _db_path() -> Optional[Path]:
    """Find the Libation database — name changed from LibationData.db to LibationContext.db in v13."""
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


def _first(candidates: list[str], pool: set[str]) -> Optional[str]:
    return next((c for c in candidates if c in pool), None)


def _schema(conn) -> dict[str, set[str]]:
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    return {
        t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
        for t in tables
    }


def _enhanced_selects(bc: set[str], id_col: str) -> list[str]:
    """Extra columns added in Phase 5 — all nullable for resilience."""
    extras = []
    for col, alias in [
        (["Subtitle"], "subtitle"),
        (["ContentType"], "content_type"),
        (["Language"], "language"),
        (["IsAbridged"], "is_abridged"),
    ]:
        c = _first(col, bc)
        extras.append(f"{'b.' + c if c else 'NULL'} AS {alias}")
    return extras


def _udi_join(sc: dict, id_col: str) -> tuple[str, str, str]:
    """Returns (join_sql, status_expr, last_dl_expr) for UserDefinedItem."""
    udi = _first(["UserDefinedItem", "UserDefinedItems"], sc)
    if not udi:
        return "", "NULL AS liberate_status", "NULL AS last_downloaded"
    udc = sc[udi]
    udi_book = _first(["BookId", "AudibleProductId"], udc)
    udi_status = _first(["BookStatus", "Status", "LibrateStatus"], udc)
    udi_dl = _first(["LastDownloaded", "DateLiberated"], udc)
    if not udi_book or not udi_status:
        return "", "NULL AS liberate_status", "NULL AS last_downloaded"
    join_sql = f"LEFT JOIN {udi} u ON u.{udi_book} = b.{id_col}"
    status_expr = f"u.{udi_status} AS liberate_status"
    dl_expr = f"{'u.' + udi_dl if udi_dl else 'NULL'} AS last_downloaded"
    return join_sql, status_expr, dl_expr


def _rating_join(sc: dict, id_col: str) -> tuple[str, str]:
    """Returns (join_sql, rating_expr) for community ratings."""
    rat = _first(["Rating", "Ratings"], sc)
    if not rat:
        return "", "NULL AS community_rating"
    rc = sc[rat]
    rat_book = _first(["BookId", "AudibleProductId"], rc)
    rat_overall = _first(["OverallRating", "Overall"], rc)
    if not rat_book or not rat_overall:
        return "", "NULL AS community_rating"
    join_sql = f"LEFT JOIN {rat} r ON r.{rat_book} = b.{id_col}"
    return join_sql, f"r.{rat_overall} AS community_rating"


def count_books() -> int:
    if not db_exists():
        return 0
    try:
        conn = _connect()
        total = conn.execute("SELECT COUNT(*) FROM Books").fetchone()[0]
        conn.close()
        return total
    except Exception:
        return 0


def get_book_cover_path(book_id: str) -> Optional[Path]:
    if not db_exists():
        return None
    try:
        conn = _connect()
        sc = _schema(conn)
        if "Books" not in sc:
            conn.close()
            return None
        bc = sc["Books"]
        id_col = _first(["AudibleProductId", "BookId"], bc)
        pic_col = _first(["PictureLarge", "CoverPath", "PictureSmall"], bc)
        if not id_col or not pic_col:
            conn.close()
            return None
        row = conn.execute(
            f"SELECT {pic_col} FROM Books WHERE {id_col} = ?", (book_id,)
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        p = Path(row[0])
        if p.is_absolute() and p.exists():
            return p
        for candidate in [_DB.parent / p, _DB.parent / p.name]:
            if candidate.exists():
                return candidate
        return None
    except Exception:
        return None


def get_library(
    search: str = "",
    sort_by: str = "date_added",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
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

        bc = sc["Books"]
        id_col = _first(["AudibleProductId", "BookId"], bc)
        title_col = _first(["Title"], bc)
        length_col = _first(["LengthInMinutes", "RunTime"], bc)
        desc_col = _first(["Description", "Summary"], bc)
        pic_col = _first(["PictureLarge", "CoverPath", "PictureSmall"], bc)
        pub_col = _first(["DatePublished", "ReleaseDate"], bc)

        if not id_col:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "schema_error"}

        def col(c: Optional[str], alias: str, default: str = "NULL") -> str:
            return f"{'b.' + c if c else default} AS {alias}"

        def contrib_subq(ctype: int, alias: str) -> str:
            ct = _first(["Contributors", "Persons", "People"], sc)
            jt = _first(["BookContributors", "BookPeople", "BookPersons"], sc)
            if not ct or not jt:
                return f"NULL AS {alias}"
            jc, cc = sc[jt], sc[ct]
            j_book = _first([id_col, "BookId", "AudibleProductId"], jc)
            j_pid = _first(["ContributorId", "PersonId", "PeopleId"], jc)
            j_type = _first(["ContributorType", "RoleId", "Role", "Type"], jc)
            c_id = _first(["ContributorId", "PersonId", "Id"], cc)
            c_name = _first(["Name", "FullName"], cc)
            if not all([j_book, j_pid, j_type, c_id, c_name]):
                return f"NULL AS {alias}"
            return (
                f"(SELECT GROUP_CONCAT(p.{c_name}, ', ') FROM {jt} bc "
                f"JOIN {ct} p ON bc.{j_pid} = p.{c_id} "
                f"WHERE bc.{j_book} = b.{id_col} AND bc.{j_type} = {ctype}) AS {alias}"
            )

        def series_subqs() -> tuple[str, str]:
            bst = _first(["BookSeries", "SeriesBook"], sc)
            if "Series" not in sc or not bst:
                return "NULL AS series_name", "NULL AS series_index"
            bsc, ssc = sc[bst], sc["Series"]
            bs_book = _first([id_col, "BookId", "AudibleProductId"], bsc)
            bs_sid = _first(["SeriesId"], bsc)
            bs_idx = _first(["Index", "SeriesIndex", "Sequence", "Position"], bsc)
            s_id = _first(["SeriesId", "Id"], ssc)
            s_name = _first(["Name", "SeriesName"], ssc)
            if not all([bs_book, bs_sid, s_id, s_name]):
                return "NULL AS series_name", "NULL AS series_index"
            sn = (
                f"(SELECT s.{s_name} FROM {bst} bs "
                f"JOIN Series s ON bs.{bs_sid} = s.{s_id} "
                f"WHERE bs.{bs_book} = b.{id_col} LIMIT 1) AS series_name"
            )
            si = (
                f"(SELECT bs.{bs_idx} FROM {bst} bs "
                f"WHERE bs.{bs_book} = b.{id_col} LIMIT 1) AS series_index"
                if bs_idx else "NULL AS series_index"
            )
            return sn, si

        def date_added_subq() -> str:
            lbt = _first(["LibraryBooks", "LibraryBook"], sc)
            if not lbt:
                return "NULL AS date_added"
            lbc = sc[lbt]
            lb_book = _first([id_col, "BookId", "AudibleProductId"], lbc)
            lb_date = _first(["DateAdded", "CreatedDate", "AddedDate"], lbc)
            if not lb_book or not lb_date:
                return "NULL AS date_added"
            return (
                f"(SELECT MAX(lb.{lb_date}) FROM {lbt} lb "
                f"WHERE lb.{lb_book} = b.{id_col}) AS date_added"
            )

        sn_sql, si_sql = series_subqs()
        selects = [
            col(id_col, "book_id"),
            col(title_col, "title", "''"),
            col(length_col, "length_minutes"),
            col(desc_col, "description"),
            col(pic_col, "picture_path"),
            col(pub_col, "date_published"),
            contrib_subq(0, "authors"),
            contrib_subq(1, "narrators"),
            sn_sql,
            si_sql,
            date_added_subq(),
        ]

        where, params = "", []
        if search and title_col:
            where = f"WHERE b.{title_col} LIKE ?"
            params = [f"%{search}%"]

        sort_map = {
            "title": f"b.{title_col}" if title_col else "b.rowid",
            "date_added": "date_added",
            "length": f"b.{length_col}" if length_col else "b.rowid",
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

        return {
            "books": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": str(e)}


def get_liberate_books(
    active_downloads: dict[str, tuple[str, int | None]] | None = None,
    filter_status: str = "all",
    page: int = 1,
    page_size: int = 48,
) -> dict:
    """Return all books with liberate status overlay.

    active_downloads: {book_id: (status, progress)} from our downloads table.
    filter_status: 'all' | 'liberated' | 'not_liberated' | 'downloading'
    """
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

        bc = sc["Books"]
        id_col = _first(["AudibleProductId", "BookId"], bc)
        title_col = _first(["Title"], bc)
        length_col = _first(["LengthInMinutes", "RunTime"], bc)
        pic_col = _first(["PictureLarge", "CoverPath", "PictureSmall"], bc)
        if not id_col:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "schema_error"}

        def col(c, alias, default="NULL"):
            return f"{'b.' + c if c else default} AS {alias}"

        def contrib_subq(ctype, alias):
            ct = _first(["Contributors", "Persons", "People"], sc)
            jt = _first(["BookContributors", "BookPeople", "BookPersons"], sc)
            if not ct or not jt:
                return f"NULL AS {alias}"
            jc, cc = sc[jt], sc[ct]
            j_book = _first([id_col, "BookId", "AudibleProductId"], jc)
            j_pid = _first(["ContributorId", "PersonId", "PeopleId"], jc)
            j_type = _first(["ContributorType", "RoleId", "Role", "Type"], jc)
            c_id = _first(["ContributorId", "PersonId", "Id"], cc)
            c_name = _first(["Name", "FullName"], cc)
            if not all([j_book, j_pid, j_type, c_id, c_name]):
                return f"NULL AS {alias}"
            return (
                f"(SELECT GROUP_CONCAT(p.{c_name}, ', ') FROM {jt} bc2 "
                f"JOIN {ct} p ON bc2.{j_pid} = p.{c_id} "
                f"WHERE bc2.{j_book} = b.{id_col} AND bc2.{j_type} = {ctype}) AS {alias}"
            )

        def series_subqs():
            bst = _first(["BookSeries", "SeriesBook"], sc)
            if "Series" not in sc or not bst:
                return "NULL AS series_name", "NULL AS series_index"
            bsc, ssc = sc[bst], sc["Series"]
            bs_book = _first([id_col, "BookId", "AudibleProductId"], bsc)
            bs_sid = _first(["SeriesId"], bsc)
            bs_idx = _first(["Index", "SeriesIndex", "Sequence", "Position"], bsc)
            s_id = _first(["SeriesId", "Id"], ssc)
            s_name = _first(["Name", "SeriesName"], ssc)
            if not all([bs_book, bs_sid, s_id, s_name]):
                return "NULL AS series_name", "NULL AS series_index"
            sn = (
                f"(SELECT s.{s_name} FROM {bst} bs JOIN Series s "
                f"ON bs.{bs_sid} = s.{s_id} WHERE bs.{bs_book} = b.{id_col} LIMIT 1) AS series_name"
            )
            si = (
                f"(SELECT bs.{bs_idx} FROM {bst} bs WHERE bs.{bs_book} = b.{id_col} LIMIT 1) AS series_index"
                if bs_idx else "NULL AS series_index"
            )
            return sn, si

        def date_added_subq():
            lbt = _first(["LibraryBooks", "LibraryBook"], sc)
            if not lbt:
                return "NULL AS date_added"
            lbc = sc[lbt]
            lb_book = _first([id_col, "BookId", "AudibleProductId"], lbc)
            lb_date = _first(["DateAdded", "CreatedDate", "AddedDate"], lbc)
            if not lb_book or not lb_date:
                return "NULL AS date_added"
            return (
                f"(SELECT MAX(lb.{lb_date}) FROM {lbt} lb "
                f"WHERE lb.{lb_book} = b.{id_col}) AS date_added"
            )

        udi_join, status_expr, last_dl_expr = _udi_join(sc, id_col)
        rating_join, rating_expr = _rating_join(sc, id_col)
        sn_sql, si_sql = series_subqs()

        selects = [
            col(id_col, "book_id"),
            col(title_col, "title", "''"),
            col(length_col, "length_minutes"),
            col(pic_col, "picture_path"),
            contrib_subq(0, "authors"),
            contrib_subq(1, "narrators"),
            sn_sql, si_sql,
            date_added_subq(),
            status_expr,
            last_dl_expr,
            rating_expr,
            *_enhanced_selects(bc, id_col),
        ]

        sql = (
            f"SELECT {', '.join(selects)} FROM Books b "
            f"{udi_join} {rating_join} "
            f"ORDER BY date_added DESC NULLS LAST LIMIT ? OFFSET ?"
        )
        total_sql = f"SELECT COUNT(*) FROM Books b"
        total = conn.execute(total_sql).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(sql, [page_size, offset]).fetchall()
        conn.close()

        books = []
        for r in rows:
            d = dict(r)
            bid = d.get("book_id", "")
            # Determine status: our active downloads table takes priority
            if bid in active:
                raw_status, progress = active[bid]
                d["liberate_status"] = "downloading"
                d["download_progress"] = progress
            else:
                raw = d.get("liberate_status")
                if raw == 1:
                    d["liberate_status"] = "liberated"
                elif raw == 2:
                    d["liberate_status"] = "error"
                else:
                    d["liberate_status"] = "not_liberated"
                d["download_progress"] = None

            # ContentType int → string
            ct = d.get("content_type")
            ct_map = {0: "Unknown", 1: "Product", 2: "Episode", 3: "Parent"}
            d["content_type"] = ct_map.get(ct, "Product") if ct is not None else "Product"

            books.append(d)

        # Apply filter after enrichment
        if filter_status != "all":
            books = [b for b in books if b["liberate_status"] == filter_status]

        return {"books": books, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": str(e)}


def get_books_by_account(
    account_id: str,
    search: str = "",
    page: int = 1,
    page_size: int = 48,
) -> dict:
    """Return books belonging to a specific Audible account (LibraryBooks.Account)."""
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

        bc = sc["Books"]
        id_col = _first(["AudibleProductId", "BookId"], bc)
        title_col = _first(["Title"], bc)
        if not id_col:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "schema_error"}

        # Find LibraryBooks table and Account column
        lbt = _first(["LibraryBooks", "LibraryBook"], sc)
        if not lbt:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "no_library_table"}
        lbc = sc[lbt]
        lb_book = _first([id_col, "BookId", "AudibleProductId"], lbc)
        lb_account = _first(["Account", "AccountId", "CustomerId"], lbc)
        lb_date = _first(["DateAdded", "CreatedDate", "AddedDate"], lbc)
        if not lb_book or not lb_account:
            conn.close()
            return {"books": [], "total": 0, "page": page, "page_size": page_size,
                    "empty_reason": "no_account_column"}

        def col(c, alias, default="NULL"):
            return f"{'b.' + c if c else default} AS {alias}"

        def contrib_subq(ctype, alias):
            ct = _first(["Contributors", "Persons", "People"], sc)
            jt = _first(["BookContributors", "BookPeople", "BookPersons"], sc)
            if not ct or not jt:
                return f"NULL AS {alias}"
            jc, cc = sc[jt], sc[ct]
            j_book = _first([id_col, "BookId", "AudibleProductId"], jc)
            j_pid = _first(["ContributorId", "PersonId", "PeopleId"], jc)
            j_type = _first(["ContributorType", "RoleId", "Role", "Type"], jc)
            c_id = _first(["ContributorId", "PersonId", "Id"], cc)
            c_name = _first(["Name", "FullName"], cc)
            if not all([j_book, j_pid, j_type, c_id, c_name]):
                return f"NULL AS {alias}"
            return (
                f"(SELECT GROUP_CONCAT(p.{c_name}, ', ') FROM {jt} bc2 "
                f"JOIN {ct} p ON bc2.{j_pid} = p.{c_id} "
                f"WHERE bc2.{j_book} = b.{id_col} AND bc2.{j_type} = {ctype}) AS {alias}"
            )

        def series_subqs():
            bst = _first(["BookSeries", "SeriesBook"], sc)
            if "Series" not in sc or not bst:
                return "NULL AS series_name", "NULL AS series_index"
            bsc, ssc = sc[bst], sc["Series"]
            bs_book = _first([id_col, "BookId", "AudibleProductId"], bsc)
            bs_sid = _first(["SeriesId"], bsc)
            bs_idx = _first(["Index", "SeriesIndex", "Sequence", "Position"], bsc)
            s_id = _first(["SeriesId", "Id"], ssc)
            s_name = _first(["Name", "SeriesName"], ssc)
            if not all([bs_book, bs_sid, s_id, s_name]):
                return "NULL AS series_name", "NULL AS series_index"
            sn = (
                f"(SELECT s.{s_name} FROM {bst} bs JOIN Series s "
                f"ON bs.{bs_sid} = s.{s_id} WHERE bs.{bs_book} = b.{id_col} LIMIT 1) AS series_name"
            )
            si = (
                f"(SELECT bs.{bs_idx} FROM {bst} bs WHERE bs.{bs_book} = b.{id_col} LIMIT 1) AS series_index"
                if bs_idx else "NULL AS series_index"
            )
            return sn, si

        length_col = _first(["LengthInMinutes", "RunTime"], bc)
        pic_col = _first(["PictureLarge", "CoverPath", "PictureSmall"], bc)
        desc_col = _first(["Description", "Summary"], bc)
        pub_col = _first(["DatePublished", "ReleaseDate"], bc)
        sn_sql, si_sql = series_subqs()

        date_added_expr = (
            f"(SELECT lb2.{lb_date} FROM {lbt} lb2 WHERE lb2.{lb_book} = b.{id_col} "
            f"AND lb2.{lb_account} = ? LIMIT 1) AS date_added"
            if lb_date else "NULL AS date_added"
        )

        selects = [
            col(id_col, "book_id"),
            col(title_col, "title", "''"),
            col(length_col, "length_minutes"),
            col(desc_col, "description"),
            col(pic_col, "picture_path"),
            col(pub_col, "date_published"),
            contrib_subq(0, "authors"),
            contrib_subq(1, "narrators"),
            sn_sql, si_sql,
            date_added_expr,
        ]

        where_parts = [f"EXISTS (SELECT 1 FROM {lbt} lb WHERE lb.{lb_book} = b.{id_col} AND lb.{lb_account} = ?)"]
        params: list = [account_id, account_id]

        if search and title_col:
            where_parts.append(f"b.{title_col} LIKE ?")
            params.append(f"%{search}%")

        where = "WHERE " + " AND ".join(where_parts)
        total = conn.execute(f"SELECT COUNT(*) FROM Books b {where}", params).fetchone()[0]
        offset = (page - 1) * page_size
        sql = (
            f"SELECT {', '.join(selects)} FROM Books b {where} "
            f"ORDER BY date_added DESC NULLS LAST LIMIT ? OFFSET ?"
        )
        rows = conn.execute(sql, params + [page_size, offset]).fetchall()
        conn.close()
        return {"books": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        return {"books": [], "total": 0, "page": page, "page_size": page_size,
                "empty_reason": str(e)}
