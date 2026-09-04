"""Audible marketplace names.

The frontend sends the marketplace names LibationCli accepted (`AccountsPage.tsx`
posts `"germany"`, `"us"`, and so on — they are `AudibleApi.Locale.Name` values,
not ISO country codes). The `audible` library keys its locales by country code.

Translating here keeps the existing frontend contract intact and confines the
mismatch to one table. Getting it wrong is quiet rather than loud: an
unrecognised marketplace still produces a plausible-looking sign-in URL and only
fails later at device registration, which is exactly the failure mode the old
`cli.VALID_LOCALES` guard existed to catch.
"""

from __future__ import annotations

from typing import Optional

#: Libation marketplace name -> `audible` country code.
LIBATION_TO_COUNTRY_CODE: dict[str, str] = {
    "us": "us",
    "uk": "uk",
    "germany": "de",
    "france": "fr",
    "canada": "ca",
    "australia": "au",
    "japan": "jp",
    "italy": "it",
    "spain": "es",
    "india": "in",
    "brazil": "br",
}

#: Every name the API will accept, in the form the frontend already sends.
VALID_MARKETPLACES = frozenset(LIBATION_TO_COUNTRY_CODE)

#: Human-readable labels, for error messages and the accounts UI.
MARKETPLACE_LABELS: dict[str, str] = {
    "us": "United States",
    "uk": "United Kingdom",
    "germany": "Germany",
    "france": "France",
    "canada": "Canada",
    "australia": "Australia",
    "japan": "Japan",
    "italy": "Italy",
    "spain": "Spain",
    "india": "India",
    "brazil": "Brazil",
}


class UnknownMarketplace(ValueError):
    """The supplied marketplace is not one Audible serves."""


def normalize(marketplace: Optional[str]) -> str:
    """Return the `audible` country code for a marketplace name.

    Accepts either the Libation name (`"germany"`) or the country code itself
    (`"de"`), so a caller that already speaks the new vocabulary is not forced
    through the old one.
    """
    key = (marketplace or "").strip().lower()
    if not key:
        raise UnknownMarketplace(
            "No Audible marketplace was supplied. Expected one of: "
            + ", ".join(sorted(VALID_MARKETPLACES))
        )

    if key in LIBATION_TO_COUNTRY_CODE:
        return LIBATION_TO_COUNTRY_CODE[key]
    if key in set(LIBATION_TO_COUNTRY_CODE.values()):
        return key

    raise UnknownMarketplace(
        f"Unknown Audible marketplace {marketplace!r}. Expected one of: "
        + ", ".join(sorted(VALID_MARKETPLACES))
    )


def label(marketplace: str) -> str:
    key = (marketplace or "").strip().lower()
    if key in MARKETPLACE_LABELS:
        return MARKETPLACE_LABELS[key]
    for name, code in LIBATION_TO_COUNTRY_CODE.items():
        if code == key:
            return MARKETPLACE_LABELS[name]
    return marketplace
