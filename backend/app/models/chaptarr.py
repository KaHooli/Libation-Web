from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from datetime import datetime, timezone
from ..database import Base


class ChaptarrImport(Base):
    """One Chaptarr interaction for a book.

    Either an attempt at handing a downloaded book to Chaptarr, or — with
    ``status="skipped"`` and ``matched_by="already_in_chaptarr"`` — a download we
    deliberately did not make because Chaptarr already had the book. Recording
    the skip is what keeps it from looking like the book was silently missed.
    """

    __tablename__ = "chaptarr_imports"

    id = Column(Integer, primary_key=True)
    book_id = Column(String, nullable=False, index=True)   # Audible ASIN
    book_title = Column(String, nullable=True)
    # skipped (not configured, or already in Chaptarr) / running / complete / error
    status = Column(String, default="running", nullable=False)
    # "asin" when Chaptarr resolved az:{ASIN}, "folder_scan" for the fallback,
    # "already_in_chaptarr" when this records a download we skipped
    matched_by = Column(String, nullable=True)
    command_id = Column(Integer, nullable=True)             # Chaptarr's command id
    file_path = Column(String, nullable=True)               # path as Chaptarr sees it
    message = Column(String, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
