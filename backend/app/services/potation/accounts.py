"""Audible accounts, as the API already describes them.

`api/accounts.py` has one response shape and one frontend built against it. This
module answers that shape from `audible_accounts` instead of from LibationCli's
`list-accounts --bare`, so the cut-over is a branch at the top of each endpoint
rather than a rewrite of the page.

**Every account is listed, not only the usable ones.** `client.active_accounts()`
filters out anything that needs re-authorising, which is right for "who can I
sync?" and exactly wrong here: an account that has fallen out of authorisation is
the one the user most needs to see. It comes back with `authenticated: false` so
the page can offer a reconnect.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...models.potation import AudibleAccount
from ...models.user import User


def _display_name(account: AudibleAccount) -> str:
    """Never blank. A nameless row rendering as an empty cell is unusable, and
    the id is at least something to point at when asking which one to remove."""
    return account.account_name or account.email or account.account_id


def as_response(account: AudibleAccount, owner: Optional[dict] = None) -> dict:
    """One account in the shape `AccountResponse` declares.

    Two fields have no direct counterpart and are mapped deliberately:

    - **`scan_library`** is LibationCli's per-account "include in scans" flag.
      Potation has no such switch; `is_active` is the same idea — an inactive
      account is dropped from `active_accounts()` and so is never synced.
    - **`authenticated`** is the inverse of `needs_reauth`, which is set both
      when Audible rejects the stored blob and when the blob will not decrypt
      (a lost `potation.key`). Both mean the same thing to someone looking at
      the page: this account cannot be used until you sign in again.
    """
    owner = owner or {}
    return {
        "account_id": account.account_id,
        "name": _display_name(account),
        "locale": account.locale,
        "scan_library": bool(account.is_active),
        "authenticated": not account.needs_reauth,
        "owner_name": owner.get("owner_name"),
        "owner_username": owner.get("username"),
        "auto_download": bool(account.auto_download),
        "added_by_user_id": account.added_by_user_id,
    }


def list_accounts(db: Session) -> list[dict]:
    """Every connected account, enriched with its owner, ordered stably."""
    owners = {
        u.audible_account_id: {"owner_name": u.owner_name, "username": u.username}
        for u in db.query(User).filter(User.audible_account_id.isnot(None)).all()
    }
    rows = db.query(AudibleAccount).order_by(AudibleAccount.account_id).all()
    return [as_response(a, owners.get(a.account_id)) for a in rows]


def set_auto_download(db: Session, account_id: str, enabled: bool) -> None:
    """Flip the per-account auto-download flag.

    Under LibationCli this lived in `audible_account_settings`; here it is a
    column on the account itself, which is why the row cannot be created on the
    fly — an account has to exist before it can be configured.
    """
    account = db.query(AudibleAccount).filter(
        AudibleAccount.account_id == account_id
    ).first()
    if account is None:
        raise LookupError(account_id)
    account.auto_download = bool(enabled)
    db.commit()


def added_by(db: Session, account_id: str) -> Optional[int]:
    """Which web UI user connected this account, or None."""
    row = db.query(AudibleAccount.added_by_user_id).filter(
        AudibleAccount.account_id == account_id
    ).first()
    return row[0] if row else None


# ── The continuity hazard ────────────────────────────────────────────────────

def unlinked_references(db: Session) -> list[dict]:
    """Rows still pointing at an Audible account id that no longer exists.

    Re-authorising mints a fresh account row, and three things reference the old
    id: `users.audible_account_id` (which drives My Books), the legacy
    `audible_account_settings` table (which drove auto-download), and the
    `account_id` query parameter the library pages send. None of them error on a
    stale id — they return **zero rows**, so the failure is a page that looks
    empty rather than a page that says something is wrong.

    Surfacing them is what turns that into a visible "reconnect this" instead.
    Each entry names the stale id and who is affected, so the fix is one edit
    rather than a hunt.
    """
    from .client import unlinked_account_ids

    stale = unlinked_account_ids(db)
    if not stale:
        return []

    users_by_account: dict[str, list[str]] = {}
    for u in db.query(User).filter(User.audible_account_id.in_(stale)).all():
        users_by_account.setdefault(u.audible_account_id, []).append(u.username)

    # The legacy table may not carry a row for every stale id, and may itself be
    # gone once Phase D drops it — a missing table is not an error here.
    legacy: set[str] = set()
    try:
        legacy = {
            r[0] for r in db.connection().execute(
                text("SELECT account_id FROM audible_account_settings")
            ).fetchall()
            if r[0] in stale
        }
    except Exception:
        pass

    return [
        {
            "account_id": account_id,
            "users": sorted(users_by_account.get(account_id, [])),
            "had_settings": account_id in legacy,
        }
        for account_id in sorted(stale)
    ]
