from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.auth import (
    LoginRequest, TwoFactorRequest, EnableTwoFactorRequest,
    DisableTwoFactorRequest, TokenResponse, TwoFactorRequiredResponse,
    UserResponse, SetupTwoFactorResponse, MessageResponse, ChangePasswordRequest,
    ChangeUsernameRequest,
)
from ..schemas.users import SessionResponse
from ..services import auth as auth_svc
from ..services import oidc as oidc_svc
from ..services.logger import get_logger
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


def _require_password_login() -> None:
    """Refuse username/password sign-in when the deployment has turned it off.

    Set ALLOW_PASSWORD_LOGIN=true to bring it back — the way out if the identity
    provider is misconfigured and nobody can get in.
    """
    if not settings.password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Password sign-in is disabled; use single sign-on. An administrator "
                "can re-enable it by setting ALLOW_PASSWORD_LOGIN=true."
            ),
        )


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
    _require_password_login()
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
    # The 2FA step only ever follows a password login, so it is gated too —
    # otherwise a temp token minted before the switch would still complete.
    _require_password_login()
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


# ── Single sign-on ────────────────────────────────────────────────────────────

@router.get("/config")
def auth_config():
    """What sign-in methods this deployment offers.

    Public and unauthenticated by necessity — the login page reads it before
    anyone has credentials. It exposes only which methods exist, never the
    issuer, client id or secret.
    """
    return {
        "password_login_enabled": settings.password_login_enabled,
        "oidc_enabled": settings.oidc_configured,
        "oidc_provider_name": settings.OIDC_PROVIDER_NAME,
    }


def _oidc_redirect_uri(request: Request) -> str:
    """The callback URL registered with the provider.

    Derived from the request when OIDC_REDIRECT_URL is blank, which is correct
    for a directly exposed container. Behind a reverse proxy that terminates
    TLS or rewrites the host, set it explicitly — uvicorn is not started with
    --proxy-headers, so the derived URL would use the internal scheme and host.
    """
    configured = settings.OIDC_REDIRECT_URL.strip()
    if configured:
        return configured
    return str(request.url_for("oidc_callback"))


def _login_page_redirect(error: str | None = None, next_path: str | None = None):
    target = next_path or "/"
    if error:
        target = f"/login?{urlencode({'sso_error': error})}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/oidc/login")
def oidc_login(request: Request, next: str | None = None, db: Session = Depends(get_db)):
    if not settings.oidc_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Single sign-on is not configured",
        )
    try:
        url = oidc_svc.begin_login(db, _oidc_redirect_uri(request), next)
    except oidc_svc.OidcError as exc:
        get_logger().error("[oidc] Could not start sign-in: %s", exc)
        return _login_page_redirect(error=str(exc))
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/oidc/callback")
def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """Where the provider sends the browser back.

    On success this sets the refresh cookie and redirects into the app; the SPA
    already calls /api/auth/refresh on load, so the access token never has to
    travel in a URL where it would land in history and proxy logs.
    """
    if not settings.oidc_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Single sign-on is not configured",
        )

    if error:
        get_logger().warning("[oidc] Provider returned an error: %s", error)
        return _login_page_redirect(error=error_description or error)
    if not code or not state:
        return _login_page_redirect(error="The provider's response was incomplete.")

    try:
        user, next_path = oidc_svc.complete_login(db, code, state)
    except oidc_svc.OidcError as exc:
        get_logger().warning("[oidc] Sign-in failed: %s", exc)
        return _login_page_redirect(error=str(exc))

    raw_refresh = auth_svc.create_session(
        db, user.id,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    get_logger().info("[oidc] Signed in %r via SSO", user.username)

    redirect = _login_page_redirect(next_path=next_path)
    _set_refresh_cookie(redirect, raw_refresh)
    return redirect


@router.post("/oidc/test")
def oidc_test(current_user=Depends(get_current_user)):
    """Reach the provider and report what discovery returned. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    if not settings.oidc_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Single sign-on is not configured",
        )
    oidc_svc.clear_cache()
    try:
        return oidc_svc.provider_check()
    except oidc_svc.OidcError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


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


@router.get("/default-credentials")
def default_credentials(current_user=Depends(get_current_user)):
    is_default = (
        current_user.username == settings.ADMIN_USERNAME
        and auth_svc.verify_password(settings.ADMIN_PASSWORD, current_user.hashed_password)
    )
    return {"using_default_credentials": is_default}


@router.patch("/me", response_model=UserResponse)
def update_me(
    body: dict,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if "audible_account_id" in body:
        current_user.audible_account_id = body["audible_account_id"] or None
    if "owner_name" in body:
        current_user.owner_name = (body["owner_name"] or "").strip() or None
    db.commit()
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


@router.post("/change-username", response_model=UserResponse)
def change_username(
    body: ChangeUsernameRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not auth_svc.verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    new_username = body.new_username.strip()
    if len(new_username) < 3:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Username must be at least 3 characters")
    from ..models.user import User
    if db.query(User).filter(User.username == new_username, User.id != current_user.id).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
    current_user.username = new_username
    db.commit()
    db.refresh(current_user)
    return UserResponse.model_validate(current_user)


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
