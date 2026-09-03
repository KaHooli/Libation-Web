from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ChaptarrSettings(BaseModel):
    """Chaptarr connection settings. The API key is never sent back to the client."""

    enabled: bool = False
    url: str = ""
    import_mode: str = "auto"
    auto_import: bool = False
    path_from: str = ""
    path_to: str = ""
    skip_existing: bool = False
    skip_when: str = "has_file"
    api_key_set: bool = False


class ChaptarrStatus(BaseModel):
    """Availability, for clients that only need to know whether to offer the action."""

    enabled: bool = False
    configured: bool = False
    # Whether downloads are being checked against Chaptarr's library first.
    skip_existing: bool = False
    skip_when: str = "has_file"


class ChaptarrSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    url: Optional[str] = None
    import_mode: Optional[str] = None
    auto_import: Optional[bool] = None
    path_from: Optional[str] = None
    path_to: Optional[str] = None
    skip_existing: Optional[bool] = None
    skip_when: Optional[str] = None
    # Omit to leave the stored key untouched; send "" to clear it.
    api_key: Optional[str] = None


class ChaptarrRootFolder(BaseModel):
    id: Optional[int] = None
    path: Optional[str] = None
    name: Optional[str] = None


class ChaptarrConnectionTest(BaseModel):
    app_name: str
    version: str
    root_folders: list[ChaptarrRootFolder] = Field(default_factory=list)


class ChaptarrImportResponse(BaseModel):
    id: int
    book_id: str
    book_title: Optional[str] = None
    status: str
    matched_by: Optional[str] = None
    command_id: Optional[int] = None
    file_path: Optional[str] = None
    message: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChaptarrImportRequest(BaseModel):
    book_ids: list[str] = Field(default_factory=list, max_length=200)


class ChaptarrCheckRequest(BaseModel):
    book_ids: list[str] = Field(default_factory=list, max_length=1000)


class ChaptarrBookCheck(BaseModel):
    """What Chaptarr knows about one Audible ASIN."""

    book_id: str
    in_chaptarr: bool
    has_file: bool
    title: Optional[str] = None
    chaptarr_book_id: Optional[int] = None
    # True when this book would not be downloaded from Audible, under the
    # current `skip_when` setting.
    would_skip: bool


class ChaptarrCheckResponse(BaseModel):
    # Echoed so a client can explain *why* a book would or wouldn't be skipped.
    skip_existing: bool
    skip_when: str
    results: list[ChaptarrBookCheck] = Field(default_factory=list)
