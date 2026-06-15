from pydantic import BaseModel
from typing import Optional


class Book(BaseModel):
    book_id: str
    title: str
    length_minutes: Optional[int] = None
    description: Optional[str] = None
    authors: Optional[str] = None
    narrators: Optional[str] = None
    series_name: Optional[str] = None
    series_index: Optional[str] = None
    date_added: Optional[str] = None
    date_published: Optional[str] = None
    is_abridged: Optional[bool] = None


class LibraryResponse(BaseModel):
    books: list[Book]
    total: int
    page: int
    page_size: int
    empty_reason: Optional[str] = None
