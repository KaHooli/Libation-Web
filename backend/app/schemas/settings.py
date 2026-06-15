from pydantic import BaseModel
from typing import Optional


class LibationSettings(BaseModel):
    decrypt_to_lossy: Optional[bool] = None
    split_files_by_chapter: Optional[bool] = None
    download_episodes: Optional[bool] = None
    create_cue_sheet: Optional[bool] = None
    save_cover_art_to_file: Optional[bool] = None
    allow_audiobook_overwrite: Optional[bool] = None
    strip_audible_brand_audio: Optional[bool] = None
    strip_unabridged: Optional[bool] = None
    books_directory: Optional[str] = None


class DownloadsPerUser(BaseModel):
    username: str
    count: int


class AppStats(BaseModel):
    total_books: int
    total_downloads: int
    accounts_count: int
    downloads_per_user: list[DownloadsPerUser]
