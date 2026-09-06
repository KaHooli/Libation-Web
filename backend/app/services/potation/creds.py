"""Encryption at rest for stored Audible credentials.

A successful Audible device registration yields a long-lived refresh token, an
`adp_token` and the device private key. That blob is never stored in the clear.

The key lives in a file on the `/config` volume rather than being derived from
`SECRET_KEY`, so rotating the JWT signing secret does not silently invalidate
every connected Audible account. This is not weaker than what it replaces:
Libation keeps the same tokens in plaintext in `/config/AccountsSettings.json`
today, so the key file swaps plaintext for ciphertext plus an 0600 key.

A decrypt failure is reported, never swallowed — the caller marks the account
`needs_reauth` so it fails loudly instead of looking mysteriously logged out.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from ...config import settings

KEY_FILENAME = "potation.key"


class CredentialDecryptError(Exception):
    """Stored credentials could not be decrypted with the current key."""


def key_path() -> Path:
    """Location of the Fernet key. On the /config volume, so it survives restarts."""
    return Path(settings.LIBATION_CONFIG) / KEY_FILENAME


def load_or_create_key() -> bytes:
    """Return the Fernet key, generating and persisting one on first use."""
    path = key_path()
    try:
        existing = path.read_bytes().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Created 0600 by os.open rather than chmod'd afterwards, so the key is
    # never briefly readable by anyone else.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _fernet() -> Fernet:
    try:
        return Fernet(load_or_create_key())
    except (ValueError, TypeError) as exc:
        raise CredentialDecryptError(
            f"The credential key at {key_path()} is not a valid Fernet key. "
            "Delete it to have a new one generated; connected Audible accounts "
            "will need to be re-authorised."
        ) from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise CredentialDecryptError(
            f"Stored Audible credentials could not be decrypted. The key at "
            f"{key_path()} is missing or has been replaced, so the account must "
            "be re-authorised."
        ) from exc


def encrypt_json(data: Any) -> str:
    return encrypt(json.dumps(data, separators=(",", ":")))


def decrypt_json(token: str) -> Any:
    return json.loads(decrypt(token))


def try_decrypt_json(token: Optional[str]) -> Optional[Any]:
    """Decrypt, or return None when the blob is absent or unreadable.

    For callers that want to flag an account `needs_reauth` rather than raise —
    a lost key must never take the whole app down at startup.
    """
    if not token:
        return None
    try:
        return decrypt_json(token)
    except CredentialDecryptError:
        return None
