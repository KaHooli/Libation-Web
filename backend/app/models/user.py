from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from ..database import Base

DEFAULT_PERMISSIONS = {
    "can_download": True,
    "can_scan": True,
    "can_manage_accounts": True,
    "can_liberate": True,
    "can_remove_downloads": False,
}


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    permissions = Column(JSON, nullable=True)
    download_cap = Column(Integer, nullable=True)
    audible_account_id = Column(String, nullable=True)
    owner_name = Column(String, nullable=True)
    #: OIDC identity. `sub` is the only claim a provider guarantees is stable —
    #: usernames and email addresses can be reassigned — so it is what links a
    #: local user to their SSO account. Scoped by issuer so switching providers
    #: cannot silently hand someone else's account over.
    oidc_subject = Column(String, nullable=True, index=True)
    oidc_issuer = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    downloads = relationship("Download", back_populates="user", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    refresh_token_hash = Column(String, unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)

    user = relationship("User", back_populates="sessions")


class OidcLoginState(Base):
    """One in-flight OIDC authorization request.

    The `state`, `nonce` and PKCE verifier have to survive the round trip to the
    provider, and the callback may land on a different worker than the one that
    started the flow — so they live in the database rather than in memory.

    Rows are single-use: `consumed_at` is stamped on the first successful
    callback, which is what stops a replayed authorization code.
    """

    __tablename__ = "oidc_login_states"

    id = Column(Integer, primary_key=True)
    state = Column(String, unique=True, nullable=False, index=True)
    nonce = Column(String, nullable=False)
    code_verifier = Column(String, nullable=False)
    redirect_uri = Column(String, nullable=False)
    #: Where to send the browser once the login completes.
    next_path = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
