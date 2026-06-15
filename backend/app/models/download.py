from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from ..database import Base


class Scan(Base):
    __tablename__ = "scans"

    id = Column(Integer, primary_key=True)
    status = Column(String, default="running", nullable=False)  # running/complete/error
    books_added = Column(Integer, default=0)
    output = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)


class Download(Base):
    __tablename__ = "downloads"

    id = Column(Integer, primary_key=True)
    book_id = Column(String, nullable=False, index=True)
    book_title = Column(String, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, default="queued", nullable=False)  # queued/running/complete/error
    progress = Column(Integer, default=0)
    error_message = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="downloads")
