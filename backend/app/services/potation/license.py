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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy.orm import Session

from ...models.potation import AudibleAccount, AudibleLicense, Book
from ..logger import get_logger

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
    #: The encrypted voucher. Decrypting it is the download pipeline's job.
    has_voucher: bool = False
    raw_status: Optional[str] = None

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
) -> LicenseInfo:
    """Ask Audible for a license, and record what it said."""
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
    if persist:
        _persist(db, info)
    return info


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
    row.fetched_at = datetime.now(timezone.utc)
    db.commit()


# ── The census ────────────────────────────────────────────────────────────────

@dataclass
class DrmCensus:
    """What a native engine could and could not fetch."""

    sampled: int = 0
    counts: Counter = field(default_factory=Counter)
    failures: list[tuple[str, str]] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)

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
        answered = self.sampled - len(self.failures)
        if answered <= 0:
            return 0.0
        return (self.sampled - len(self.failures) - self.native_capable) / answered

    def verdict(self) -> str:
        if self.sampled - len(self.failures) == 0:
            return "No titles could be checked — the sample produced no answers."
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
                # A multi-part parent has no content of its own to license.
                Book.is_multipart_parent.is_(False),
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
