"""OpenID Connect single sign-on.

Standard authorization-code flow with PKCE:

  1. `begin_login` mints `state`, `nonce` and a PKCE verifier, stores them, and
     returns the provider's authorization URL.
  2. The provider sends the browser back to `complete_login`, which checks the
     state is one we issued and have not already used, swaps the code for
     tokens, validates the ID token against the provider's JWKS, and maps the
     claims onto a local user.

Trust model: the provider is authoritative for identity. Someone who controls
it can assert any username or email and will be signed in as the matching local
user. That is inherent to SSO, but it is the reason `OIDC_AUTO_CREATE_USERS`
exists and why linking prefers the immutable `sub` over anything human-readable.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from jose import jwt
from jose.exceptions import JWTError
from sqlalchemy.orm import Session

from ..config import settings
from ..models.user import OidcLoginState, User
from .auth import hash_password
from .logger import get_logger

#: How long a started login may sit unfinished.
LOGIN_STATE_TTL = timedelta(minutes=10)

#: Discovery and JWKS documents are cached for this long.
_DOC_TTL_SECONDS = 300

#: Asymmetric signatures only. Allowing an HMAC algorithm while verifying
#: against a JWKS invites algorithm confusion — a forged token signed with the
#: public key as its HMAC secret would verify.
ALLOWED_ALGORITHMS = frozenset({
    "RS256", "RS384", "RS512",
    "ES256", "ES384", "ES512",
    "PS256", "PS384", "PS512",
})

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

_cache: dict[str, tuple[float, Any]] = {}


class OidcError(Exception):
    """SSO could not complete. The message is safe to show a user."""


# ── Provider metadata ─────────────────────────────────────────────────────────

def _cached(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and entry[0] > time.monotonic():
        return entry[1]
    return None


def _store(key: str, value: Any) -> Any:
    _cache[key] = (time.monotonic() + _DOC_TTL_SECONDS, value)
    return value


def clear_cache() -> None:
    _cache.clear()


def _issuer() -> str:
    return settings.OIDC_ISSUER.strip().rstrip("/")


def discovery() -> dict:
    """The provider's OpenID configuration document."""
    issuer = _issuer()
    if not issuer:
        raise OidcError("No OIDC issuer is configured.")

    key = f"discovery:{issuer}"
    cached = _cached(key)
    if cached is not None:
        return cached

    url = f"{issuer}/.well-known/openid-configuration"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
            doc = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"Could not read OIDC discovery from {url}: {exc}") from exc

    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(required):
            raise OidcError(f"OIDC discovery at {url} is missing {required}.")
    return _store(key, doc)


def jwks() -> dict:
    uri = discovery()["jwks_uri"]
    key = f"jwks:{uri}"
    cached = _cached(key)
    if cached is not None:
        return cached

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            resp = client.get(uri)
            resp.raise_for_status()
            doc = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"Could not read the provider's signing keys: {exc}") from exc
    return _store(key, doc)


def provider_check() -> dict:
    """Reach the provider and report what was found. For the settings UI."""
    doc = discovery()
    keys = jwks().get("keys", [])
    return {
        "issuer": doc.get("issuer", _issuer()),
        "authorization_endpoint": doc.get("authorization_endpoint"),
        "token_endpoint": doc.get("token_endpoint"),
        "signing_keys": len(keys),
        "scopes_supported": doc.get("scopes_supported"),
    }


# ── Starting a login ──────────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def safe_next_path(raw: Optional[str]) -> Optional[str]:
    """Only same-origin paths, so the callback cannot be used as an open redirect."""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return None
    return raw


def _prune_expired(db: Session) -> None:
    db.query(OidcLoginState).filter(
        OidcLoginState.expires_at < datetime.now(timezone.utc)
    ).delete(synchronize_session=False)


def begin_login(db: Session, redirect_uri: str, next_path: Optional[str] = None) -> str:
    """Record a pending login and return the URL to send the browser to."""
    doc = discovery()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    _prune_expired(db)
    db.add(OidcLoginState(
        state=state,
        nonce=nonce,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        next_path=safe_next_path(next_path),
        expires_at=datetime.now(timezone.utc) + LOGIN_STATE_TTL,
    ))
    db.commit()

    params = {
        "response_type": "code",
        "client_id": settings.OIDC_CLIENT_ID.strip(),
        "redirect_uri": redirect_uri,
        "scope": settings.OIDC_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{doc['authorization_endpoint']}?{urlencode(params)}"


# ── Completing a login ────────────────────────────────────────────────────────

def _consume_state(db: Session, state: str) -> OidcLoginState:
    row = db.query(OidcLoginState).filter(OidcLoginState.state == state).first()
    if row is None:
        raise OidcError("This sign-in link is not one we issued. Please try again.")
    if row.consumed_at is not None:
        raise OidcError("This sign-in link has already been used. Please try again.")

    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise OidcError("This sign-in took too long. Please try again.")

    # Stamped before the code is exchanged, so a replayed callback loses the
    # race rather than triggering a second exchange.
    row.consumed_at = datetime.now(timezone.utc)
    db.commit()
    return row


def _exchange_code(code: str, redirect_uri: str, verifier: str) -> dict:
    doc = discovery()
    client_id = settings.OIDC_CLIENT_ID.strip()
    client_secret = settings.OIDC_CLIENT_SECRET.strip()

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "client_id": client_id,
    }

    supported = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    auth = None
    if "client_secret_basic" in supported:
        auth = (client_id, client_secret)
    else:
        data["client_secret"] = client_secret

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            resp = client.post(doc["token_endpoint"], data=data, auth=auth)
    except httpx.HTTPError as exc:
        raise OidcError(f"Could not reach the provider's token endpoint: {exc}") from exc

    if resp.status_code != 200:
        # The body can carry a client secret in an echoed request; log only the
        # provider's own error code.
        try:
            detail = resp.json().get("error", resp.status_code)
        except ValueError:
            detail = resp.status_code
        raise OidcError(f"The provider rejected the sign-in ({detail}).")

    payload = resp.json()
    if not payload.get("id_token"):
        raise OidcError("The provider did not return an ID token.")
    return payload


def _validate_id_token(id_token: str, access_token: Optional[str], nonce: str) -> dict:
    doc = discovery()
    advertised = doc.get("id_token_signing_alg_values_supported") or ["RS256"]
    algorithms = sorted(set(advertised) & ALLOWED_ALGORITHMS)
    if not algorithms:
        raise OidcError(
            "The provider offers no ID token signing algorithm this app accepts "
            f"(offered: {', '.join(advertised)})."
        )

    try:
        claims = jwt.decode(
            id_token,
            jwks(),
            algorithms=algorithms,
            audience=settings.OIDC_CLIENT_ID.strip(),
            issuer=doc.get("issuer", _issuer()),
            access_token=access_token,
            options={"verify_at_hash": access_token is not None},
        )
    except JWTError as exc:
        raise OidcError(f"The provider's ID token could not be verified: {exc}") from exc

    if not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise OidcError("The ID token nonce did not match. Please try again.")
    if not claims.get("sub"):
        raise OidcError("The provider did not identify the user (no `sub` claim).")
    return claims


# ── Mapping claims onto a local user ──────────────────────────────────────────

def _derive_username(claims: dict) -> str:
    for value in (
        claims.get(settings.OIDC_USERNAME_CLAIM),
        claims.get("preferred_username"),
        claims.get(settings.OIDC_EMAIL_CLAIM),
        claims.get("email"),
    ):
        text = (value or "").strip() if isinstance(value, str) else ""
        if text:
            return text.split("@", 1)[0] if "@" in text else text
    return f"sso-{claims['sub'][:12]}"


def _unusable_password() -> str:
    """A hash of a value nobody holds, so an SSO user cannot be password-authed."""
    return hash_password(secrets.token_urlsafe(64))


def _is_admin_by_group(claims: dict) -> Optional[bool]:
    """True/False when the group mapping is configured, None when it is not."""
    group = settings.OIDC_ADMIN_GROUP.strip()
    if not group:
        return None
    raw = claims.get(settings.OIDC_GROUPS_CLAIM)
    if isinstance(raw, str):
        groups = [raw]
    elif isinstance(raw, (list, tuple)):
        groups = [str(g) for g in raw]
    else:
        groups = []
    return group in groups


def provision_user(db: Session, claims: dict) -> User:
    """Find, link or create the local user this token identifies."""
    logger = get_logger()
    issuer = str(claims.get("iss") or _issuer())
    subject = str(claims["sub"])
    email = (claims.get(settings.OIDC_EMAIL_CLAIM) or claims.get("email") or "").strip() or None

    user = db.query(User).filter(User.oidc_subject == subject).first()
    if user is not None and user.oidc_issuer and user.oidc_issuer != issuer:
        # The subject namespace belongs to the issuer; the same string from a
        # different provider is a different person.
        raise OidcError("This account is linked to a different identity provider.")

    if user is None:
        username = _derive_username(claims)
        # Link an existing local account, by email first because it is the
        # claim least likely to collide, then by username.
        candidates = []
        if email:
            candidates.append(db.query(User).filter(User.username == email).first())
        candidates.append(db.query(User).filter(User.username == username).first())
        existing = next((c for c in candidates if c is not None), None)

        if existing is not None:
            if existing.oidc_subject and existing.oidc_subject != subject:
                raise OidcError(
                    f"The local account {existing.username!r} is already linked to a "
                    "different SSO identity."
                )
            user = existing
            logger.info("[oidc] Linked existing user %r to subject %s", user.username, subject)
        elif settings.OIDC_AUTO_CREATE_USERS:
            # First user in an empty install becomes admin, mirroring _seed_admin.
            first_user = db.query(User.id).first() is None
            user = User(
                username=username,
                hashed_password=_unusable_password(),
                is_active=True,
                is_admin=first_user,
            )
            db.add(user)
            logger.info("[oidc] Created user %r from subject %s", username, subject)
        else:
            raise OidcError(
                "No local account matches this SSO user, and automatic account "
                "creation is disabled."
            )

    user.oidc_subject = subject
    user.oidc_issuer = issuer

    admin_by_group = _is_admin_by_group(claims)
    if admin_by_group is not None:
        user.is_admin = admin_by_group

    db.commit()
    db.refresh(user)

    if not user.is_active:
        raise OidcError("This account is disabled.")
    return user


def complete_login(db: Session, code: str, state: str) -> tuple[User, Optional[str]]:
    """Validate the callback and return the signed-in user and where to send them."""
    row = _consume_state(db, state)
    tokens = _exchange_code(code, row.redirect_uri, row.code_verifier)
    claims = _validate_id_token(
        tokens["id_token"], tokens.get("access_token"), row.nonce
    )
    return provision_user(db, claims), row.next_path
