"""Matching audiobook files already on disk back to the books they belong to.

This is what makes `liberated` trustworthy once Potation owns it. Today Libation
answers "do I already have this?", and both the auto-download loop and bulk
liberate trust that answer blindly. The moment we own it, every one of those
paths is one bad reconciliation away from enqueueing the whole library against a
daily-capped, ban-capable upstream — so this module is deliberately conservative
in both directions: it never invents a match, and it never drops a row it cannot
prove is gone.

**Both roots, not just ours.** `chaptarr_import_mode` defaults to `auto`, and on
`move` (or `auto` where hardlinking fails) Chaptarr relocates the file out of
`AUDIOBOOKS_DIR` into its own root and renames it. Scanning only our own
directory would find nothing for everything Chaptarr has already imported —
likely the largest population on an existing install.

**Tags first.** Once Chaptarr has moved and renamed a file, the embedded ASIN is
the only identity left on it. The filename is gone, the folder is gone. So the
ladder runs tag → path shape → sidecar → fuzzy, cheapest-and-surest first, and
a fuzzy hit is recorded `confirmed=False` so it is surfaced rather than trusted.

Nothing here is wired into the live download paths: LibationCli is still the
engine and still owns `liberated`. The gate and the valve below exist so the
Phase C cut-over has something to call.

**Known limit.** The `(path, size, mtime)` skip cache lives on `book_files`, so
it only covers files that matched a book. A file nothing could be matched to is
re-read on every run — there is no row to hold its signature. That is the right
trade for now: the volume is in matched files (a chapter-split library is tens
of thousands of them), and giving unmatched files a home means either a nullable
FK or a table of their own. Worth revisiting if a real library turns out to
carry a large unmatched tail.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...models.potation import Book, BookFile, ReconciliationRun

#: Extensions we treat as the book itself. Anything else is a companion file.
AUDIO_EXTENSIONS = frozenset({".aac", ".flac", ".m4a", ".m4b", ".mp3", ".mp4", ".ogg", ".opus"})
COVER_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
PDF_EXTENSIONS = frozenset({".pdf"})

#: Audible ASINs are `B0` plus eight upper-case alphanumerics. Matching on the
#: *shape* is far cheaper than the O(books x files) substring scan the current
#: Chaptarr fallback does, and the `books` lookup below rejects a false hit.
ASIN_RE = re.compile(r"\bB0[0-9A-Z]{8}\b")

#: Libation writes this beside a book when the sidecar option is on.
SIDECAR_NAMES = ("metadata.json", ".metadata.json")

#: How close two durations must be to corroborate a fuzzy title match.
_DURATION_TOLERANCE = 0.02

#: Refuse to bulk-enqueue more than this without someone saying so explicitly.
DEFAULT_BULK_LIMIT = 25


class ReconcileError(Exception):
    """Reconciliation could not run at all."""


@dataclass(frozen=True)
class Root:
    """One directory to walk, and the label its files get on `book_files.root`."""

    path: Path
    label: str  # "audiobooks" | "chaptarr"


@dataclass
class FileFacts:
    """Everything one read of a file tells us."""

    path: Path
    size: int
    mtime: float
    kind: str
    asin: Optional[str] = None
    asin_source: Optional[str] = None
    confirmed: bool = True
    part_index: Optional[int] = None
    title: Optional[str] = None
    authors: tuple[str, ...] = ()
    duration: Optional[float] = None


#: How many unmatched paths to keep for reporting. An install with a
#: non-default Libation template predating ASIN tagging can have thousands, and
#: the caller wants a sample to look at, not all of them in memory.
UNMATCHED_SAMPLE = 200


@dataclass
class ReconcileResult:
    run_id: Optional[int] = None
    files_scanned: int = 0
    files_skipped: int = 0
    matched: int = 0
    unconfirmed: int = 0
    #: A sample, capped at `UNMATCHED_SAMPLE`. `unmatched_total` is the real count.
    unmatched: list[str] = field(default_factory=list)
    unmatched_total: int = 0
    pruned: int = 0
    roots: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── Roots ─────────────────────────────────────────────────────────────────────

def audiobooks_root() -> Root:
    return Root(path=Path(settings.AUDIOBOOKS_DIR), label="audiobooks")


async def chaptarr_roots(db: Session) -> list[Root]:
    """Chaptarr's root folders, translated back into paths we can actually walk.

    Returns nothing rather than raising when Chaptarr is off, unconfigured or
    unreachable — a metadata server being down must not stop us reconciling our
    own directory. Roots that do not exist on this filesystem are dropped: an
    unmapped Chaptarr path is a configuration problem, not a set of files.
    """
    from ...services import chaptarr as chaptarr_svc

    cfg = chaptarr_svc.load_config(db)
    if not (cfg.enabled and cfg.configured):
        return []
    try:
        info = await chaptarr_svc.test_connection(cfg)
    except Exception:
        return []

    roots: list[Root] = []
    for folder in info.get("root_folders") or []:
        raw = (folder or {}).get("path")
        if not raw:
            continue
        local = Path(chaptarr_svc.unmap_path(cfg, str(raw)))
        if local.is_dir():
            roots.append(Root(path=local, label="chaptarr"))
    return roots


def _dedupe_roots(roots: Iterable[Root]) -> list[Root]:
    """Drop roots that are the same directory, or nested inside an earlier one.

    Walking a root twice would visit each file twice; the second visit would see
    the row the first just wrote and skip it, so the result is merely wasteful —
    but a nested root would also relabel files, which is not.
    """
    kept: list[Root] = []
    for root in roots:
        try:
            resolved = root.path.resolve()
        except OSError:
            continue
        if not resolved.is_dir():
            continue
        if any(resolved == k.path or resolved.is_relative_to(k.path) for k in kept):
            continue
        kept.append(Root(path=resolved, label=root.label))
    return kept


# ── Reading a file ────────────────────────────────────────────────────────────

def _kind_of(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in COVER_EXTENSIONS:
        return "cover"
    return None


def _first_str(value) -> Optional[str]:
    """Tag values arrive as lists, bytes, or mutagen's own text wrappers."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", "replace")
        except Exception:
            return None
    text = str(value).strip()
    return text or None


def _tag_asin(tags) -> Optional[str]:
    """Pull an ASIN out of whatever tag shape this container uses.

    MP4 keeps freeform atoms under `----:com.apple.iTunes:ASIN`; ID3 uses a
    `TXXX:ASIN` frame. Libation writes `Asin` unconditionally, and case varies
    between writers, so every key is compared case-insensitively.
    """
    if tags is None:
        return None
    try:
        keys = list(tags.keys())
    except Exception:
        return None

    for key in keys:
        name = str(key)
        tail = name.rsplit(":", 1)[-1].strip().lower()
        if tail not in ("asin", "audible_asin", "audibleasin"):
            continue
        try:
            candidate = _first_str(tags[key])
        except Exception:
            continue
        if not candidate:
            continue
        # A tag can hold anything; only accept something ASIN-shaped.
        match = ASIN_RE.search(candidate.upper())
        if match:
            return match.group(0)
    return None


def _tag_int(tags, *keys) -> Optional[int]:
    for key in keys:
        try:
            raw = tags[key]
        except Exception:
            continue
        if isinstance(raw, (list, tuple)) and raw:
            raw = raw[0]
        # MP4 stores trkn/disk as a (number, total) tuple.
        if isinstance(raw, (list, tuple)) and raw:
            raw = raw[0]
        try:
            return int(str(raw).split("/")[0])
        except (TypeError, ValueError):
            continue
    return None


def _tag_part_index(tags) -> Optional[int]:
    """Part ordering from the tags, disc-major so a multi-disc split sorts right."""
    if tags is None:
        return None
    disc = _tag_int(tags, "disk", "TPOS", "discnumber")
    track = _tag_int(tags, "trkn", "TRCK", "tracknumber")
    if disc is not None and track is not None:
        return disc * 1000 + track
    return track if track is not None else disc


_TRAILING_NUMBER = re.compile(r"(\d+)(?!.*\d)")


def _filename_part_index(path: Path) -> Optional[int]:
    """Last number in the file name — the fallback when nothing is tagged."""
    match = _TRAILING_NUMBER.search(path.stem)
    return int(match.group(1)) if match else None


def read_audio_facts(path: Path) -> dict:
    """Tags worth having from one audio file. Never raises."""
    facts: dict = {}
    try:
        from mutagen import File as MutagenFile
    except ImportError:  # pragma: no cover - dependency is pinned
        return facts

    try:
        media = MutagenFile(str(path))
    except Exception:
        return facts
    if media is None:
        return facts

    tags = getattr(media, "tags", None)
    facts["asin"] = _tag_asin(tags)
    facts["part_index"] = _tag_part_index(tags)
    if tags is not None:
        facts["title"] = _first_str(_lookup(tags, "\xa9nam", "TIT2", "title"))
        facts["authors"] = tuple(
            a for a in (_first_str(_lookup(tags, "\xa9ART", "TPE1", "artist")),
                        _first_str(_lookup(tags, "aART", "TPE2", "albumartist")))
            if a
        )
    info = getattr(media, "info", None)
    length = getattr(info, "length", None)
    if isinstance(length, (int, float)) and length > 0:
        facts["duration"] = float(length)
    return facts


def _lookup(tags, *keys):
    for key in keys:
        try:
            value = tags[key]
        except Exception:
            continue
        if value:
            return value
    return None


def _sidecar_asin(path: Path) -> Optional[str]:
    """ASIN from a `.metadata.json` Libation may have written beside the book."""
    for name in SIDECAR_NAMES:
        # Libation writes either "<book>.metadata.json" beside the file or one
        # "metadata.json" for the folder, depending on version and template.
        for candidate in (path.parent / f"{path.stem}.{name.lstrip('.')}",
                          path.parent / name):
            try:
                if not candidate.is_file():
                    continue
                data = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for key in ("asin", "Asin", "ASIN", "audibleProductId", "AudibleProductId"):
                found = ASIN_RE.search(str(data.get(key) or "").upper())
                if found:
                    return found.group(0)
    return None


def _path_asin(path: Path, root: Path) -> Optional[str]:
    """ASIN by shape anywhere in the path below the root.

    Bounded to the part below the root so a root directory that happens to
    contain an ASIN-shaped segment cannot tag every file under it.
    """
    try:
        relative = str(path.relative_to(root))
    except ValueError:
        relative = path.name
    match = ASIN_RE.search(relative.upper())
    return match.group(0) if match else None


# ── Fuzzy fallback ────────────────────────────────────────────────────────────

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(value: Optional[str]) -> str:
    return _NON_ALNUM.sub(" ", (value or "").lower()).strip()


def _build_fuzzy_index(db: Session) -> dict[str, list[Book]]:
    index: dict[str, list[Book]] = {}
    for book in db.query(Book).filter(Book.is_multipart_parent.is_(False)).all():
        key = normalize_title(book.title)
        if key:
            index.setdefault(key, []).append(book)
    return index


def _fuzzy_match(facts: FileFacts, index: dict[str, list[Book]]) -> Optional[str]:
    """An exact normalized-title hit, corroborated by author or duration.

    Never enough on its own: the caller records it `confirmed=False`, which keeps
    it out of the `liberated` derivation entirely. It exists to be shown to a
    person, not to suppress a download.
    """
    candidates = index.get(normalize_title(facts.title)) or []
    if len(candidates) != 1:
        # No hit, or an ambiguous one. Ambiguity is a reason to say nothing.
        return None
    book = candidates[0]

    if facts.authors and book.authors:
        known = {normalize_title(a) for a in book.authors if a}
        if known & {normalize_title(a) for a in facts.authors}:
            return book.asin

    if facts.duration and book.length_minutes:
        expected = book.length_minutes * 60.0
        if expected > 0 and abs(facts.duration - expected) / expected <= _DURATION_TOLERANCE:
            return book.asin

    return None


# ── The walk ──────────────────────────────────────────────────────────────────

def _walk(root: Root, on_error: Callable[[str], None]) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(
        root.path, onerror=lambda e: on_error(f"{root.path}: {e}")
    ):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            yield Path(dirpath) / name


def _identify(
    path: Path, root: Root, known_asins: set[str], fuzzy_index: dict[str, list[Book]]
) -> Optional[FileFacts]:
    kind = _kind_of(path)
    if kind is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None

    facts = FileFacts(path=path, size=stat.st_size, mtime=stat.st_mtime, kind=kind)

    tag_facts = read_audio_facts(path) if kind == "audio" else {}
    facts.title = tag_facts.get("title")
    facts.authors = tag_facts.get("authors") or ()
    facts.duration = tag_facts.get("duration")
    facts.part_index = tag_facts.get("part_index") or _filename_part_index(path)

    # The ladder, surest first. A candidate only counts if the library actually
    # holds that ASIN, which is what stops a stray regex hit becoming a match.
    for candidate, source in (
        (tag_facts.get("asin"), "tag"),
        (_path_asin(path, root.path), "path"),
        (_sidecar_asin(path), "metadata_json"),
    ):
        if candidate and candidate in known_asins:
            facts.asin, facts.asin_source = candidate, source
            return facts

    if kind == "audio":
        guess = _fuzzy_match(facts, fuzzy_index)
        if guess:
            facts.asin, facts.asin_source, facts.confirmed = guess, "fuzzy", False
    return facts


# ── Reconciling ───────────────────────────────────────────────────────────────

def reconcile(
    db: Session,
    roots: Optional[list[Root]] = None,
    *,
    prune: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
) -> ReconcileResult:
    """Walk the roots and bring `book_files` in line with what is on disk.

    Idempotent and re-runnable: a file whose `(path, size, mtime)` already
    matches a row is skipped without being reopened, which is what keeps a
    5,000-book chapter-split library affordable to re-scan.
    """
    roots = _dedupe_roots(roots if roots is not None else [audiobooks_root()])
    result = ReconcileResult(roots=[str(r.path) for r in roots])
    if not roots:
        raise ReconcileError(
            "No readable audiobook directory. Check AUDIOBOOKS_DIR is mounted."
        )

    run = ReconciliationRun(status="running", roots=result.roots)
    db.add(run)
    db.commit()
    db.refresh(run)
    result.run_id = run.id

    known_asins = {row[0] for row in db.execute(select(Book.asin)).all()}
    existing = {row.path: row for row in db.query(BookFile).all()}
    fuzzy_index = _build_fuzzy_index(db)

    seen_paths: set[str] = set()
    verified_ids: list[int] = []
    now = datetime.now(timezone.utc)

    try:
        for root in roots:
            for path in _walk(root, result.errors.append):
                key = str(path)
                if key in seen_paths:
                    continue

                row = existing.get(key)
                if row is not None:
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if row.size == stat.st_size and row.mtime == stat.st_mtime:
                        seen_paths.add(key)
                        verified_ids.append(row.id)
                        result.files_skipped += 1
                        result.files_scanned += 1
                        if progress and result.files_scanned % 500 == 0:
                            progress(result.files_scanned, result.matched)
                        continue

                facts = _identify(path, root, known_asins, fuzzy_index)
                if facts is None:
                    continue

                seen_paths.add(key)
                result.files_scanned += 1
                if progress and result.files_scanned % 500 == 0:
                    progress(result.files_scanned, result.matched)

                if not facts.asin:
                    if facts.kind == "audio":
                        result.unmatched_total += 1
                        if len(result.unmatched) < UNMATCHED_SAMPLE:
                            result.unmatched.append(key)
                    # A cover or PDF we cannot attribute is not worth reporting.
                    if row is not None:
                        db.delete(row)
                        existing.pop(key, None)
                    continue

                _upsert(db, row, facts, root, now)
                if row is not None:
                    verified_ids.append(row.id)
                result.matched += 1
                if not facts.confirmed:
                    result.unconfirmed += 1

        db.commit()
        _mark_verified(db, verified_ids, now)
        if prune and result.errors:
            # A directory we could not read is not a directory whose files are
            # gone. Its rows are absent from `seen_paths` for the same reason a
            # deleted file's would be, and pruning cannot tell the two apart —
            # so a partial walk never prunes.
            result.errors.append(
                "Skipped pruning: part of the tree could not be read, so a "
                "missing row cannot be told apart from an unreadable one."
            )
        elif prune:
            result.pruned = _prune_missing(db, roots, seen_paths)

        run.status = "complete"
    except Exception as exc:  # noqa: BLE001 — the run row is the only report there is
        db.rollback()
        run = db.get(ReconciliationRun, result.run_id)
        if run is not None:
            run.status = "error"
            run.error_message = str(exc)[:2000]
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
        result.errors.append(str(exc))
        return result

    run.files_scanned = result.files_scanned
    run.books_matched = result.matched
    run.unmatched = result.unmatched_total
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    return result


def _upsert(db: Session, row: Optional[BookFile], facts: FileFacts, root: Root, now) -> None:
    if row is None:
        row = BookFile(path=str(facts.path))
        db.add(row)
    row.book_asin = facts.asin
    row.kind = facts.kind
    row.part_index = facts.part_index
    row.size = facts.size
    row.mtime = facts.mtime
    row.root = root.label
    row.asin_source = facts.asin_source
    row.confirmed = facts.confirmed
    row.verified_at = now


def _mark_verified(db: Session, ids: list[int], now) -> None:
    """One bulk update rather than a write per skipped file."""
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        db.query(BookFile).filter(BookFile.id.in_(chunk)).update(
            {BookFile.verified_at: now}, synchronize_session=False
        )
    if ids:
        db.commit()


def _prune_missing(db: Session, roots: list[Root], seen: set[str]) -> int:
    """Drop rows for files that are gone — but only under roots we just walked.

    A Chaptarr root that failed to mount, or a Chaptarr that was unreachable when
    the roots were discovered, is not evidence that its files were deleted.
    Pruning on that basis would mark a shelf of books as not-downloaded and hand
    them straight back to the auto-download loop.

    Absence has to be positively established, too: "I could not stat it" is not
    "it is gone". Anything but ENOENT leaves the row alone.
    """
    prefixes = [str(r.path) for r in roots]
    removed = 0
    for row in db.query(BookFile).all():
        if row.path in seen:
            continue
        if not any(row.path == p or row.path.startswith(p + os.sep) for p in prefixes):
            continue
        try:
            os.stat(row.path)
            continue  # still there; it was just not reached this pass
        except FileNotFoundError:
            pass
        except OSError:
            continue  # unreadable, not absent
        db.delete(row)
        removed += 1
    if removed:
        db.commit()
    return removed


# ── The gate and the valve ────────────────────────────────────────────────────

def last_completed_run(db: Session) -> Optional[ReconciliationRun]:
    return (
        db.query(ReconciliationRun)
        .filter(ReconciliationRun.status == "complete")
        .order_by(ReconciliationRun.completed_at.desc())
        .first()
    )


def reconciliation_complete(db: Session) -> bool:
    """Whether reconciliation has ever finished a clean pass.

    `_auto_download_if_enabled` fires after *every* successful scan and enqueues
    every book that looks un-downloaded. Run before reconciliation has marked
    what is already on disk, it re-downloads the library. This is the gate that
    makes that impossible.
    """
    return last_completed_run(db) is not None


class BulkEnqueueRefused(Exception):
    """A bulk enqueue was larger than the valve allows, or ran before reconciling."""


def check_bulk_enqueue(
    db: Session, count: int, *, limit: int = DEFAULT_BULK_LIMIT, confirmed: bool = False
) -> None:
    """Refuse an implausibly large bulk download unless someone said so.

    The backstop behind `reconciliation_complete`: if reconciliation ran but got
    it wrong, the first symptom is an enqueue far larger than a real day's new
    releases. Requiring explicit confirmation past `limit` turns a silent
    library-wide re-download into a question.
    """
    if count <= 0:
        return
    if not reconciliation_complete(db):
        raise BulkEnqueueRefused(
            f"Refusing to queue {count} download(s): the library has not been "
            "reconciled against what is already on disk, so books you already "
            "have would be downloaded again."
        )
    if count > limit and not confirmed:
        raise BulkEnqueueRefused(
            f"Refusing to queue {count} download(s) without confirmation "
            f"(limit {limit}). Audible enforces a daily download cap."
        )
