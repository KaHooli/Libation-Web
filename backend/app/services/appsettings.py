"""Read and write Libation's `appsettings.json` — the download toggles.

Lives in a service rather than in `api/settings.py` because the settings backup
needs the same file, and two copies of the field map would drift the moment one
of them learned a new key.

The file is Libation's, not ours: it may hold keys we know nothing about, so
every write merges into whatever is already there rather than replacing it.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..config import settings as app_settings

APPSETTINGS_PATH = os.path.join(app_settings.LIBATION_CONFIG, "appsettings.json")

#: Our snake_case field name → the Libation key variants that mean it. The first
#: entry is what we write; the rest are accepted on read, because the casing has
#: moved around across Libation versions.
FIELD_MAP: dict[str, list[str]] = {
    "decrypt_to_lossy": ["DecryptToLossy"],
    "split_files_by_chapter": ["SplitFilesByChapter"],
    "download_episodes": ["DownloadEpisodes"],
    "create_cue_sheet": ["CreateCueSheet"],
    "save_cover_art_to_file": ["SaveCoverArtToFile"],
    "allow_audiobook_overwrite": ["AllowAudiobookOverwrite"],
    "strip_audible_brand_audio": ["StripAudibleBrandAudio"],
    "strip_unabridged": ["StripUnabridged"],
    "books_directory": ["Books"],
}


def read_raw() -> dict:
    """The file as it stands. A missing or unparseable file reads as empty."""
    if not os.path.exists(APPSETTINGS_PATH):
        return {}
    try:
        with open(APPSETTINGS_PATH, "r") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_raw(data: dict) -> None:
    os.makedirs(os.path.dirname(APPSETTINGS_PATH), exist_ok=True)
    with open(APPSETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def parse(raw: dict) -> dict[str, Any]:
    """Pull the fields we understand out of a raw appsettings document."""
    result: dict[str, Any] = {}
    for field, keys in FIELD_MAP.items():
        for key in keys:
            if key in raw:
                result[field] = raw[key]
                break
    return result


def apply(values: dict[str, Any]) -> dict[str, Any]:
    """Merge the supplied fields into the file, leaving unknown keys alone."""
    raw = read_raw()
    for field, keys in FIELD_MAP.items():
        value = values.get(field)
        if value is not None:
            raw[keys[0]] = value
    write_raw(raw)
    return parse(raw)
