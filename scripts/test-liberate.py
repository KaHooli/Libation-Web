#!/usr/bin/env python3
"""Liberate filtering and downloaded-file ordering, against a synthetic library.

Builds a throwaway Libation v13 `LibationContext.db` and asserts the two things
the Liberate page depends on being consistent:

**Filtering.** The grid (`get_liberate_books`) and "Select All" across pages
(`get_liberate_book_ids`) must agree — same books, same count. They used to
disagree whenever a filter tab was active: the grid counted `total` from the
unfiltered query, applied LIMIT/OFFSET, then dropped rows in Python, so the
count included books the filter had removed and the page links ran off the end.
Both now share one SQL predicate, and these checks page through every tab
asserting the count, the pages and the id list line up.

**File order.** `get_audio_file_paths` feeds Chaptarr the file list in the order
it returns it, and neither Libation's file cache nor the directory fallback
carries a part index — so the order comes out of the path, and plain lexical
order puts "Part 10" before "Part 2".

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-liberate.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="liberate-test-"))
CONFIG = WORKDIR / "config"
BOOKS = WORKDIR / "audiobooks"
for d in (CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

# Must be set before `app.config` is imported.
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{WORKDIR / 'app.db'}")
os.environ.setdefault("SECRET_KEY", "liberate-test-only-not-a-real-secret")

# Same guard the other scripts carry: the service must confine itself to the
# directories its settings name, so a hardcoded /config merely happens to work
# on a root dev box while failing on an unprivileged host.
PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}

ACCOUNT_A = "acct-alpha"
ACCOUNT_B = "acct-beta"

# BookStatus values as Libation stores them, and the status each renders as.
NOT_LIBERATED, LIBERATED, ERRORED = 0, 1, 2

PAGE_SIZE = 4  # small enough that every tab spans several pages


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"service created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


# ── Fixture ───────────────────────────────────────────────────────────────────

def build_library() -> list[dict]:
    """A v13 LibationContext.db covering every combination the tabs slice on.

    Returns the expected shape of each book, which the assertions below compare
    the service's answers against.
    """
    db = CONFIG / "LibationContext.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE Books (
            BookId INTEGER PRIMARY KEY, AudibleProductId TEXT, Title TEXT,
            Subtitle TEXT, LengthInMinutes INTEGER, Language TEXT,
            IsAbridged INTEGER, ContentType INTEGER, PictureLarge TEXT,
            DatePublished TEXT, Rating_OverallRating REAL, Description TEXT
        );
        CREATE TABLE LibraryBooks (
            BookId INTEGER, Account TEXT, DateAdded TEXT, IsAudiblePlus INTEGER
        );
        CREATE TABLE UserDefinedItem (
            BookId INTEGER PRIMARY KEY, BookStatus INTEGER, IsFinished INTEGER,
            Rating_OverallRating REAL, Rating_PerformanceRating REAL,
            Rating_StoryRating REAL, Tags TEXT
        );
        CREATE TABLE BookContributor (BookId INTEGER, ContributorId INTEGER, Role INTEGER);
        CREATE TABLE Contributors (ContributorId INTEGER PRIMARY KEY, Name TEXT);
        CREATE TABLE SeriesBook (BookId INTEGER, SeriesId INTEGER, "Order" INTEGER);
        CREATE TABLE Series (SeriesId INTEGER PRIMARY KEY, Name TEXT);
        """
    )

    expected: list[dict] = []
    for i in range(1, 26):
        asin = f"B{i:09d}"
        # Rotate through the three stored statuses, and give every fourth book
        # no UserDefinedItem row at all — the COALESCE case, which must read as
        # not_liberated rather than dropping the book from every tab.
        status = [NOT_LIBERATED, LIBERATED, ERRORED][i % 3]
        has_udi = i % 4 != 0
        account = ACCOUNT_A if i <= 15 else ACCOUNT_B
        is_plus = i % 2
        title = f"{'Quest' if i % 5 else 'Saga'} {i:02d}"

        conn.execute(
            "INSERT INTO Books (BookId, AudibleProductId, Title, LengthInMinutes, "
            "ContentType, IsAbridged, Rating_OverallRating) VALUES (?, ?, ?, ?, 1, 0, 4.5)",
            (i, asin, title, 100 + i),
        )
        conn.execute(
            "INSERT INTO LibraryBooks (BookId, Account, DateAdded, IsAudiblePlus) "
            "VALUES (?, ?, ?, ?)",
            (i, account, f"2024-01-{i:02d}T00:00:00", is_plus),
        )
        if has_udi:
            conn.execute(
                "INSERT INTO UserDefinedItem (BookId, BookStatus, IsFinished, "
                "Rating_OverallRating, Rating_PerformanceRating, Rating_StoryRating, Tags) "
                "VALUES (?, ?, 0, 0.0, 0.0, 0.0, '')",
                (i, status),
            )
        expected.append({
            "book_id": asin,
            "title": title,
            "account": account,
            "is_audible_plus": bool(is_plus),
            "stored_status": status if has_udi else NOT_LIBERATED,
        })

    # A book held on both accounts, Audible Plus on one and purchased on the
    # other. The tabs must still partition the library: an EXISTS-based filter
    # puts this book in *both* Plus and Purchased, and then the two tabs add up
    # to more than the library.
    both = "B000000026"
    conn.execute(
        "INSERT INTO Books (BookId, AudibleProductId, Title, LengthInMinutes, "
        "ContentType, IsAbridged, Rating_OverallRating) VALUES (26, ?, ?, 300, 1, 0, 4.0)",
        (both, "Quest 26 Shared"),
    )
    conn.execute(
        "INSERT INTO LibraryBooks (BookId, Account, DateAdded, IsAudiblePlus) "
        "VALUES (26, ?, '2024-01-26T00:00:00', 1)", (ACCOUNT_A,),
    )
    conn.execute(
        "INSERT INTO LibraryBooks (BookId, Account, DateAdded, IsAudiblePlus) "
        "VALUES (26, ?, '2024-01-26T00:00:00', 0)", (ACCOUNT_B,),
    )
    conn.execute(
        "INSERT INTO UserDefinedItem (BookId, BookStatus, IsFinished, "
        "Rating_OverallRating, Rating_PerformanceRating, Rating_StoryRating, Tags) "
        "VALUES (26, 0, 0, 0.0, 0.0, 0.0, '')"
    )
    expected.append({
        "book_id": both, "title": "Quest 26 Shared", "account": None,
        "is_audible_plus": None, "stored_status": NOT_LIBERATED,
    })

    conn.commit()
    conn.close()
    return expected


# ── Filter consistency ────────────────────────────────────────────────────────

def collect_pages(lib, active_map, **kwargs) -> tuple[list[str], int, list[int]]:
    """Page through the grid, returning the ids seen, the reported total, and
    the size of each page."""
    ids: list[str] = []
    sizes: list[int] = []
    total = None
    page = 1
    while True:
        result = lib.get_liberate_books(
            active_downloads=active_map, page=page, page_size=PAGE_SIZE, **kwargs
        )
        assert "empty_reason" not in result, result
        if total is None:
            total = result["total"]
        assert result["total"] == total, (
            f"total changed between pages: {total} then {result['total']}"
        )
        ids.extend(b["book_id"] for b in result["books"])
        sizes.append(len(result["books"]))
        if page * PAGE_SIZE >= total:
            break
        page += 1
        assert page < 100, "pagination did not terminate"
    return ids, total, sizes


def check_tab(lib, tab: str, active_map: dict, *, account_id=None, search="",
              expect_status=None, expect_plus=None) -> list[str]:
    """The grid, its total, and Select All must all describe the same set."""
    active_ids = set(active_map)
    selected = lib.get_liberate_book_ids(
        filter_status=tab, account_id=account_id,
        active_download_ids=active_ids, search=search,
    )
    ids, total, sizes = collect_pages(
        lib, active_map, filter_status=tab, account_id=account_id, search=search
    )

    label = f"{tab}" + (f" account={account_id}" if account_id else "") + (
        f" search={search!r}" if search else "")
    assert total == len(selected), (
        f"[{label}] grid total {total} but Select All offers {len(selected)}"
    )
    assert sorted(ids) == sorted(selected), (
        f"[{label}] grid shows {len(ids)} books, Select All picks {len(selected)}"
    )
    assert len(ids) == len(set(ids)), f"[{label}] a book appeared on two pages"
    assert len(ids) == total, f"[{label}] paged {len(ids)} books but total says {total}"

    # Every page but the last must be full. This is the symptom the old code
    # showed directly: a filtered page came back part-empty under a total that
    # had counted the books the filter dropped.
    for n, size in enumerate(sizes[:-1], start=1):
        assert size == PAGE_SIZE, f"[{label}] page {n} held {size} of {PAGE_SIZE}"

    # And every book returned really belongs to the tab.
    if expect_status is not None or expect_plus is not None:
        page_one = lib.get_liberate_books(
            active_downloads=active_map, filter_status=tab, page=1,
            page_size=200, account_id=account_id, search=search,
        )
        for book in page_one["books"]:
            if expect_status is not None:
                assert book["liberate_status"] == expect_status, (
                    f"[{label}] {book['book_id']} shows {book['liberate_status']}"
                )
            if expect_plus is not None:
                assert book["is_audible_plus"] is expect_plus, (
                    f"[{label}] {book['book_id']} is_audible_plus={book['is_audible_plus']}"
                )
    return selected


def test_filters(lib, expected) -> None:
    # Two books mid-download, one of them otherwise "liberated" — a download in
    # flight has to win over the stored status on every download-state tab.
    downloading = {"B000000002": ("running", 40), "B000000007": ("queued", 0)}
    library_size = len(expected)

    everything = check_tab(lib, "all", downloading)
    assert len(everything) == library_size, (len(everything), library_size)
    print(f"✓ unfiltered grid, total and Select All agree over {library_size} books")

    tabs = {
        "liberated": {"expect_status": "liberated"},
        "not_liberated": {"expect_status": "not_liberated"},
        "error": {"expect_status": "error"},
        "downloading": {"expect_status": "downloading"},
        "audible_plus": {"expect_plus": True},
        "purchased": {"expect_plus": False},
    }
    sets = {}
    for tab, expectations in tabs.items():
        sets[tab] = set(check_tab(lib, tab, downloading, **expectations))
        assert sets[tab], f"the {tab} tab matched nothing — fixture is not exercising it"
    print("✓ every filter tab pages cleanly and returns only books that match it")

    # The download-state tabs partition the library, and a book in flight
    # belongs to exactly one of them.
    state_tabs = ["liberated", "not_liberated", "error", "downloading"]
    union = set().union(*(sets[t] for t in state_tabs))
    assert union == set(everything), "download-state tabs do not cover the library"
    assert sum(len(sets[t]) for t in state_tabs) == library_size, (
        "a book appears under two download-state tabs"
    )
    assert sets["downloading"] == set(downloading), sets["downloading"]
    print("✓ download-state tabs partition the library; a download in flight wins")

    # Ownership is a separate axis, so it partitions the library too — including
    # the book held on two accounts, which must not land in both tabs.
    assert sets["audible_plus"] | sets["purchased"] == set(everything)
    assert not (sets["audible_plus"] & sets["purchased"]), (
        "a book counted as both Audible Plus and purchased"
    )
    print("✓ Audible Plus and Purchased partition the library, shared accounts included")

    # ...and it does *not* exclude a book that happens to be downloading, the
    # way a download-state tab does.
    in_flight_plus = {b for b in downloading if b in sets["audible_plus"] | sets["purchased"]}
    assert in_flight_plus == set(downloading), (
        "an ownership tab dropped a book only because it was downloading"
    )
    print("✓ ownership tabs ignore download state")

    # Filters combine with the account tabs and the search box.
    for tab in tabs:
        for account in (ACCOUNT_A, ACCOUNT_B):
            check_tab(lib, tab, downloading, account_id=account, **tabs[tab])
        check_tab(lib, tab, downloading, search="Saga", **tabs[tab])
    per_account = {
        t: set(lib.get_liberate_book_ids(filter_status=t, account_id=ACCOUNT_A,
                                         active_download_ids=set(downloading)))
        for t in state_tabs
    }
    assert sum(len(v) for v in per_account.values()) == len(
        lib.get_liberate_book_ids(filter_status="all", account_id=ACCOUNT_A)
    )
    print("✓ filters stay consistent when combined with an account tab or a search")


def test_edge_cases(lib) -> None:
    # Nothing in flight: the downloading tab is empty, not the whole library.
    # It used to skip its Python post-filter entirely when handed no active set.
    assert lib.get_liberate_book_ids(filter_status="downloading") == []
    assert lib.get_liberate_book_ids(
        filter_status="downloading", active_download_ids=set()) == []
    assert lib.get_liberate_books(filter_status="downloading")["total"] == 0
    print("✓ 'downloading' with nothing in flight matches nothing, not everything")

    # An unrecognised tab matches nothing on both paths. Quietly ignoring it is
    # how a bad filter would hand the whole library to a bulk download.
    assert lib.get_liberate_book_ids(filter_status="not-a-tab") == []
    assert lib.get_liberate_books(filter_status="not-a-tab")["total"] == 0
    print("✓ an unrecognised filter matches nothing on both paths")

    # A book with no UserDefinedItem row reads as not_liberated, which is what
    # the auto-download and Download All paths enumerate.
    not_liberated = lib.get_liberate_book_ids(filter_status="not_liberated")
    assert "B000000004" in not_liberated, "book without a UserDefinedItem row was dropped"
    print("✓ a book with no UserDefinedItem row counts as not downloaded")

    # Past the last page: an empty page under an unchanged total.
    tail = lib.get_liberate_books(filter_status="liberated", page=99, page_size=PAGE_SIZE)
    assert tail["books"] == [] and tail["total"] > 0, tail
    print("✓ a page past the end is empty without disturbing the total")


# ── File ordering ─────────────────────────────────────────────────────────────

PART_ASIN = "B0PARTED01"
GLOB_ASIN = "B0GLOBBED1"


def test_file_order(lib) -> None:
    # Twelve parts, so the lexical order ("Part 10" before "Part 2") is wrong in
    # a way a 9-part book would hide.
    folder = BOOKS / "Robert Jordan" / f"The Eye of the World [{PART_ASIN}]"
    folder.mkdir(parents=True, exist_ok=True)
    parts = []
    for n in range(1, 13):
        f = folder / f"The Eye of the World - Part {n}.m4b"
        f.write_bytes(b"not really an m4b")
        parts.append(str(f))

    # Listed in the cache out of order, and with a PDF that must not come back.
    shuffled = [parts[9], parts[1], parts[11], *parts[2:9], parts[0], parts[10]]
    (CONFIG / "FileLocationsV2.json").write_text(json.dumps({
        "Dictionary": {
            PART_ASIN: (
                [{"Id": PART_ASIN, "FileType": 1, "Path": p} for p in shuffled]
                + [{"Id": PART_ASIN, "FileType": 3, "Path": str(folder / "booklet.pdf")}]
            ),
        }
    }))

    got = lib.get_audio_file_paths(PART_ASIN)
    assert got == parts, (
        "parts came back out of sequence:\n  "
        + "\n  ".join(Path(p).name for p in got)
    )
    print("✓ file cache entries are returned in part order, not lexical order")

    # The fallback that finds files by globbing the books directory has to sort
    # the same way — it is what every import falls back to when the cache has
    # no usable entry.
    glob_folder = BOOKS / "Someone" / f"Chaptered [{GLOB_ASIN}]"
    glob_folder.mkdir(parents=True, exist_ok=True)
    chapters = []
    for n in range(1, 12):
        f = glob_folder / f"Chaptered [{GLOB_ASIN}] - {n}.m4b"
        f.write_bytes(b"nope")
        chapters.append(str(f))
    assert lib.get_audio_file_paths(GLOB_ASIN) == chapters, (
        "the directory fallback returned files out of order"
    )
    print("✓ the directory-scan fallback sorts the same way")

    # Deleted files drop out, and the order of what is left is unchanged.
    Path(parts[2]).unlink()
    remaining = lib.get_audio_file_paths(PART_ASIN)
    assert remaining == parts[:2] + parts[3:], remaining
    print("✓ files that no longer exist are dropped, order preserved")

    assert lib.get_audio_file_paths("B0NOTHERE1") == []
    print("✓ a book that was never downloaded returns nothing")


def main() -> None:
    expected = build_library()
    from app.services import libation as lib

    assert lib.db_exists(), "fixture database was not picked up from LIBATION_CONFIG"
    test_filters(lib, expected)
    test_edge_cases(lib)
    test_file_order(lib)

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll Liberate checks passed.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
