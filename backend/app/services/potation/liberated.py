"""Whether a book counts as already downloaded.

    liberated = liberated_override IF NOT NULL
                ELSE EXISTS(book_files WHERE kind='audio' AND confirmed)

Purely deriving it from the files on disk would kill the "I already have this,
stop offering it" workflow that `PATCH /api/liberate/books/{id}` serves today —
a user who keeps their library elsewhere, or who deliberately skipped a title,
has no file for us to find. So the override is tri-state: NULL derives, 1 and 0
are a person's explicit answer and always win.

The `confirmed` requirement is what keeps a fuzzy reconciliation match from
suppressing a download. An unconfirmed row is a suggestion for someone to look
at, never an assertion that the book is on disk.
"""

from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ...models.potation import Book, BookFile

#: A file only counts towards `liberated` when it is the book itself and the
#: match behind it was certain.
_HAS_FILE = and_(BookFile.kind == "audio", BookFile.confirmed.is_(True))


def _derived_subquery():
    return (
        select(BookFile.book_asin)
        .where(_HAS_FILE)
        .where(BookFile.book_asin == Book.asin)
        .exists()
    )


def is_liberated(db: Session, asin: str) -> bool:
    row = db.execute(
        select(Book.liberated_override, _derived_subquery()).where(Book.asin == asin)
    ).first()
    if row is None:
        return False
    override, derived = row
    return bool(override) if override is not None else bool(derived)


def liberated_map(db: Session, asins: Optional[Iterable[str]] = None) -> dict[str, bool]:
    """`{asin: liberated}` for a page of books, in one query.

    Pass the ASINs on the page rather than calling `is_liberated` per row; the
    grid renders up to 200 at a time.
    """
    stmt = select(Book.asin, Book.liberated_override, _derived_subquery())
    if asins is not None:
        wanted = list(asins)
        if not wanted:
            return {}
        stmt = stmt.where(Book.asin.in_(wanted))
    return {
        asin: (bool(override) if override is not None else bool(derived))
        for asin, override, derived in db.execute(stmt).all()
    }


def liberated_asins(db: Session) -> set[str]:
    """Every book currently counting as downloaded."""
    return {asin for asin, value in liberated_map(db).items() if value}


def not_liberated_asins(db: Session, account_id: Optional[str] = None) -> list[str]:
    """Books a download run would consider fetching.

    Multi-part *parents* are excluded: a `MultiPartBook` has no downloadable
    content of its own, so enqueueing one downloads nothing and reports an
    error. The parts are separate rows and are included on their own merits.
    """
    stmt = select(Book.asin, Book.liberated_override, _derived_subquery()).where(
        Book.is_multipart_parent.is_(False)
    )
    if account_id:
        stmt = stmt.where(Book.account_id == account_id)
    return [
        asin
        for asin, override, derived in db.execute(stmt).all()
        if not (bool(override) if override is not None else bool(derived))
    ]


def set_override(db: Session, asin: str, liberated: Optional[bool]) -> bool:
    """Record a person's explicit answer, or `None` to go back to deriving it."""
    book = db.get(Book, asin)
    if book is None:
        return False
    book.liberated_override = None if liberated is None else int(bool(liberated))
    db.commit()
    return True


def unconfirmed_files(db: Session, limit: int = 200) -> list[BookFile]:
    """Fuzzy matches awaiting a human decision.

    These are the rows reconciliation could not prove. They are deliberately
    inert until someone confirms them, so this is the queue that has to be shown
    somewhere — otherwise a correct match sits unused and the book is downloaded
    a second time.
    """
    return (
        db.query(BookFile)
        .filter(BookFile.confirmed.is_(False))
        .order_by(BookFile.discovered_at.desc())
        .limit(limit)
        .all()
    )


def confirm_file(db: Session, file_id: int, confirmed: bool = True) -> bool:
    """Accept a fuzzy match, or delete it if it was wrong."""
    row = db.get(BookFile, file_id)
    if row is None:
        return False
    if confirmed:
        row.confirmed = True
    else:
        db.delete(row)
    db.commit()
    return True


def audio_paths(db: Session, asin: str) -> list[str]:
    """The book's audio files, in part order.

    Replaces `libation.get_audio_file_paths` at the Phase C cut-over. Ordering
    comes from `part_index` rather than the path, so it does not depend on a
    naming convention Chaptarr may already have renamed away.
    """
    rows = (
        db.query(BookFile)
        .filter(BookFile.book_asin == asin, BookFile.kind == "audio",
                BookFile.confirmed.is_(True))
        .all()
    )
    rows.sort(key=lambda r: (r.part_index is None, r.part_index or 0, r.path))
    return [r.path for r in rows]
