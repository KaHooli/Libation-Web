"""Connecting an Audible account, without a subprocess.

The old flow started `libationcli login-external` and kept the process alive in a
module-level dict while the user signed in at Amazon, feeding the response URL
back into its PTY on a second HTTP request. That held a file descriptor and a
child process for up to ten minutes, died with the worker, and could not survive
a restart.

The same OAuth exchange is plain data:

  1. `begin_login` builds the Amazon sign-in URL and stores the PKCE verifier and
     device serial on a row.
  2. The user signs in and pastes back the URL Amazon redirected them to.
  3. `complete_login` pulls `authorization_code` out of it and registers a
     device, yielding the credentials that get encrypted onto an account row.

Nothing is held open in between.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy.orm import Session

from ...models.potation import AudibleAccount, AudibleLoginState
from . import creds
from .marketplaces import UnknownMarketplace, normalize  # noqa: F401 — re-exported
from ..logger import get_logger

#: How long a started sign-in may sit unfinished. Matches the old PTY timeout.
LOGIN_STATE_TTL = timedelta(minutes=10)


class AudibleAuthError(Exception):
    """Sign-in could not complete. The message is safe to show a user."""


def _locale(country_code: str) -> Any:
    from audible.localization import Locale

    return Locale(country_code)


def _prune_expired(db: Session) -> None:
    db.query(AudibleLoginState).filter(
        AudibleLoginState.expires_at < datetime.now(timezone.utc)
    ).delete(synchronize_session=False)


def begin_login(
    db: Session,
    marketplace: str,
    email: Optional[str] = None,
    started_by_user_id: Optional[int] = None,
) -> dict:
    """Return the Amazon sign-in URL and the handle to finish with."""
    country_code = normalize(marketplace)

    try:
        from audible.login import build_oauth_url
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise AudibleAuthError(
            "The `audible` library is not installed, so Audible sign-in is unavailable."
        ) from exc

    locale = _locale(country_code)
    code_verifier = _code_verifier()

    try:
        oauth_url, serial = build_oauth_url(
            country_code=locale.country_code,
            domain=locale.domain,
            market_place_id=locale.market_place_id,
            code_verifier=code_verifier,
            with_username=False,
        )
    except Exception as exc:
        raise AudibleAuthError(f"Could not build the Audible sign-in URL: {exc}") from exc

    _reject_malformed(oauth_url, marketplace)

    state = secrets.token_urlsafe(32)
    _prune_expired(db)
    db.add(AudibleLoginState(
        state=state,
        email=(email or "").strip() or None,
        marketplace=marketplace.strip().lower(),
        country_code=locale.country_code,
        # The verifier is not a credential on its own, but it is single-use
        # secret material for an in-flight exchange, so it is not stored bare.
        code_verifier=creds.encrypt(
            code_verifier.decode() if isinstance(code_verifier, bytes) else str(code_verifier)
        ),
        serial=serial,
        domain=locale.domain,
        started_by_user_id=started_by_user_id,
        expires_at=datetime.now(timezone.utc) + LOGIN_STATE_TTL,
    ))
    db.commit()

    get_logger().info(
        "[potation-auth] Sign-in URL generated for marketplace %s", marketplace
    )
    # The URL is returned but never logged — it carries OAuth parameters.
    return {"session_id": state, "login_url": oauth_url}


def _code_verifier() -> bytes:
    from audible.login import create_code_verifier

    return create_code_verifier()


def _reject_malformed(url: str, marketplace: str) -> None:
    """Never hand back a URL that cannot work.

    Two failures the old implementation learned about the hard way: an
    unrecognised marketplace renders an empty top-level domain
    (`https://www.amazon./ap/signin`), and a truncated read drops the OAuth
    parameters. Both look like ordinary links until the user tries them.
    """
    parsed = urlparse(url)
    host = parsed.netloc or ""
    if not host or host.rstrip(".").endswith("amazon") or host.endswith("."):
        raise AudibleAuthError(
            f"Audible marketplace {marketplace!r} produced an invalid sign-in domain."
        )

    query = parse_qs(parsed.query)
    missing = [
        p for p in ("openid.oa2.code_challenge", "openid.oa2.client_id")
        if p not in query
    ]
    if missing:
        raise AudibleAuthError(
            "The generated Audible sign-in URL is incomplete (missing "
            + ", ".join(missing) + ")."
        )


def _consume_state(db: Session, session_id: str) -> AudibleLoginState:
    row = (
        db.query(AudibleLoginState)
        .filter(AudibleLoginState.state == session_id)
        .first()
    )
    if row is None:
        raise AudibleAuthError("That sign-in session was not found. Start again.")
    if row.consumed_at is not None:
        raise AudibleAuthError("That sign-in session has already been used. Start again.")

    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise AudibleAuthError("That sign-in took too long. Start again.")

    # Stamped before the code is exchanged, so a double submit cannot register
    # the device twice — Amazon caps how many registrations an account may hold.
    row.consumed_at = datetime.now(timezone.utc)
    db.commit()
    return row


def extract_authorization_code(response_url: str) -> str:
    """Pull the authorization code out of the URL the user pasted back."""
    query = parse_qs(urlparse((response_url or "").strip()).query)
    code = query.get("openid.oa2.authorization_code", [None])[0]
    if not code:
        raise AudibleAuthError(
            "That URL does not contain an Audible authorization code. Copy the "
            "full address bar contents from the page Amazon redirected you to."
        )
    return code


def complete_login(db: Session, session_id: str, response_url: str) -> AudibleAccount:
    """Register a device from the pasted response URL and store the account."""
    row = _consume_state(db, session_id)
    code = extract_authorization_code(response_url)

    verifier = creds.decrypt(row.code_verifier).encode()

    try:
        from audible.register import register as register_device
    except ImportError as exc:  # pragma: no cover
        raise AudibleAuthError(
            "The `audible` library is not installed, so Audible sign-in is unavailable."
        ) from exc

    try:
        registration = register_device(
            authorization_code=code,
            code_verifier=verifier,
            domain=row.domain,
            serial=row.serial,
            with_username=False,
        )
    except Exception as exc:
        get_logger().error("[potation-auth] Device registration failed: %s", exc)
        raise AudibleAuthError(f"Audible rejected the sign-in: {exc}") from exc

    return _store_account(db, row, registration)


def _store_account(db: Session, row: AudibleLoginState, registration: dict) -> AudibleAccount:
    from audible import Authenticator

    # `locale` is a keyword argument, not a key in the registration data. Its
    # `to_dict()` then carries `locale_code`, so a later `from_dict` restores the
    # marketplace on its own.
    authenticator = Authenticator.from_dict(registration, locale=row.country_code)

    customer = registration.get("customer_info") or {}
    account_id = str(
        customer.get("user_id")
        or customer.get("account_pool")
        or row.serial
    )
    email = row.email or customer.get("email") or None
    name = customer.get("name") or email or account_id

    account = (
        db.query(AudibleAccount)
        .filter(AudibleAccount.account_id == account_id)
        .first()
    )
    if account is None:
        account = AudibleAccount(account_id=account_id)
        db.add(account)

    account.locale = row.marketplace
    account.marketplace_id = _market_place_id(row.country_code)
    account.account_name = name
    account.email = email
    account.is_active = True
    if account.added_by_user_id is None:
        account.added_by_user_id = row.started_by_user_id

    account.auth_blob = creds.encrypt_json(authenticator.to_dict())
    account.needs_reauth = False
    db.commit()
    db.refresh(account)

    get_logger().info("[potation-auth] Connected Audible account %s", account_id)
    return account


def _market_place_id(country_code: str) -> Optional[str]:
    try:
        return _locale(country_code).market_place_id
    except Exception:
        return None


def disconnect_account(db: Session, account_id: str) -> None:
    """Remove an account, releasing its device registration at Amazon.

    The old `DELETE /api/accounts/{id}` only edited `AccountsSettings.json`, so
    every removal left a registered device behind forever — and Amazon caps how
    many an account may hold. Deregistration is best-effort: a failure there must
    not prevent removing the account locally.
    """
    from .client import AccountUnavailable, get_account, load_authenticator

    account = get_account(db, account_id)

    try:
        authenticator = load_authenticator(db, account)
        authenticator.deregister_device()
        get_logger().info("[potation-auth] Deregistered device for account %s", account_id)
    except AccountUnavailable as exc:
        get_logger().warning(
            "[potation-auth] Removing account %s without deregistering: %s", account_id, exc
        )
    except Exception as exc:
        get_logger().warning(
            "[potation-auth] Deregistration failed for account %s: %s", account_id, exc
        )

    db.delete(account)
    db.commit()
