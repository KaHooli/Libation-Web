import hashlib
import secrets
import base64
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

import pyotp
import qrcode
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from ..config import settings
from ..models.user import User, Session as UserSession

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Passwords ──────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT ────────────────────────────────────────────────────────────────────

def _make_token(data: dict, expires_delta: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    return jwt.encode(
        {**data, "exp": expire},
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def create_access_token(user_id: int) -> str:
    return _make_token(
        {"sub": str(user_id), "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_temp_token(user_id: int) -> str:
    """Short-lived token used only to complete the 2FA verification step."""
    return _make_token(
        {"sub": str(user_id), "type": "2fa_pending"},
        timedelta(minutes=settings.TEMP_TOKEN_EXPIRE_MINUTES),
    )


def decode_token(token: str, expected_type: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != expected_type:
            return None
        user_id = payload.get("sub")
        return int(user_id) if user_id else None
    except JWTError:
        return None


# ── Refresh tokens ─────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user_id: int, user_agent: str = None, ip: str = None) -> str:
    raw_token = secrets.token_urlsafe(64)
    expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    session = UserSession(
        user_id=user_id,
        refresh_token_hash=_hash_token(raw_token),
        expires_at=expires,
        user_agent=user_agent,
        ip_address=ip,
    )
    db.add(session)
    db.commit()
    return raw_token


def validate_refresh_token(db: Session, raw_token: str) -> Optional[UserSession]:
    token_hash = _hash_token(raw_token)
    session = db.query(UserSession).filter(
        UserSession.refresh_token_hash == token_hash
    ).first()

    if not session:
        return None
    if session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None

    session.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return session


def revoke_session(db: Session, raw_token: str) -> None:
    token_hash = _hash_token(raw_token)
    session = db.query(UserSession).filter(
        UserSession.refresh_token_hash == token_hash
    ).first()
    if session:
        db.delete(session)
        db.commit()


def revoke_all_sessions(db: Session, user_id: int) -> None:
    db.query(UserSession).filter(UserSession.user_id == user_id).delete()
    db.commit()


# ── TOTP ───────────────────────────────────────────────────────────────────

def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name="Libation",
    )


def generate_qr_image(uri: str) -> str:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# ── User helpers ───────────────────────────────────────────────────────────

def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
