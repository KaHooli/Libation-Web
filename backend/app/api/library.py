from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import FileResponse, Response, RedirectResponse

from .auth import get_current_user
from ..schemas.library import LibraryResponse
from ..services.libation import get_library, get_book_cover_path, get_cover_image_hash, get_books_by_account

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


@router.get("/my-books", response_model=LibraryResponse)
def my_books(
    search: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user=Depends(get_current_user),
):
    if not current_user.audible_account_id:
        raise HTTPException(status_code=400, detail="No Audible account linked to your profile")
    return get_books_by_account(
        account_id=current_user.audible_account_id,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get("/covers/{book_id}")
def get_cover(book_id: str):
    path = get_book_cover_path(book_id)
    if path:
        return FileResponse(str(path))
    image_hash = get_cover_image_hash(book_id)
    if image_hash:
        return RedirectResponse(
            url=f"https://m.media-amazon.com/images/I/{image_hash}._SL500_.jpg",
            status_code=302,
        )
    return Response(status_code=404)
