from pydantic import BaseModel
from typing import Optional


class LiberateBook(BaseModel):
    book_id: str
    title: str
    authors: Optional[str] = None
    narrators: Optional[str] = None
    series_name: Optional[str] = None
    series_index: Optional[str] = None
    length_minutes: Optional[int] = None
    date_added: Optional[str] = None
    subtitle: Optional[str] = None
    content_type: Optional[str] = None
    language: Optional[str] = None
    is_abridged: Optional[bool] = None
    is_audible_plus: Optional[bool] = None
    community_rating: Optional[float] = None
    # "not_liberated" | "liberated" | "error" | "downloading"
    liberate_status: str = "not_liberated"
    download_progress: Optional[int] = None


class LiberateResponse(BaseModel):
    books: list[LiberateBook]
    total: int


class DownloadCapStatus(BaseModel):
    cap: Optional[int]
    used: int
    remaining: Optional[int]
    resets_at: Optional[str]
