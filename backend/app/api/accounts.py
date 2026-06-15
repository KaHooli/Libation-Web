import asyncio
from fastapi import APIRouter, Depends, HTTPException, status

from .auth import get_current_user
from ..schemas.accounts import (
    AccountResponse, StartLoginRequest, StartLoginResponse,
    CompleteLoginRequest, MessageResponse,
)
from ..services import cli

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountResponse])
async def get_accounts(_=Depends(get_current_user)):
    try:
        return await cli.list_accounts()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/login/start", response_model=StartLoginResponse)
async def login_start(body: StartLoginRequest, _=Depends(get_current_user)):
    try:
        result = await cli.start_login(body.email, body.locale)
        return StartLoginResponse(**result)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/login/complete", response_model=MessageResponse)
async def login_complete(body: CompleteLoginRequest, _=Depends(get_current_user)):
    try:
        await cli.complete_login(body.session_id, body.response_url)
        return MessageResponse(message="Account added successfully")
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
