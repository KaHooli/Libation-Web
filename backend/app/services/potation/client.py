"""Turning a stored account row into a usable Audible client.

`audible.Authenticator` carries the whole device registration — adp_token, the
device private key, the access and refresh tokens. `to_dict()` / `from_dict()`
are the serialisation hooks, so the blob is encrypted on the way out and
decrypted on the way back in; nothing readable is ever written to the database.

Access tokens expire. The authenticator refreshes them itself, so after any call
that might have refreshed, the row is re-saved — otherwise every request pays
for a refresh it has already done once.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy.orm import Session

from ...models.potation import AudibleAccount
from . import creds
from .marketplaces import normalize


class AccountUnavailable(Exception):
    """The account cannot be used until someone signs in again."""


def _authenticator_class():
    # Imported lazily so the module can be imported (and the rest of the app can
    # start) even where the optional dependency is absent.
    from audible import Authenticator

    return Authenticator


def save_authenticator(
    db: Session, account: AudibleAccount, authenticator: Any, *, commit: bool = True
) -> None:
    """Encrypt the current registration state onto the account row."""
    account.auth_blob = creds.encrypt_json(authenticator.to_dict())
    account.needs_reauth = False
    if commit:
        db.commit()


def load_authenticator(db: Session, account: AudibleAccount) -> Any:
    """Rehydrate the stored registration, or explain why it cannot be used."""
    if not account.auth_blob:
        raise AccountUnavailable(
            f"Audible account {account.account_id} has no stored credentials. "
            "Sign in to it again."
        )

    data = creds.try_decrypt_json(account.auth_blob)
    if data is None:
        # A lost or replaced key file. Flag the account rather than raising past
        # the caller — one unreadable account must not take the app down.
        account.needs_reauth = True
        db.commit()
        raise AccountUnavailable(
            f"The stored credentials for Audible account {account.account_id} "
            f"could not be decrypted (see {creds.key_path()}). Sign in again."
        )

    try:
        return _authenticator_class().from_dict(data)
    except Exception as exc:  # the library raises a variety of types
        account.needs_reauth = True
        db.commit()
        raise AccountUnavailable(
            f"The stored credentials for Audible account {account.account_id} "
            f"are not usable: {exc}"
        ) from exc


@contextmanager
def client_for(db: Session, account: AudibleAccount) -> Iterator[Any]:
    """A synchronous Audible client for one account.

    Re-saves the registration on the way out, because the authenticator may have
    silently refreshed the access token during the block.
    """
    from audible import Client

    authenticator = load_authenticator(db, account)
    before = authenticator.access_token

    client = Client(auth=authenticator, country_code=normalize(account.locale))
    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:  # closing must never mask the real error
            pass
        if authenticator.access_token != before:
            save_authenticator(db, account, authenticator)


def get_account(db: Session, account_id: str) -> AudibleAccount:
    account = (
        db.query(AudibleAccount)
        .filter(AudibleAccount.account_id == account_id)
        .first()
    )
    if account is None:
        raise AccountUnavailable(f"No Audible account {account_id!r} is connected.")
    return account


def active_accounts(db: Session) -> list[AudibleAccount]:
    """Accounts that could actually be used right now."""
    return (
        db.query(AudibleAccount)
        .filter(
            AudibleAccount.is_active.is_(True),
            AudibleAccount.needs_reauth.is_(False),
            AudibleAccount.auth_blob.isnot(None),
        )
        .order_by(AudibleAccount.account_id)
        .all()
    )


def mark_synced(db: Session, account: AudibleAccount) -> None:
    account.last_sync_at = datetime.now(timezone.utc)
    db.commit()


def unlinked_account_ids(db: Session) -> set[str]:
    """Account ids other tables still point at that no longer exist.

    Re-authorising mints fresh account rows, and `users.audible_account_id`,
    `audible_account_settings.account_id` and the `account_id` query parameter on
    the library endpoints all reference the old value. Left undetected those
    references simply return no rows — a silent failure — so the accounts UI
    surfaces them instead.
    """
    from sqlalchemy import text

    known = {a.account_id for a in db.query(AudibleAccount.account_id).all()}
    referenced: set[str] = set()

    conn = db.connection()
    for statement in (
        "SELECT DISTINCT audible_account_id FROM users WHERE audible_account_id IS NOT NULL",
        "SELECT DISTINCT account_id FROM audible_account_settings",
    ):
        try:
            referenced.update(r[0] for r in conn.execute(text(statement)).fetchall() if r[0])
        except Exception:
            # The legacy table may already be gone; that is not an error here.
            continue

    return referenced - known
