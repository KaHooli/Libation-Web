#!/usr/bin/env python3
"""Reconciliation and the `liberated` derivation, against real tagged files.

Reconciliation is the part of the native engine that has to be right. Once
Potation owns "do I already have this?", every bulk-download path trusts its
answer — so a miss re-downloads a book against a daily-capped upstream, and a
false positive silently withholds one. These checks cover both directions.

The files are real: `mutagen` writes actual MP4 and ID3 tags into small
synthesised files, so the tag-reading path is exercised rather than mocked.

Covered:
  * the matching ladder — tag, path shape, `.metadata.json` sidecar — and that
    each records how it matched
  * a file Chaptarr has moved and renamed out of AUDIOBOOKS_DIR, found under the
    Chaptarr root by its embedded ASIN alone, with the stale row pruned
  * part ordering from tags, including the "Part 10 before Part 2" case and the
    disc-major case
  * the `(path, size, mtime)` skip cache, and that a changed file is re-read
  * fuzzy matches landing `confirmed=False`, staying out of `liberated`, and an
    ambiguous title matching nothing
  * that pruning never touches a root which was not walked — the failure that
    would hand a whole shelf back to the auto-download loop
  * the tri-state `liberated_override`
  * the reconciliation gate and the bulk-enqueue valve

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-reconcile.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="reconcile-test-"))
CONFIG = WORKDIR / "config"
BOOKS = WORKDIR / "audiobooks"
CHAPTARR_ROOT = WORKDIR / "chaptarr-library"
DATA = WORKDIR / "data"
for d in (DATA, CONFIG, BOOKS, CHAPTARR_ROOT):
    d.mkdir(parents=True, exist_ok=True)

# Must be set before `app.config` is imported.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "reconcile-test-only-not-a-real-secret")

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


# ── Synthesising tagged audio ─────────────────────────────────────────────────
#
# A real MP4/M4B container, small enough to write by hand. mutagen refuses to
# tag a file it cannot parse, so this has to be structurally valid.

def _m4a_bytes() -> bytes:
    def box(kind: bytes, payload: bytes) -> bytes:
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    ftyp = box(b"ftyp", b"M4A " + (0).to_bytes(4, "big") + b"M4A mp42isom")
    mvhd = box(b"mvhd", bytes(4) + bytes(8) + (1000).to_bytes(4, "big")
               + (0).to_bytes(4, "big") + b"\x00\x01\x00\x00" + b"\x01\x00" + bytes(10)
               + bytes.fromhex("0001000000000000000000000001000000000000000000000040000000")
               + bytes(24) + (0).to_bytes(4, "big"))
    moov = box(b"moov", mvhd + box(b"udta", box(b"meta", bytes(4) + box(b"ilst", b""))))
    return ftyp + moov + box(b"free", b"")


def write_m4b(path: Path, *, asin=None, title=None, artist=None,
              track=None, disc=None) -> Path:
    from mutagen.mp4 import MP4, MP4FreeForm

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_m4a_bytes())
    audio = MP4(str(path))
    if asin:
        audio["----:com.apple.iTunes:ASIN"] = [
            MP4FreeForm(asin.encode("utf-8"))
        ]
    if title:
        audio["\xa9nam"] = [title]
    if artist:
        audio["\xa9ART"] = [artist]
    if track is not None:
        audio["trkn"] = [(track, 0)]
    if disc is not None:
        audio["disk"] = [(disc, 0)]
    audio.save()
    return path


def write_mp3(path: Path, *, asin=None, title=None) -> Path:
    from mutagen.id3 import ID3, TIT2, TXXX

    path.parent.mkdir(parents=True, exist_ok=True)
    # A silent MPEG frame header is enough for mutagen to accept the file.
    path.write_bytes(b"\xff\xfb\x90\x00" + bytes(400))
    tags = ID3()
    if asin:
        tags.add(TXXX(encoding=3, desc="ASIN", text=[asin]))
    if title:
        tags.add(TIT2(encoding=3, text=[title]))
    tags.save(str(path))
    return path


def write_plain(path: Path, body: bytes = b"untagged") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


# ── Fixture library ───────────────────────────────────────────────────────────

TAGGED = "B000000101"        # found by its embedded tag
IN_PATH = "B000000102"       # found by ASIN in the path
SIDECAR = "B000000103"       # found by a .metadata.json beside it
MOVED = "B000000104"         # relocated by Chaptarr; only the tag survives
PARTS = "B000000105"         # twelve parts, tagged with track numbers
DISCS = "B000000106"         # two discs x two tracks
FUZZY = "B000000107"         # matched only by title + author
AMBIGUOUS = "B000000108"     # shares a title with another book
AMBIGUOUS2 = "B000000109"    # the other one
NEVER = "B000000110"         # nothing on disk at all
OVERRIDDEN = "B000000111"    # no file, but a user says they have it
PDF_ONLY = "B000000112"      # a companion PDF and nothing else


def seed_books(db) -> None:
    from app.models.potation import Book

    rows = [
        (TAGGED, "The Tagged One", "Ann Author", 100),
        (IN_PATH, "The Path One", "Ben Bard", 200),
        (SIDECAR, "The Sidecar One", "Cara Chronicle", 300),
        (MOVED, "The Moved One", "Dana Drift", 400),
        (PARTS, "The Long One", "Eli Epic", 500),
        (DISCS, "The Boxed One", "Fay Folio", 600),
        (FUZZY, "The Untagged One", "Gil Ghost", 700),
        (AMBIGUOUS, "Twice Told", "Hana Half", 800),
        (AMBIGUOUS2, "Twice Told", "Ivan Other", 900),
        (NEVER, "The Missing One", "Jo Nobody", 111),
        (OVERRIDDEN, "The Claimed One", "Kim Keeper", 222),
        (PDF_ONLY, "The Booklet One", "Lena Leaflet", 333),
    ]
    for asin, title, author, minutes in rows:
        db.add(Book(asin=asin, title=title, authors=[author],
                    length_minutes=minutes, is_multipart_parent=False))
    db.commit()


def build_tree() -> None:
    write_m4b(BOOKS / "Ann Author" / "The Tagged One" / "The Tagged One.m4b",
              asin=TAGGED, title="The Tagged One", artist="Ann Author")

    # No tag at all — the ASIN is only in the folder name, Libation's default.
    # The companion cover and PDF beside it are attributable for the same reason.
    path_dir = BOOKS / "Ben Bard" / f"The Path One [{IN_PATH}]"
    write_plain(path_dir / "The Path One.m4b")
    write_plain(path_dir / "The Path One.jpg")
    write_plain(path_dir / "The Path One.pdf")

    # A booklet with no audiobook. Only `kind='audio'` counts as having the
    # book, so this must not read as downloaded.
    write_plain(BOOKS / "Lena Leaflet" / f"The Booklet One [{PDF_ONLY}]" / "booklet.pdf")

    sidecar_dir = BOOKS / "Cara Chronicle" / "The Sidecar One"
    write_plain(sidecar_dir / "The Sidecar One.m4b")
    (sidecar_dir / "metadata.json").write_text(json.dumps({"asin": SIDECAR}))

    # Chaptarr moved and renamed this one out of our directory entirely.
    write_m4b(CHAPTARR_ROOT / "Dana Drift" / "The Moved One" / "01 - Renamed By Chaptarr.m4b",
              asin=MOVED, title="The Moved One", artist="Dana Drift")

    for n in range(1, 13):
        write_m4b(BOOKS / "Eli Epic" / "The Long One" / f"The Long One - Part {n}.m4b",
                  asin=PARTS, title="The Long One", track=n)

    for disc in (1, 2):
        for track in (1, 2):
            write_m4b(
                BOOKS / "Fay Folio" / "The Boxed One" / f"d{disc} t{track}.m4b",
                asin=DISCS, title="The Boxed One", track=track, disc=disc,
            )

    # Title and author, but no ASIN anywhere: the fuzzy route.
    write_m4b(BOOKS / "Gil Ghost" / "The Untagged One" / "The Untagged One.m4b",
              title="The Untagged One", artist="Gil Ghost")

    # Two books share this title, so it must match neither.
    write_m4b(BOOKS / "Hana Half" / "Twice Told" / "Twice Told.m4b",
              title="Twice Told", artist="Someone Unrecorded")

    # Companion files, and an audio file belonging to nothing we know about.
    write_plain(BOOKS / "Ann Author" / "The Tagged One" / "cover.jpg")
    write_plain(BOOKS / "Zed Stranger" / "Not In The Library.m4b")


# ── Checks ────────────────────────────────────────────────────────────────────

def files_for(db, asin):
    from app.models.potation import BookFile
    return (
        db.query(BookFile)
        .filter(BookFile.book_asin == asin)
        .order_by(BookFile.part_index, BookFile.path)
        .all()
    )


def test_ladder(db, reconcile) -> None:
    result = reconcile.reconcile(
        db,
        roots=[reconcile.audiobooks_root(),
               reconcile.Root(path=CHAPTARR_ROOT, label="chaptarr")],
    )
    assert not result.errors, result.errors
    assert result.run_id is not None

    sources = {}
    for asin in (TAGGED, SIDECAR, MOVED):
        rows = files_for(db, asin)
        assert len(rows) == 1, (asin, rows)
        sources[asin] = rows[0].asin_source
    sources[IN_PATH] = {r.asin_source for r in files_for(db, IN_PATH)}.pop()
    assert sources == {
        TAGGED: "tag", IN_PATH: "path", SIDECAR: "metadata_json", MOVED: "tag",
    }, sources
    print("✓ tag, path shape and sidecar each match, and record how they matched")

    moved = files_for(db, MOVED)[0]
    assert moved.root == "chaptarr", moved.root
    assert str(CHAPTARR_ROOT) in moved.path, moved.path
    print("✓ a book Chaptarr moved and renamed is found by its embedded ASIN alone")

    kinds = sorted(r.kind for r in files_for(db, IN_PATH))
    assert kinds == ["audio", "cover", "pdf"], kinds
    print("✓ companion files are recorded by kind, not mistaken for the book")

    assert any("Not In The Library" in p for p in result.unmatched), result.unmatched
    assert not any("cover.jpg" in p for p in result.unmatched), (
        "an unattributable cover is not worth reporting as unmatched"
    )
    print("✓ an audio file we cannot attribute is reported; a stray cover is not")


def test_part_order(db, reconcile) -> None:
    from app.services.potation import liberated as lib

    paths = lib.audio_paths(db, PARTS)
    assert len(paths) == 12, len(paths)
    numbers = [int(Path(p).stem.rsplit(" ", 1)[1]) for p in paths]
    assert numbers == list(range(1, 13)), numbers
    print("✓ twelve parts come back 1..12, not 1, 10, 11, 12, 2 ...")

    indices = [r.part_index for r in files_for(db, DISCS)]
    assert indices == [1001, 1002, 2001, 2002], indices
    print("✓ disc-major ordering keeps a two-disc set in sequence")


def test_fuzzy(db, reconcile) -> None:
    from app.services.potation import liberated as lib

    rows = files_for(db, FUZZY)
    assert len(rows) == 1, rows
    assert rows[0].asin_source == "fuzzy" and rows[0].confirmed is False, rows[0].asin_source
    print("✓ a title+author match is recorded, but as unconfirmed")

    assert lib.is_liberated(db, FUZZY) is False, (
        "an unconfirmed match must never suppress a download"
    )
    assert FUZZY in lib.not_liberated_asins(db)
    print("✓ an unconfirmed match does not count as downloaded")

    assert not files_for(db, AMBIGUOUS) and not files_for(db, AMBIGUOUS2), (
        "a title shared by two books must match neither"
    )
    print("✓ an ambiguous title matches nothing rather than guessing")

    pending = lib.unconfirmed_files(db)
    assert [r.book_asin for r in pending] == [FUZZY], pending
    assert lib.confirm_file(db, pending[0].id) is True
    assert lib.is_liberated(db, FUZZY) is True
    print("✓ confirming a fuzzy match promotes it to downloaded")


def test_liberated(db, reconcile) -> None:
    from app.services.potation import liberated as lib

    assert lib.is_liberated(db, TAGGED) is True
    assert lib.is_liberated(db, NEVER) is False
    print("✓ liberated derives from the files on disk")

    assert files_for(db, PDF_ONLY), "the booklet should have been attributed"
    assert lib.is_liberated(db, PDF_ONLY) is False, (
        "a companion PDF is not the audiobook — only kind='audio' counts"
    )
    assert PDF_ONLY in lib.not_liberated_asins(db)
    print("✓ a book with only a companion PDF still counts as not downloaded")

    assert lib.set_override(db, OVERRIDDEN, True) is True
    assert lib.is_liberated(db, OVERRIDDEN) is True
    assert OVERRIDDEN not in lib.not_liberated_asins(db)
    print("✓ an override marks a book downloaded with no file present")

    assert lib.set_override(db, TAGGED, False) is True
    assert lib.is_liberated(db, TAGGED) is False, (
        "an explicit 'not downloaded' must beat the file on disk"
    )
    lib.set_override(db, TAGGED, None)
    assert lib.is_liberated(db, TAGGED) is True
    print("✓ the override is tri-state: 1 and 0 win, NULL goes back to deriving")

    bulk = lib.liberated_map(db, [TAGGED, NEVER, OVERRIDDEN])
    assert bulk == {TAGGED: True, NEVER: False, OVERRIDDEN: True}, bulk
    assert lib.liberated_map(db, []) == {}
    print("✓ the bulk map agrees with the per-book answer")


def test_skip_cache(db, reconcile) -> None:
    from app.models.potation import BookFile

    recorded = db.query(BookFile).count()
    second = reconcile.reconcile(
        db,
        roots=[reconcile.audiobooks_root(),
               reconcile.Root(path=CHAPTARR_ROOT, label="chaptarr")],
    )
    assert second.files_skipped == recorded, (
        f"re-run reopened {recorded - second.files_skipped} file(s) that already "
        "had a row whose size and mtime had not changed"
    )
    # The only files re-read are the ones no row exists for, because there is
    # nothing to cache a signature against: the stray audiobook, the
    # unattributable cover, and the ambiguous title that matched nothing.
    assert second.files_scanned - second.files_skipped == 3, (
        second.files_scanned, second.files_skipped
    )
    assert second.pruned == 0, second.pruned
    print(f"✓ a re-run skips all {second.files_skipped} recorded files and prunes nothing")

    # Touching a file must bring it back through the ladder.
    target = BOOKS / "Ann Author" / "The Tagged One" / "The Tagged One.m4b"
    os.utime(target, (0, 0))
    third = reconcile.reconcile(
        db,
        roots=[reconcile.audiobooks_root(),
               reconcile.Root(path=CHAPTARR_ROOT, label="chaptarr")],
    )
    assert third.files_skipped == third.files_scanned - 4, (
        "expected the touched file plus the three unmatched ones to be re-read",
        third.files_skipped, third.files_scanned,
    )
    assert files_for(db, TAGGED)[0].mtime == target.stat().st_mtime
    print("✓ a changed file is re-read and its row updated")


def test_pruning(db, reconcile) -> None:
    from app.models.potation import BookFile
    from app.services.potation import liberated as lib

    # Simulate Chaptarr moving a book we still have a row for.
    stale = BOOKS / "Cara Chronicle" / "The Sidecar One" / "The Sidecar One.m4b"
    stale.unlink()
    reconcile.reconcile(
        db,
        roots=[reconcile.audiobooks_root(),
               reconcile.Root(path=CHAPTARR_ROOT, label="chaptarr")],
    )
    assert not files_for(db, SIDECAR), "a deleted file's row should be gone"
    assert lib.is_liberated(db, SIDECAR) is False
    print("✓ a file that is genuinely gone is pruned, and the book stops counting")

    # Now the failure that matters: the Chaptarr root is not walked this run —
    # unreachable, unmounted, whatever. Its rows must survive untouched.
    before = {r.path for r in db.query(BookFile).filter(BookFile.root == "chaptarr").all()}
    assert before, "fixture no longer covers the Chaptarr root"
    reconcile.reconcile(db, roots=[reconcile.audiobooks_root()])
    after = {r.path for r in db.query(BookFile).filter(BookFile.root == "chaptarr").all()}
    assert after == before, (
        "rows under a root that was not walked were pruned — this hands a whole "
        "shelf of books back to the auto-download loop"
    )
    assert lib.is_liberated(db, MOVED) is True
    print("✓ a root that was not walked is never pruned")


def test_partial_walk_does_not_prune(db, reconcile) -> None:
    """The scenario this guard exists for, end to end.

    Chaptarr moves a book out of our directory. The next reconcile cannot read
    the tree it moved into. The old row's file is now genuinely absent, so
    pruning it looks right — but the replacement was never discovered, and
    deleting the only evidence leaves the book reading as not-downloaded and
    queued for re-download from a daily-capped upstream.
    """
    from app.services.potation import liberated as lib

    old_path = BOOKS / "Ann Author" / "The Tagged One" / "The Tagged One.m4b"
    new_path = CHAPTARR_ROOT / "Ann Author" / "Renamed By Chaptarr.m4b"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_path), str(new_path))
    assert not old_path.exists() and lib.is_liberated(db, TAGGED) is True

    real_walk = reconcile._walk

    def broken_walk(root, on_error):
        # What an unreadable directory looks like: os.walk reports through
        # onerror and simply yields nothing for that subtree.
        on_error(f"{root.path}: [Errno 13] Permission denied")
        return iter(())

    reconcile._walk = broken_walk
    try:
        result = reconcile.reconcile(
            db,
            roots=[reconcile.audiobooks_root(),
                   reconcile.Root(path=CHAPTARR_ROOT, label="chaptarr")],
        )
    finally:
        reconcile._walk = real_walk

    assert lib.is_liberated(db, TAGGED) is True, (
        "a failed walk pruned the moved book's only row — it now reads as "
        "not downloaded and goes back into the download queue"
    )
    assert result.pruned == 0, result.pruned
    assert any("Skipped pruning" in e for e in result.errors), result.errors
    print("✓ a partial walk keeps a moved book's stale row rather than losing it")

    # And once the tree can be read, the row follows the file to its new home.
    reconcile.reconcile(
        db,
        roots=[reconcile.audiobooks_root(),
               reconcile.Root(path=CHAPTARR_ROOT, label="chaptarr")],
    )
    rows = files_for(db, TAGGED)
    assert [r.path for r in rows] == [str(new_path)], rows
    assert rows[0].root == "chaptarr" and rows[0].asin_source == "tag"
    assert lib.is_liberated(db, TAGGED) is True
    print("✓ a clean pass afterwards re-points the row and drops the stale one")


def test_roots(db, reconcile) -> None:
    nested = reconcile._dedupe_roots([
        reconcile.Root(path=BOOKS, label="audiobooks"),
        reconcile.Root(path=BOOKS / "Ann Author", label="chaptarr"),
        reconcile.Root(path=BOOKS, label="chaptarr"),
    ])
    assert [r.label for r in nested] == ["audiobooks"], nested
    print("✓ duplicate and nested roots are collapsed to the outermost one")

    missing = reconcile._dedupe_roots([
        reconcile.Root(path=WORKDIR / "does-not-exist", label="chaptarr"),
    ])
    assert missing == [], missing
    try:
        reconcile.reconcile(db, roots=missing)
    except reconcile.ReconcileError as exc:
        assert "AUDIOBOOKS_DIR" in str(exc), exc
    else:
        raise AssertionError("reconciling with no readable root must refuse, not report success")
    print("✓ reconciling with no readable root refuses instead of reporting an empty library")


def test_unmap(db) -> None:
    from app.services import chaptarr as chaptarr_svc

    cfg = chaptarr_svc.ChaptarrConfig(path_from="/audiobooks", path_to="/data/media/books")
    assert chaptarr_svc.map_path(cfg, "/audiobooks/A/b.m4b") == "/data/media/books/A/b.m4b"
    assert chaptarr_svc.unmap_path(cfg, "/data/media/books/A/b.m4b") == "/audiobooks/A/b.m4b"
    assert chaptarr_svc.unmap_path(cfg, "/data/media/books") == "/audiobooks"
    assert chaptarr_svc.unmap_path(cfg, "/elsewhere/x.m4b") == "/elsewhere/x.m4b"
    blank = chaptarr_svc.ChaptarrConfig()
    assert chaptarr_svc.unmap_path(blank, "/audiobooks/A") == "/audiobooks/A"
    # A prefix that merely starts with the same characters is not a subpath.
    assert chaptarr_svc.unmap_path(cfg, "/data/media/books-old/x") == "/data/media/books-old/x"
    print("✓ unmap_path inverts map_path, including the not-a-subpath case")


def test_chaptarr_roots_fail_soft(db, reconcile) -> None:
    """Discovering Chaptarr's roots must never be able to stop reconciliation.

    A metadata server being down is a reason to reconcile only our own
    directory, not a reason to reconcile nothing.
    """
    import asyncio

    from app.services import chaptarr as chaptarr_svc

    assert asyncio.run(reconcile.chaptarr_roots(db)) == [], (
        "unconfigured Chaptarr should be a silent no-op"
    )

    # Configured, enabled, and pointing at a port nothing is listening on.
    chaptarr_svc.save_config(db, {
        "chaptarr_enabled": True,
        "chaptarr_url": "http://127.0.0.1:9",
        "chaptarr_api_key": "not-a-real-key",
    })
    try:
        assert asyncio.run(reconcile.chaptarr_roots(db)) == [], (
            "an unreachable Chaptarr must yield no roots, not raise"
        )
    finally:
        chaptarr_svc.save_config(db, {
            "chaptarr_enabled": False, "chaptarr_url": "", "chaptarr_api_key": "",
        })
    print("✓ root discovery fails soft when Chaptarr is off or unreachable")


def test_gate_and_valve(db, reconcile) -> None:
    from app.models.potation import ReconciliationRun

    assert reconcile.reconciliation_complete(db) is True
    reconcile.check_bulk_enqueue(db, 5)
    print("✓ a small enqueue passes once reconciliation has completed")

    try:
        reconcile.check_bulk_enqueue(db, 5000)
    except reconcile.BulkEnqueueRefused as exc:
        assert "daily download cap" in str(exc), exc
    else:
        raise AssertionError("the valve let 5000 downloads through unconfirmed")
    reconcile.check_bulk_enqueue(db, 5000, confirmed=True)
    print("✓ a library-sized enqueue is refused unless explicitly confirmed")

    # Wind the database back to "never reconciled".
    db.query(ReconciliationRun).delete()
    db.commit()
    assert reconcile.reconciliation_complete(db) is False
    try:
        reconcile.check_bulk_enqueue(db, 1)
    except reconcile.BulkEnqueueRefused as exc:
        assert "reconciled" in str(exc), exc
    else:
        raise AssertionError("a bulk enqueue ran before reconciliation had ever completed")
    reconcile.check_bulk_enqueue(db, 0)
    print("✓ no enqueue at all is allowed before reconciliation has ever completed")


def test_failed_run_does_not_gate(db, reconcile) -> None:
    from app.models.potation import ReconciliationRun

    db.add(ReconciliationRun(status="error", error_message="disk fell off"))
    db.commit()
    assert reconcile.reconciliation_complete(db) is False, (
        "a failed run must not satisfy the gate — it proves nothing about what is on disk"
    )
    print("✓ a failed reconciliation run does not satisfy the gate")


def main() -> None:
    from app.database import SessionLocal
    from app.migrations import run_migrations
    from app.services.potation import reconcile

    run_migrations()
    build_tree()

    with SessionLocal() as db:
        seed_books(db)
        test_unmap(db)
        test_ladder(db, reconcile)
        test_part_order(db, reconcile)
        test_fuzzy(db, reconcile)
        test_liberated(db, reconcile)
        test_skip_cache(db, reconcile)
        test_pruning(db, reconcile)
        test_partial_walk_does_not_prune(db, reconcile)
        test_roots(db, reconcile)
        test_chaptarr_roots_fail_soft(db, reconcile)
        test_gate_and_valve(db, reconcile)
        test_failed_run_does_not_gate(db, reconcile)

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll reconciliation checks passed.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
