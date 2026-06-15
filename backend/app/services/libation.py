import sqlite3
from pathlib import Path
from typing import Optional

from ..config import settings

_DB = Path(settings.LIBATION_CONFIG) / "LibationData.db"


def db_exists() -> bool:
    return _DB.exists()


def _connect():
    conn = sqlite3.connect(str(_DB))
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
