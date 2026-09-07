"""Audible content licenses, and the DRM census that gates the native engine.

A license request is what turns "I own this" into "here is where to download it
and how to decrypt it". The response also names the DRM scheme Audible will
serve the title under, which is the number the whole native-engine plan hinges
on: a pure-Python pipeline can decrypt Adrm (AAX/AAXC, via ffmpeg since 4.4) and
pass through unencrypted delivery, but it has no CDM, so anything served under
Widevine, PlayReady or FairPlay is out of reach.

This module only *requests* licenses and records what came back. Decrypting the
voucher to recover the AAXC key and iv belongs to the download pipeline; the
census does not need it, and leaving it out keeps this honest about what has
actually been proven to work.

**Quota.** A `Download` license counts against Audible's daily download
allowance. The census therefore samples rather than sweeping a whole library by
default, and every license it fetches is persisted — so a probe is not
necessarily wasted work, since the download pipeline reuses a stored license
rather than asking again.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy.orm import Session

from ...models.potation import AudibleAccount, AudibleLicense, Book
from ..logger import get_logger
from . import creds

#: What a Python pipeline can actually handle end to end.
#: Adrm covers both AAX (4-byte activation-bytes key) and AAXC (16+16 key/iv);
#: Mpeg is Audible's unencrypted mp3 delivery.
NATIVE_CAPABLE_DRM = frozenset({"Adrm", "Mpeg"})

#: Schemes that need a content decryption module we do not have.
CDM_REQUIRED_DRM = frozenset({"Widevine", "PlayReady", "FairPlay"})

#: Advertised to Audible. Deliberately the full set for a census, so the answer
#: reflects what Audible *would* serve rather than what it falls back to when we
#: claim to support little.
ALL_DRM_TYPES = [
    "Mpeg", "PlayReady", "Hls", "Dash", "Adrm", "FairPlay", "Widevine", "HlsCmaf",
]

#: What the download pipeline will actually ask for.
NATIVE_DRM_TYPES = ["Adrm", "Mpeg"]

CODECS = ["mp4a.40.2", "mp4a.40.42", "ec+3", "ac-4"]

LICENSE_RESPONSE_GROUPS = "content_reference,chapter_info,pdf_url,last_position_heard"


class LicenseError(Exception):
    """Audible would not issue a license for this title."""


@dataclass
class LicenseInfo:
    asin: str
    drm_type: Optional[str]
    content_format: Optional[str] = None
    acr: Optional[str] = None
    version: Optional[str] = None
    download_url: Optional[str] = None
    url_expires_at: Optional[datetime] = None
    refresh_date: Optional[datetime] = None
    #: Whether the response carried an encrypted voucher at all.
    has_voucher: bool = False
    raw_status: Optional[str] = None
    #: Recovered from the voucher, only when `decrypt_voucher=True` was asked
    #: for. **Secrets** — never log them, never put them in an error message.
    key: Optional[str] = None
    iv: Optional[str] = None

    @property
    def natively_downloadable(self) -> bool:
        return (self.drm_type or "") in NATIVE_CAPABLE_DRM


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _url_expiry(url: Optional[str]) -> Optional[datetime]:
    """CDN links carry their own expiry; reusing one past it just 403s."""
    if not url:
        return None
    expires = parse_qs(urlparse(url).query).get("Expires", [None])[0]
    try:
        return datetime.fromtimestamp(int(expires), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_license(asin: str, payload: dict) -> LicenseInfo:
    content_license = payload.get("content_license") or {}
    metadata = content_license.get("content_metadata") or {}
    reference = metadata.get("content_reference") or {}
    content_url = metadata.get("content_url") or {}

    download_url = content_url.get("offline_url") or None
    return LicenseInfo(
        asin=asin,
        drm_type=content_license.get("drm_type") or None,
        content_format=reference.get("content_format") or None,
        acr=reference.get("acr") or None,
        version=reference.get("version") or None,
        download_url=download_url,
        url_expires_at=_url_expiry(download_url),
        refresh_date=_parse_iso(content_license.get("refresh_date")),
        has_voucher=bool(content_license.get("license_response")),
        raw_status=content_license.get("status_code") or None,
    )


def request_license(
    db: Session,
    account: AudibleAccount,
    asin: str,
    *,
    drm_types: Optional[list[str]] = None,
    consumption_type: str = "Download",
    quality: str = "High",
    persist: bool = True,
    decrypt_voucher: bool = False,
) -> LicenseInfo:
    """Ask Audible for a license, and record what it said.

    `decrypt_voucher` is off by default so the census stays a measurement: it
    only needs to know *which* scheme Audible chose, and decrypting adds failure
    surface to a path whose whole value is that it does not overclaim. The
    download pipeline turns it on, because the key and iv are what it is for.
    """
    from .client import client_for

    body = {
        "supported_media_features": {
            "codecs": CODECS,
            "drm_types": list(drm_types if drm_types is not None else ALL_DRM_TYPES),
        },
        "quality": quality,
        "consumption_type": consumption_type,
        "response_groups": LICENSE_RESPONSE_GROUPS,
        "spatial": False,
    }

    with client_for(db, account) as client:
        try:
            payload = client.post(f"content/{asin}/licenserequest", body=body)
        except Exception as exc:
            raise LicenseError(f"{asin}: {exc}") from exc

        info = parse_license(asin, payload)
        if decrypt_voucher and info.has_voucher:
            # Inside the client block: the voucher is keyed to this device
            # registration, so it needs the same authenticator that made the
            # request. A re-registration makes an old voucher undecryptable,
            # which is why the recovered key is persisted rather than re-derived.
            key, iv = _decrypt_voucher(client.auth, payload, asin)
            info = replace(info, key=key, iv=iv)

    if persist:
        _persist(db, info)
    return info


def _decrypt_voucher(authenticator, payload: dict, asin: str) -> tuple[Optional[str], Optional[str]]:
    """Recover the AAXC key and iv, or report failure without quoting them."""
    from audible.aescipher import decrypt_voucher_from_licenserequest

    try:
        voucher = decrypt_voucher_from_licenserequest(authenticator, payload)
    except Exception as exc:
        # Deliberately does not include the exception's own payload: a failure
        # part-way through can carry plaintext fragments of the voucher.
        raise LicenseError(
            f"{asin}: the licence voucher could not be decrypted "
            f"({type(exc).__name__})."
        ) from None

    if not isinstance(voucher, dict):
        raise LicenseError(f"{asin}: the licence voucher was not in the expected shape.")
    return voucher.get("key"), voucher.get("iv")


def _persist(db: Session, info: LicenseInfo) -> None:
    """Store the license so a retry does not have to buy another one."""
    row = (
        db.query(AudibleLicense)
        .filter(AudibleLicense.book_asin == info.asin)
        .order_by(AudibleLicense.id.desc())
        .first()
    )
    if row is None:
        row = AudibleLicense(book_asin=info.asin)
        db.add(row)

    row.drm_type = info.drm_type
    row.download_url = info.download_url
    row.url_expires_at = info.url_expires_at
    row.refresh_date = info.refresh_date
    row.acr = info.acr
    row.version = info.version
    # Encrypted at rest under the same key as Audible credentials. Only written
    # when this request actually recovered them — a later census probe of the
    # same title must not blank out key material a download already stored.
    if info.key:
        row.key_ciphertext = creds.encrypt(info.key)
    if info.iv:
        row.iv_ciphertext = creds.encrypt(info.iv)
    row.fetched_at = datetime.now(timezone.utc)
    db.commit()


# ── Reuse ─────────────────────────────────────────────────────────────────────

#: Don't hand a CDN link to a downloader that is about to spend minutes on it
#: when the link expires in seconds. Re-licensing is cheaper than a failed
#: download that has to be retried anyway.
URL_EXPIRY_MARGIN = timedelta(minutes=5)


def stored_license(db: Session, asin: str, *, now: Optional[datetime] = None) -> Optional[LicenseInfo]:
    """The persisted licence for a title, if it is still usable.

    A `Download` licence counts against Audible's daily allowance, so a retry
    that re-licenses spends quota to learn something already known. Reuse is the
    difference between a retry being free and a retry being rationed.

    Returns None when there is no row, when the CDN link has expired (or is
    about to), or when the key material cannot be decrypted — a lost
    `potation.key` reads as "no licence", which costs one request rather than
    producing a file that will not play.
    """
    row = (
        db.query(AudibleLicense)
        .filter(AudibleLicense.book_asin == asin)
        .order_by(AudibleLicense.id.desc())
        .first()
    )
    if row is None or not row.download_url:
        return None

    moment = now or datetime.now(timezone.utc)
    expires = row.url_expires_at
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires - URL_EXPIRY_MARGIN <= moment:
            return None

    key = creds.try_decrypt(row.key_ciphertext) if row.key_ciphertext else None
    iv = creds.try_decrypt(row.iv_ciphertext) if row.iv_ciphertext else None
    if (row.key_ciphertext and key is None) or (row.iv_ciphertext and iv is None):
        return None

    return LicenseInfo(
        asin=asin,
        drm_type=row.drm_type,
        acr=row.acr,
        version=row.version,
        download_url=row.download_url,
        url_expires_at=row.url_expires_at,
        refresh_date=row.refresh_date,
        has_voucher=bool(row.key_ciphertext),
        key=key,
        iv=iv,
    )


def license_for_download(
    db: Session,
    account: AudibleAccount,
    asin: str,
    *,
    force_refresh: bool = False,
) -> LicenseInfo:
    """The licence a download should use: the stored one when it still serves."""
    if not force_refresh:
        existing = stored_license(db, asin)
        if existing is not None:
            return existing
    return request_license(
        db, account, asin,
        drm_types=NATIVE_DRM_TYPES,
        consumption_type="Download",
        persist=True,
        decrypt_voucher=True,
    )


# ── The census ────────────────────────────────────────────────────────────────

#: Audible's refusal when handed the ASIN of one part of a multi-part title.
#: Worth recognising by sight: it means the *sample* was wrong, not that the
#: title is unavailable, and 20-odd of these will otherwise read as a library
#: that cannot be licensed at all.
PART_ASIN_REFUSED = "Audio Part asins are no longer supported"


@dataclass
class DrmCensus:
    """What a native engine could and could not fetch."""

    sampled: int = 0
    counts: Counter = field(default_factory=Counter)
    failures: list[tuple[str, str]] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    #: Titles Audible served under a CDM scheme when offered every option, but
    #: served natively once we asked as the download pipeline actually will.
    #: These are *not* blocked — counting them as blocked overstates the problem.
    fell_back_to_native: list[str] = field(default_factory=list)

    @property
    def part_asin_failures(self) -> list[tuple[str, str]]:
        return [(a, m) for a, m in self.failures if PART_ASIN_REFUSED in m]

    @property
    def answered(self) -> int:
        return self.sampled - len(self.failures)

    @property
    def native_capable(self) -> int:
        return sum(n for drm, n in self.counts.items() if drm in NATIVE_CAPABLE_DRM)

    @property
    def cdm_required(self) -> int:
        return sum(n for drm, n in self.counts.items() if drm in CDM_REQUIRED_DRM)

    @property
    def other(self) -> int:
        return self.sampled - self.native_capable - self.cdm_required - len(self.failures)

    @property
    def blocked_fraction(self) -> float:
        """Share of the sample a pure-Python engine could not fetch."""
        if self.answered <= 0:
            return 0.0
        return (self.answered - self.native_capable) / self.answered

    def verdict(self) -> str:
        if self.answered == 0:
            parts = len(self.part_asin_failures)
            if parts:
                return (
                    f"No titles could be checked: {parts} of {self.sampled} were the "
                    "ASINs of individual parts, which Audible no longer licenses. "
                    "The sample is wrong, not the library — re-run after updating."
                )
            return "No titles could be checked — the sample produced no answers."

        # A sample too small to divide is a sample, not a measurement.
        if self.answered < 5:
            return (
                f"Only {self.answered} of {self.sampled} titles answered — too few to "
                "put a percentage on. Fix whatever caused the failures and re-run "
                "before reading anything into this."
            )

        pct = self.blocked_fraction * 100
        if pct == 0:
            return (
                "Every title sampled is natively downloadable. Nothing in this "
                "sample needs LibationCli."
            )
        if pct < 5:
            return (
                f"{pct:.1f}% of the sample needs DRM a Python engine cannot handle. "
                "Small enough to treat as an exception rather than a blocker."
            )
        return (
            f"{pct:.1f}% of the sample needs DRM a Python engine cannot handle. "
            "That is enough to change the plan — keep LibationCli as a fallback "
            "for those titles rather than deleting it."
        )


def _reprobe_as_native(
    db: Session,
    account: AudibleAccount,
    asin: str,
    consumption_type: str,
    census: DrmCensus,
    logger: Any,
) -> Optional[LicenseInfo]:
    """Re-ask claiming only the DRM a native engine can actually handle.

    Returns the second answer when it is natively downloadable, `None` when the
    title is genuinely out of reach. Costs one extra license request, and only
    for titles that looked blocked on the first ask.
    """
    try:
        second = request_license(
            db, account, asin, drm_types=NATIVE_DRM_TYPES,
            consumption_type=consumption_type, persist=True,
        )
    except LicenseError as exc:
        # A refusal here is the meaningful answer: offered only what we can
        # decrypt, Audible has nothing to give us.
        logger.info("[potation-census] %s: no native license available (%s)", asin, exc)
        return None

    if second.natively_downloadable:
        census.fell_back_to_native.append(asin)
        return second
    return None


def run_census(
    db: Session,
    account: AudibleAccount,
    *,
    sample_size: Optional[int] = 25,
    consumption_type: str = "Download",
    asins: Optional[list[str]] = None,
    progress: Optional[Any] = None,
) -> DrmCensus:
    """Probe DRM across a sample of the library.

    `sample_size=None` sweeps everything, which on a large library is a lot of
    license requests against a daily-capped API — deliberately not the default.
    """
    logger = get_logger()
    census = DrmCensus()

    if asins is None:
        query = (
            db.query(Book.asin)
            .filter(
                Book.account_id == account.account_id,
                # One probe per *owned title*: standalone books and multi-part
                # parents, never the individual parts. Audible refuses a part
                # ASIN outright ("Audio Part asins are no longer supported"), so
                # sampling parts measures nothing at all — which is exactly what
                # the first real run of this did.
                Book.parent_asin.is_(None),
            )
            .order_by(Book.purchase_date.desc().nullslast(), Book.asin)
        )
        if sample_size:
            query = query.limit(sample_size)
        asins = [row[0] for row in query.all()]

    for index, asin in enumerate(asins, start=1):
        census.sampled += 1
        try:
            info = request_license(
                db, account, asin, consumption_type=consumption_type, persist=True
            )
        except LicenseError as exc:
            census.failures.append((asin, str(exc)))
            logger.warning("[potation-census] %s", exc)
        else:
            # The first ask offers every scheme, so Audible answers with what it
            # would *prefer* to serve. That is not the same question as "could a
            # native engine get this title": Audible may well fall back to AAXC
            # for a client that never claims Widevine support. Asking again the
            # way the download pipeline actually will is what separates "we
            # cannot fetch this" from "we did not ask properly".
            if not info.natively_downloadable:
                info = _reprobe_as_native(
                    db, account, asin, consumption_type, census, logger
                ) or info
            census.counts[info.drm_type or "unknown"] += 1
            if not info.natively_downloadable:
                census.unreachable.append(asin)
        if progress is not None:
            progress(index, len(asins), census)

    logger.info(
        "[potation-census] %s: sampled %d, native %d, cdm %d, failures %d",
        account.account_id, census.sampled, census.native_capable,
        census.cdm_required, len(census.failures),
    )
    return census
