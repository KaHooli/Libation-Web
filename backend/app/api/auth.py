from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.auth import (
    LoginRequest, TwoFactorRequest, EnableTwoFactorRequest,
    DisableTwoFactorRequest, TokenResponse, TwoFactorRequiredResponse,
    UserResponse, SetupTwoFactorResponse, MessageResponse, ChangePasswordRequest,
)
from ..schemas.users import SessionResponse
from ..services import auth as auth_svc
from ..config import settings
from ..limiter import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)

COOKIE_NAME = "refresh_token"
COOKIE_OPTS = dict(
    httponly=True,
    samesite="lax",
    secure=False,  # set True behind HTTPS in production
    max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    path="/api/auth",
)


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, **COOKIE_OPTS)


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/api/auth")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
):
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user_id = auth_svc.decode_token(credentials.credentials, "access")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    user = auth_svc.get_user_by_id(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


@router.post("/login", response_model=TokenResponse | TwoFactorRequiredResponse)
@limiter.limit("20/minute")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = auth_svc.authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if user.totp_enabled:
        temp_token = auth_svc.create_temp_token(user.id)
        return TwoFactorRequiredResponse(temp_token=temp_token)

    raw_refresh = auth_svc.create_session(
        db, user.id,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, raw_refresh)
    return TokenResponse(
        access_token=auth_svc.create_access_token(user.id),
        user=UserResponse.model_validate(user),
    )


@router.post("/verify-2fa", response_model=TokenResponse)
@limiter.limit("10/minute")
def verify_2fa(body: TwoFactorRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user_id = auth_svc.decode_token(body.temp_token, "2fa_pending")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired 2FA session")

    user = auth_svc.get_user_by_id(db, user_id)
    if not user or not user.totp_enabled or not user.totp_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="2FA not configured")

    if not auth_svc.verify_totp(user.totp_secret, body.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2FA code")

    raw_refresh = auth_svc.create_session(
        db, user.id,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, raw_refresh)
    return TokenResponse(
        access_token=auth_svc.create_access_token(user.id),
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    raw_token = request.cookies.get(COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")

    session = auth_svc.validate_refresh_token(db, raw_token)
    if not session:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    user = auth_svc.get_user_by_id(db, session.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return TokenResponse(
        access_token=auth_svc.create_access_token(user.id),
        user=UserResponse.model_validate(user),
    )


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw_token = request.cookies.get(COOKIE_NAME)
    if raw_token:
        auth_svc.revoke_session(db, raw_token)
    _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=UserResponse)
def me(current_user=Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@router.post("/setup-2fa", response_model=SetupTwoFactorResponse)
def setup_2fa(current_user=Depends(get_current_user)):
    if current_user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="2FA already enabled")
    secret = auth_svc.generate_totp_secret()
    uri = auth_svc.get_totp_uri(secret, current_user.username)
    return SetupTwoFactorResponse(
        secret=secret,
        qr_uri=uri,
        qr_image=auth_svc.generate_qr_image(uri),
    )


@router.post("/enable-2fa", response_model=MessageResponse)
def enable_2fa(body: EnableTwoFactorRequest, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="2FA already enabled")
    # secret was just generated; the client must send it back with the code
    # but we can't verify without it — use a two-step: setup returns secret,
    # client calls enable with {secret, code}
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use /enable-2fa-confirm with secret")


@router.post("/enable-2fa-confirm", response_model=MessageResponse)
def enable_2fa_confirm(
    body: dict,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    secret = body.get("secret", "")
    code = body.get("code", "")
    if not auth_svc.verify_totp(secret, code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2FA code")
    current_user.totp_secret = secret
    current_user.totp_enabled = True
    db.commit()
    return MessageResponse(message="2FA enabled successfully")


@router.post("/disable-2fa", response_model=MessageResponse)
def disable_2fa(
    body: DisableTwoFactorRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="2FA is not enabled")
    if not auth_svc.verify_totp(current_user.totp_secret, body.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2FA code")
    current_user.totp_secret = None
    current_user.totp_enabled = False
    db.commit()
    auth_svc.revoke_all_sessions(db, current_user.id)
    return MessageResponse(message="2FA disabled. Please log in again.")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    body: ChangePasswordRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not auth_svc.verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    current_user.hashed_password = auth_svc.hash_password(body.new_password)
    db.commit()
    auth_svc.revoke_all_sessions(db, current_user.id)
    return MessageResponse(message="Password changed. Please log in again.")


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return auth_svc.get_sessions_for_user(db, current_user.id)


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
def revoke_session(
    session_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not auth_svc.revoke_session_by_id(db, session_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return MessageResponse(message="Session revoked")


@router.delete("/sessions", response_model=MessageResponse)
def revoke_all_sessions(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    auth_svc.revoke_all_sessions(db, current_user.id)
    return MessageResponse(message="All sessions revoked")
