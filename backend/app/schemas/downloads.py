from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class ScanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    books_added: int
    output: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class DownloadRequest(BaseModel):
    book_id: str
    book_title: Optional[str] = None
    # Download even if Chaptarr already has the book ("Download anyway").
    force: bool = False


class DownloadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: str
    book_title: Optional[str] = None
    status: str
    progress: int
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
