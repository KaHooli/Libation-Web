"""Export and restore the app's configuration as a single JSON document.

**What is in scope: settings, not data.** The Chaptarr connection, the OIDC
provider, and Libation's download toggles — the things that take a while to fill
in again and that you would want to carry to a rebuilt container. Users,
sessions, download history and the library itself are not settings and are not
here.

**Audible accounts are excluded on purpose, in both directions.** The stored
blob is a *device registration*, encrypted under `potation.key`, and Amazon caps
how many devices an account may have. Moving it between installs would either
be useless (a different key cannot decrypt it) or wrong (two installs sharing
one registration). Reconnecting an Audible account after a restore is a login,
not a data-loss event, so that is the trade taken.

**Secrets travel in the clear, and are opt-out.** A restore that leaves you
retyping the Chaptarr API key has not restored much, so the export carries
secrets by default. The OIDC client secret is stored Fernet-encrypted under a
key that deliberately does *not* leave with the backup, so it is decrypted on
the way out and re-encrypted under the destination's own key on the way in —
ciphertext in the file would restore to something unreadable. That plainly makes
the file sensitive, which is why `include_secrets=False` produces a shareable
copy instead and why the document says so about itself in `_warning`.

Restoring routes every section through the same `save_config` the Settings page
uses, so nothing here needs to know that OIDC secrets are encrypted or that
environment-pinned fields are read-only — those rules apply for free, and the
report says which fields the destination refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..config import APP_VERSION
from . import appsettings, chaptarr as chaptarr_svc, oidc_config

#: Identifies the document. A file without it is not one of ours, and saying so
#: beats half-applying whatever JSON someone happened to upload.
FORMAT = "libation-web-settings"

#: Bumped only for a change a *reader* must understand. A newer document is
#: refused rather than partially applied.
VERSION = 1

SECTIONS = ("chaptarr", "oidc", "libation")

WARNING_WITH_SECRETS = (
    "This file contains secrets in plain text (Chaptarr API key, OIDC client "
    "secret). Store it as you would a password."
)
WARNING_SANITISED = (
    "Secrets were excluded from this file. After restoring, re-enter anything "
    "listed in secrets_omitted."
)

#: Names as they appear in `secrets_omitted`, so the restore side can name them.
CHAPTARR_SECRET = "chaptarr.api_key"
OIDC_SECRET = "oidc.client_secret"

_CHAPTARR_FIELDS = (
    "enabled", "url", "import_mode", "auto_import",
    "path_from", "path_to", "skip_existing", "skip_when",
)

#: Every OidcConfig field except the secret, which is handled separately at both
#: ends: withheld from a sanitised export, re-encrypted on restore.
_OIDC_FIELDS = tuple(n for n in oidc_config.FIELD_NAMES if n != "client_secret")


@dataclass
class RestoreReport:
    """What a restore actually did — never assumed, always reported."""

    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: OIDC fields the destination pins in its environment, so the file's values
    #: were ignored. Silently dropping them would look like a successful restore.
    env_locked: list[str] = field(default_factory=list)
    #: Secrets the file did not carry, which are therefore still whatever this
    #: install had before.
    secrets_missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class BackupFormatError(Exception):
    """The document is not a settings backup this version can read."""


# ── Export ───────────────────────────────────────────────────────────────────

def export_settings(db: Session, include_secrets: bool = True) -> dict[str, Any]:
    chaptarr = chaptarr_svc.load_config(db)
    oidc = oidc_config.load_config(db)

    chaptarr_out: dict[str, Any] = {name: getattr(chaptarr, name) for name in _CHAPTARR_FIELDS}
    oidc_out: dict[str, Any] = {name: getattr(oidc, name) for name in _OIDC_FIELDS}

    omitted: list[str] = []
    if include_secrets:
        chaptarr_out["api_key"] = chaptarr.api_key
        oidc_out["client_secret"] = oidc.client_secret
    else:
        # Only name a secret that actually exists — telling someone to re-enter
        # a Chaptarr key they never had is noise, not caution.
        if chaptarr.api_key:
            omitted.append(CHAPTARR_SECRET)
        if oidc.client_secret:
            omitted.append(OIDC_SECRET)

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "includes_secrets": include_secrets,
        "secrets_omitted": omitted,
        "_warning": WARNING_WITH_SECRETS if include_secrets else WARNING_SANITISED,
        # Informational: these came from the exporting machine's environment, so
        # they are values you could not have edited there.
        "oidc_env_locked": sorted(oidc.env_locked),
        "chaptarr": chaptarr_out,
        "oidc": oidc_out,
        "libation": appsettings.parse(appsettings.read_raw()),
    }


def suggested_filename(now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return f"libation-web-settings-{stamp}.json"


# ── Restore ──────────────────────────────────────────────────────────────────

def validate_document(doc: dict[str, Any]) -> None:
    """Refuse anything we would only half-understand, before touching the DB."""
    if not isinstance(doc, dict):
        raise BackupFormatError("Not a settings backup: expected a JSON object.")
    if doc.get("format") != FORMAT:
        raise BackupFormatError(
            "Not a Libation Web settings backup — the file is missing its "
            f"`format: {FORMAT}` marker."
        )
    version = doc.get("version")
    if not isinstance(version, int):
        raise BackupFormatError("The backup does not say which format version it is.")
    if version > VERSION:
        raise BackupFormatError(
            f"This backup was written by a newer version (format {version}; this "
            f"install reads up to {VERSION}). Update before restoring it."
        )


def restore_settings(
    db: Session,
    doc: dict[str, Any],
    sections: Optional[list[str]] = None,
) -> RestoreReport:
    """Apply the document. `sections` limits it; None means everything present."""
    validate_document(doc)

    wanted = set(sections) if sections is not None else set(SECTIONS)
    unknown = wanted - set(SECTIONS)
    if unknown:
        raise BackupFormatError(f"Unknown section(s): {', '.join(sorted(unknown))}")

    report = RestoreReport()
    declared_missing = {s for s in doc.get("secrets_omitted") or [] if isinstance(s, str)}

    todo: list[tuple[str, dict]] = []
    for name in SECTIONS:
        body = doc.get(name)
        if name not in wanted or not isinstance(body, dict):
            report.skipped.append(name)
        else:
            todo.append((name, body))

    # Every selected section is checked before any of them is written, so a bad
    # value in the last section cannot leave the first one already applied.
    for name, body in todo:
        if name == "chaptarr":
            _validate_chaptarr(body)

    for name, body in todo:
        if name == "chaptarr":
            _restore_chaptarr(db, body, report, declared_missing)
        elif name == "oidc":
            _restore_oidc(db, body, report, declared_missing)
        else:
            _restore_libation(body, report)
        report.applied.append(name)

    return report


def _validate_chaptarr(body: dict) -> None:
    mode = body.get("import_mode")
    if mode is not None and mode not in chaptarr_svc.IMPORT_MODES:
        raise BackupFormatError(
            f"chaptarr.import_mode must be one of {', '.join(chaptarr_svc.IMPORT_MODES)}"
        )
    skip_when = body.get("skip_when")
    if skip_when is not None and skip_when not in chaptarr_svc.SKIP_MODES:
        raise BackupFormatError(
            f"chaptarr.skip_when must be one of {', '.join(chaptarr_svc.SKIP_MODES)}"
        )


def _restore_chaptarr(
    db: Session, body: dict, report: RestoreReport, declared_missing: set[str]
) -> None:
    patch = {
        f"chaptarr_{name}": body[name]
        for name in _CHAPTARR_FIELDS
        if body.get(name) is not None
    }
    # Absent means "keep what is stored" — the same rule the Settings PUT uses,
    # so restoring a sanitised backup does not wipe a working key. `null` counts
    # as absent too: a document that lists its fields explicitly must behave the
    # same as one that omits them. Clearing a key is `""`, as it is in the UI.
    if body.get("api_key") is not None:
        patch["chaptarr_api_key"] = body["api_key"]
    elif CHAPTARR_SECRET in declared_missing:
        report.secrets_missing.append(CHAPTARR_SECRET)

    cfg = chaptarr_svc.save_config(db, patch)
    # A different URL or key is a different library; the cached index would
    # otherwise answer skip-checks from the old instance.
    chaptarr_svc.invalidate_library_cache()

    if cfg.enabled and not cfg.configured:
        report.warnings.append(
            "Chaptarr is switched on but not usable yet — it still needs "
            + ("an API key" if not cfg.api_key else "a URL")
            + " in Settings → Integrations."
        )


def _restore_oidc(
    db: Session, body: dict, report: RestoreReport, declared_missing: set[str]
) -> None:
    patch = {name: body[name] for name in _OIDC_FIELDS if body.get(name) is not None}
    if body.get("client_secret") is not None:
        # Stored re-encrypted under this install's own key by save_config.
        patch["client_secret"] = body["client_secret"]
    elif OIDC_SECRET in declared_missing:
        report.secrets_missing.append(OIDC_SECRET)

    locked = oidc_config.load_config(db).env_locked
    ignored = sorted(locked & set(patch))
    cfg = oidc_config.save_config(db, patch)

    if ignored:
        report.env_locked.extend(ignored)
        report.warnings.append(
            "These OIDC fields are pinned by this deployment's environment and "
            "were left as they are: " + ", ".join(ignored) + "."
        )
    if cfg.enabled and not cfg.configured:
        report.warnings.append(
            "SSO is switched on but not complete, so it stays inactive and "
            "password sign-in stays available. Finish it in Settings → Sign-in."
        )


def _restore_libation(body: dict, report: RestoreReport) -> None:
    known = {k: v for k, v in body.items() if k in appsettings.FIELD_MAP and v is not None}
    appsettings.apply(known)
    unknown = sorted(set(body) - set(appsettings.FIELD_MAP))
    if unknown:
        report.warnings.append(
            "Ignored unrecognised Libation setting(s): " + ", ".join(unknown) + "."
        )
