import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
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
async def get_accounts(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        accounts = await cli.list_accounts()
        owners = {
            u.audible_account_id: {"owner_name": u.owner_name, "username": u.username}
            for u in db.query(User).filter(User.audible_account_id.isnot(None)).all()
        }
        conn = db.connection()
        rows = conn.execute(
            text("SELECT account_id, added_by_user_id, auto_download FROM audible_account_settings")
        ).fetchall()
        acct_settings = {r[0]: {"added_by_user_id": r[1], "auto_download": bool(r[2])} for r in rows}

        for acc in accounts:
            owner = owners.get(acc["account_id"]) or {}
            acc["owner_name"] = owner.get("owner_name")
            acc["owner_username"] = owner.get("username")
            s = acct_settings.get(acc["account_id"]) or {}
            acc["auto_download"] = s.get("auto_download", False)
            acc["added_by_user_id"] = s.get("added_by_user_id")
        return accounts
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/login/start", response_model=StartLoginResponse)
async def login_start(body: StartLoginRequest, _=Depends(get_current_user)):
    try:
        result = await cli.start_login(body.email, body.locale)
        return StartLoginResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/login/complete", response_model=MessageResponse)
async def login_complete(
    body: CompleteLoginRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        await cli.complete_login(body.session_id, body.response_url)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    # Record which web UI user added this Audible account
    try:
        accounts = await cli.list_accounts()
        conn = db.connection()
        existing = {r[0] for r in conn.execute(
            text("SELECT account_id FROM audible_account_settings")
        ).fetchall()}
        for acc in accounts:
            aid = acc["account_id"]
            if aid not in existing:
                conn.execute(
                    text(
                        # ON CONFLICT rather than SQLite's INSERT OR IGNORE, so
                        # the statement also runs on PostgreSQL.
                        "INSERT INTO audible_account_settings "
                        "(account_id, added_by_user_id, auto_download) VALUES (:aid, :uid, 0) "
                        "ON CONFLICT (account_id) DO NOTHING"
                    ),
                    {"aid": aid, "uid": current_user.id},
                )
        db.commit()
    except Exception:
        pass  # non-fatal — account was already added successfully

    return MessageResponse(message="Account added successfully")


@router.patch("/{account_id}/auto-download", response_model=MessageResponse)
async def toggle_auto_download(
    account_id: str,
    body: dict,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.connection()
    row = conn.execute(
        text("SELECT added_by_user_id FROM audible_account_settings WHERE account_id = :aid"),
        {"aid": account_id},
    ).first()

    if not current_user.is_admin:
        if row is None or row[0] != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only manage auto-download for accounts you added",
            )

    enabled = int(bool(body.get("auto_download", False)))
    adder_id = (row[0] if row else None) or current_user.id

    conn.execute(
        text(
            "INSERT INTO audible_account_settings (account_id, added_by_user_id, auto_download) "
            "VALUES (:aid, :uid, :val) "
            "ON CONFLICT(account_id) DO UPDATE SET auto_download = :val"
        ),
        {"aid": account_id, "uid": adder_id, "val": enabled},
    )
    db.commit()
    return MessageResponse(message="Auto-download updated")


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
