"""Which delivery path a licensed title takes, and whether we can walk it.

Libation's `DownloadOptions.cs` branches four ways, and a pipeline that assumes
one of them breaks on the others in different and unhelpful directions:
always passing `-audible_key` hard-fails an unencrypted title, and passing a
4-byte activation blob where a 16-byte key belongs produces *noise* rather than
an error — a file that looks like an audiobook and is not one.

So the branch is decided here, once, from the DRM type and the shape of the key
material, and nothing downstream guesses:

``aaxc``          `Adrm` with a 16-byte key and a 16-byte iv, from the voucher.
                  What a modern device registration is served.
``aax``           `Adrm` with 4-byte activation bytes. The legacy format.
``unencrypted``   `Mpeg`, or delivery with no DRM at all. Copy, do not decrypt.
``unsupported``   Everything else, with a reason to show a person.

**One deliberate divergence from Libation.** It treats *anything* that is not
Widevine or Adrm as unencrypted mp3 delivery. We refuse an unrecognised DRM type
instead. If Audible introduces a scheme we do not know, Libation's behaviour is
to hand ffmpeg an encrypted file with no key and write whatever comes out;
having a classified `drm_unsupported` error and a book that is honestly marked
un-downloaded is better than a corrupt m4b that reaches Chaptarr, then
Audiobookshelf, and is only noticed by whoever tries to listen to it.

This module is pure: no network, no database, no subprocess. That is what lets
the branch itself be tested exhaustively, which matters more than usual because
three of its four arms cannot be exercised against a real account on demand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .license import CDM_REQUIRED_DRM

KIND_AAXC = "aaxc"
KIND_AAX = "aax"
KIND_UNENCRYPTED = "unencrypted"
KIND_UNSUPPORTED = "unsupported"

#: DRM types that mean "no decryption needed". `Mpeg` is Audible's unencrypted
#: mp3 delivery; an absent type is delivery with no DRM at all.
UNENCRYPTED_DRM = frozenset({"Mpeg", "", "none", "None"})

#: AAXC: AES-128, so both halves are exactly 16 bytes.
AAXC_KEY_BYTES = 16
#: AAX: the activation blob ffmpeg wants for `-activation_bytes`.
AAX_ACTIVATION_BYTES = 4

_HEX = re.compile(r"\A[0-9a-fA-F]+\Z")


@dataclass(frozen=True)
class DeliveryPlan:
    """How to fetch and decrypt one title — or why we cannot."""

    kind: str
    drm_type: Optional[str] = None
    #: Hex, lower-cased. Secrets: never log these, never put them in an error.
    key: Optional[str] = None
    iv: Optional[str] = None
    activation_bytes: Optional[str] = None
    #: Why an `unsupported` plan is unsupported. Safe to show a person.
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.kind != KIND_UNSUPPORTED

    @property
    def needs_decryption(self) -> bool:
        return self.kind in (KIND_AAXC, KIND_AAX)


def _hex_bytes(value: Optional[str]) -> Optional[int]:
    """Length in bytes of a hex string, or None if it is not clean hex.

    Rejects an odd length outright: half a byte is not a key, and letting it
    through would produce a plausible-looking ffmpeg argument.
    """
    if not value:
        return None
    text = value.strip()
    if not text or len(text) % 2 or not _HEX.match(text):
        return None
    return len(text) // 2


def plan_delivery(
    drm_type: Optional[str],
    *,
    key: Optional[str] = None,
    iv: Optional[str] = None,
    activation_bytes: Optional[str] = None,
) -> DeliveryPlan:
    """Decide the delivery path from the licence and its key material."""
    drm = (drm_type or "").strip()

    if drm in CDM_REQUIRED_DRM:
        return DeliveryPlan(
            kind=KIND_UNSUPPORTED,
            drm_type=drm,
            reason=(
                f"Audible served this under {drm}, which needs a content "
                "decryption module this engine does not have."
            ),
        )

    if drm == "Adrm":
        key_len, iv_len = _hex_bytes(key), _hex_bytes(iv)
        if key_len == AAXC_KEY_BYTES and iv_len == AAXC_KEY_BYTES:
            return DeliveryPlan(
                kind=KIND_AAXC, drm_type=drm,
                key=key.strip().lower(), iv=iv.strip().lower(),
            )

        act_len = _hex_bytes(activation_bytes)
        if act_len == AAX_ACTIVATION_BYTES:
            return DeliveryPlan(
                kind=KIND_AAX, drm_type=drm,
                activation_bytes=activation_bytes.strip().lower(),
            )

        # Adrm with key material we cannot use. Refusing is the whole point:
        # handing ffmpeg a wrong-length key writes a file rather than failing.
        return DeliveryPlan(
            kind=KIND_UNSUPPORTED, drm_type=drm,
            reason=_describe_bad_key(key_len, iv_len, act_len),
        )

    if drm in UNENCRYPTED_DRM:
        return DeliveryPlan(kind=KIND_UNENCRYPTED, drm_type=drm or None)

    return DeliveryPlan(
        kind=KIND_UNSUPPORTED,
        drm_type=drm,
        reason=(
            f"Unrecognised DRM type {drm!r}. Refusing rather than guessing it is "
            "unencrypted, which would write a corrupt file instead of failing."
        ),
    )


def _describe_bad_key(
    key_len: Optional[int], iv_len: Optional[int], act_len: Optional[int]
) -> str:
    """Say what was wrong with the key material without quoting any of it."""
    if key_len is None and iv_len is None and act_len is None:
        return (
            "Audible served this under Adrm but the licence carried no usable "
            "key material. The voucher may not have been decrypted."
        )
    parts = []
    if key_len is not None or iv_len is not None:
        parts.append(
            f"expected a {AAXC_KEY_BYTES}-byte key and iv, got "
            f"{key_len if key_len is not None else 'unreadable'}"
            f"/{iv_len if iv_len is not None else 'unreadable'}"
        )
    if act_len is not None:
        parts.append(f"expected {AAX_ACTIVATION_BYTES} activation bytes, got {act_len}")
    return "Adrm key material is the wrong shape: " + "; ".join(parts) + "."


def ffmpeg_decrypt_args(plan: DeliveryPlan) -> list[str]:
    """Input flags that let ffmpeg read the downloaded file.

    These go *before* `-i`, because they describe how to decrypt the input.
    An unencrypted plan contributes nothing, which is the point of the branch:
    passing `-audible_key` for a file that has none is an error, not a no-op.
    """
    if plan.kind == KIND_AAXC:
        return ["-audible_key", plan.key, "-audible_iv", plan.iv]
    if plan.kind == KIND_AAX:
        return ["-activation_bytes", plan.activation_bytes]
    if plan.kind == KIND_UNENCRYPTED:
        return []
    raise ValueError(f"No ffmpeg arguments for an {plan.kind} delivery plan")
