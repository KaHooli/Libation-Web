from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response

from .auth import get_current_user
from ..schemas.library import LibraryResponse
from ..services.libation import get_library, get_book_cover_path

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/books", response_model=LibraryResponse)
def list_books(
    search: str = Query(""),
    sort_by: str = Query("date_added"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(get_current_user),
):
    return get_library(
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )


@router.get("/covers/{book_id}")
def get_cover(book_id: str):
    path = get_book_cover_path(book_id)
    if not path:
        return Response(status_code=404)
    return FileResponse(str(path))
