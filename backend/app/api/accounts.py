import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from sqlalchemy.orm import Session

from .auth import get_current_user
from ..database import get_db
from ..models.user import User
from ..schemas.accounts import (
    AccountResponse, StartLoginRequest, StartLoginResponse,
    CompleteLoginRequest, MessageResponse,
)
from ..services import cli
from ..config import settings

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountResponse])
async def get_accounts(_=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        accounts = await cli.list_accounts()
        # Attach owner info from users table
        owners = {
            u.audible_account_id: {"owner_name": u.owner_name, "username": u.username}
            for u in db.query(User).filter(User.audible_account_id.isnot(None)).all()
        }
        for acc in accounts:
            owner = owners.get(acc["account_id"]) or {}
            acc["owner_name"] = owner.get("owner_name")
            acc["owner_username"] = owner.get("username")
        return accounts
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


@router.delete("/{account_id}", response_model=MessageResponse)
async def delete_account(account_id: str, _=Depends(get_current_user)):
    accounts_file = Path(settings.LIBATION_CONFIG) / "AccountsSettings.json"
    if not accounts_file.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AccountsSettings.json not found")
    try:
        data = json.loads(accounts_file.read_text())
        original = data.get("Accounts", [])
        filtered = [a for a in original if a.get("AccountId") != account_id]
        if len(filtered) == len(original):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        data["Accounts"] = filtered
        accounts_file.write_text(json.dumps(data, indent=2))
        return MessageResponse(message="Account removed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
