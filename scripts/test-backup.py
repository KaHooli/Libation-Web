#!/usr/bin/env python3
"""Settings backup and restore.

A backup is only worth having if restoring it reproduces the setup, and only
safe to hand around if you know what is in it. Both halves have sharp edges:

  * **The OIDC client secret is stored encrypted under a key that does not
    travel.** Exporting the ciphertext would restore to something the
    destination cannot read — silently, because a bad secret looks exactly like
    a working one until someone tries to sign in. So it leaves as plaintext and
    is re-encrypted under the destination's own key.
  * **A sanitised backup must not wipe the secrets it declined to carry.** The
    "absent means keep" rule is the difference between a shareable copy and a
    file that quietly disconnects Chaptarr on restore.
  * **Environment-pinned fields cannot be restored over.** A restore that
    reports success while the destination ignored half of it is worse than one
    that refuses, so the report names them.
  * **Audible credentials must never be in the file**, in either mode. They are
    a device registration; Amazon caps how many an account may have.
  * **A restore must not be able to lock anyone out.** Password sign-in retires
    only once SSO has *actually worked* on this install, which a restored
    configuration cannot fake.

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-backup.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="backup-test-"))
CONFIG = WORKDIR / "config"
DATA = WORKDIR / "data"
BOOKS = WORKDIR / "audiobooks"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

# Must be set before `app.config` is imported.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "backup-test-only-not-a-real-secret")
os.environ.setdefault("ADMIN_PASSWORD", "backup-test-admin-pw")
# The OIDC env vars must be *unset*: anything supplied here would be env-locked
# and the database path — the one this feature actually restores into — would
# never be exercised.
for var in list(os.environ):
    if var.startswith("OIDC_") or var == "ALLOW_PASSWORD_LOGIN":
        os.environ.pop(var)

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}

CHAPTARR_KEY = "chaptarr-api-key-0123456789"
OIDC_SECRET = "oidc-client-secret-abcdef"


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────

def seed_settings(db) -> None:
    """A fully configured install: Chaptarr, SSO and the Libation toggles."""
    from app.services import appsettings, chaptarr as chaptarr_svc, oidc_config

    chaptarr_svc.save_config(db, {
        "chaptarr_enabled": True,
        "chaptarr_url": "http://chaptarr.local:8787",
        "chaptarr_api_key": CHAPTARR_KEY,
        "chaptarr_import_mode": "move",
        "chaptarr_auto_import": True,
        "chaptarr_path_from": "/audiobooks",
        "chaptarr_path_to": "/media/books",
        "chaptarr_skip_existing": True,
        "chaptarr_skip_when": "in_library",
    })
    oidc_config.save_config(db, {
        "enabled": True,
        "issuer": "https://sso.example.com/realms/main",
        "client_id": "libation-web",
        "client_secret": OIDC_SECRET,
        "provider_name": "Keycloak",
        "admin_group": "libation-admins",
        "auto_create_users": False,
    })
    appsettings.apply({
        "split_files_by_chapter": True,
        "decrypt_to_lossy": False,
        "books_directory": "/audiobooks",
    })


def clear_settings(db) -> None:
    """Wipe the destination's settings, standing in for a rebuilt container."""
    from sqlalchemy import text
    from app.services import appsettings, chaptarr as chaptarr_svc

    db.connection().execute(
        text("DELETE FROM system_settings WHERE key LIKE 'chaptarr\\_%' ESCAPE '\\' "
             "OR key LIKE 'oidc\\_%' ESCAPE '\\'")
    )
    db.commit()
    chaptarr_svc.invalidate_library_cache()
    appsettings.write_raw({})


# ── Export ───────────────────────────────────────────────────────────────────

def test_export_round_trips(db) -> dict:
    from app.services import backup

    doc = backup.export_settings(db, include_secrets=True)
    assert doc["format"] == backup.FORMAT and doc["version"] == backup.VERSION
    assert doc["includes_secrets"] is True
    assert doc["secrets_omitted"] == []

    assert doc["chaptarr"]["url"] == "http://chaptarr.local:8787"
    assert doc["chaptarr"]["import_mode"] == "move"
    assert doc["chaptarr"]["skip_when"] == "in_library"
    assert doc["chaptarr"]["api_key"] == CHAPTARR_KEY
    assert doc["oidc"]["issuer"] == "https://sso.example.com/realms/main"
    assert doc["oidc"]["provider_name"] == "Keycloak"
    assert doc["oidc"]["auto_create_users"] is False, "an explicit false must survive the export"
    assert doc["libation"]["split_files_by_chapter"] is True
    print("✓ an export carries Chaptarr, SSO and the Libation toggles verbatim")

    # The stored form is Fernet ciphertext under a key that stays on the box.
    # Shipping that would restore to a secret the destination cannot read.
    from sqlalchemy import text
    stored = db.connection().execute(
        text("SELECT value FROM system_settings WHERE key = 'oidc_client_secret_enc'")
    ).scalar()
    assert stored and stored != OIDC_SECRET, "the secret is not encrypted at rest"
    assert doc["oidc"]["client_secret"] == OIDC_SECRET, (
        "the export shipped ciphertext; a restore under a different key would "
        "produce an unusable secret that looks fine until someone signs in"
    )
    print("✓ the OIDC secret is decrypted on the way out, not shipped as ciphertext")
    return doc


def test_export_is_serialisable(doc: dict) -> None:
    """It is written to a file, so nothing in it may be a set or a dataclass."""
    text = json.dumps(doc)
    assert json.loads(text) == doc
    print("✓ the document survives a JSON round trip")


def test_sanitised_export_withholds_secrets(db) -> dict:
    from app.services import backup

    doc = backup.export_settings(db, include_secrets=False)
    assert doc["includes_secrets"] is False
    assert "api_key" not in doc["chaptarr"], "the Chaptarr key leaked into a sanitised backup"
    assert "client_secret" not in doc["oidc"], "the OIDC secret leaked into a sanitised backup"
    assert set(doc["secrets_omitted"]) == {backup.CHAPTARR_SECRET, backup.OIDC_SECRET}

    blob = json.dumps(doc)
    assert CHAPTARR_KEY not in blob and OIDC_SECRET not in blob, (
        "a secret appears somewhere in a sanitised document"
    )
    # Everything else must still be there, or the sanitised copy is useless.
    assert doc["chaptarr"]["url"] == "http://chaptarr.local:8787"
    assert doc["oidc"]["client_id"] == "libation-web"
    print("✓ a sanitised export withholds both secrets, names them, and keeps the rest")
    return doc


def test_sanitised_export_names_only_real_secrets(db) -> None:
    """Telling someone to re-enter a key they never had is noise, not caution."""
    from app.services import backup, chaptarr as chaptarr_svc

    chaptarr_svc.save_config(db, {"chaptarr_api_key": ""})
    doc = backup.export_settings(db, include_secrets=False)
    assert backup.CHAPTARR_SECRET not in doc["secrets_omitted"], doc["secrets_omitted"]
    assert backup.OIDC_SECRET in doc["secrets_omitted"]
    chaptarr_svc.save_config(db, {"chaptarr_api_key": CHAPTARR_KEY})
    print("✓ secrets_omitted lists only secrets that actually exist")


def test_no_audible_credentials_anywhere(db) -> None:
    """The one thing that must never travel, in either mode."""
    from app.services import backup

    for include in (True, False):
        blob = json.dumps(backup.export_settings(db, include_secrets=include))
        for forbidden in ("auth_blob", "audible_account", "adp_token", "potation.key"):
            assert forbidden not in blob, (
                f"{forbidden!r} appears in a backup (include_secrets={include}); an "
                "Audible device registration must never leave the install"
            )
    print("✓ neither export carries anything resembling an Audible registration")


# ── Restore ──────────────────────────────────────────────────────────────────

def test_restore_reproduces_the_setup(db, doc: dict) -> None:
    from app.services import appsettings, backup, chaptarr as chaptarr_svc, oidc_config

    clear_settings(db)
    assert not chaptarr_svc.load_config(db).url, "the fixture did not actually clear"

    report = backup.restore_settings(db, doc)
    assert sorted(report.applied) == ["chaptarr", "libation", "oidc"], report.applied
    assert report.env_locked == [] and report.secrets_missing == []

    chaptarr = chaptarr_svc.load_config(db)
    assert chaptarr.url == "http://chaptarr.local:8787"
    assert chaptarr.api_key == CHAPTARR_KEY
    assert chaptarr.import_mode == "move" and chaptarr.skip_when == "in_library"
    assert chaptarr.enabled and chaptarr.auto_import and chaptarr.skip_existing

    oidc = oidc_config.load_config(db)
    assert oidc.issuer == "https://sso.example.com/realms/main"
    assert oidc.client_secret == OIDC_SECRET, "the secret did not survive the round trip"
    assert oidc.auto_create_users is False, "an explicit false was lost on restore"
    assert oidc.admin_group == "libation-admins"

    assert appsettings.parse(appsettings.read_raw())["split_files_by_chapter"] is True
    print("✓ restoring a full backup reproduces every section")


def test_restored_secret_is_re_encrypted(db) -> None:
    """Plaintext in, ciphertext at rest — the restore must not store it raw."""
    from sqlalchemy import text
    from app.services.potation import creds

    stored = db.connection().execute(
        text("SELECT value FROM system_settings WHERE key = 'oidc_client_secret_enc'")
    ).scalar()
    assert stored != OIDC_SECRET, "the restore wrote the client secret in plain text"
    assert creds.try_decrypt(stored) == OIDC_SECRET
    print("✓ the restored secret is re-encrypted under this install's own key")


def test_sanitised_restore_keeps_stored_secrets(db, sanitised: dict) -> None:
    """The rule that makes a shareable backup safe to actually use."""
    from app.services import backup, chaptarr as chaptarr_svc, oidc_config

    report = backup.restore_settings(db, sanitised)
    assert sorted(report.secrets_missing) == sorted(
        [backup.CHAPTARR_SECRET, backup.OIDC_SECRET]
    ), report.secrets_missing

    assert chaptarr_svc.load_config(db).api_key == CHAPTARR_KEY, (
        "a sanitised restore wiped the Chaptarr API key it deliberately did not carry"
    )
    assert oidc_config.load_config(db).client_secret == OIDC_SECRET, (
        "a sanitised restore wiped the stored OIDC client secret"
    )
    print("✓ a sanitised restore keeps the stored secrets and reports what it could not set")


def test_explicit_null_means_absent_not_clear(db, sanitised: dict) -> None:
    """A document that spells its empty fields out must behave like one that omits them.

    Anything that has been through a schema — the API's own pydantic model
    included — arrives with every optional field present and null. If null read
    as "clear this", passing a sanitised backup through the API would wipe the
    secrets it deliberately declined to carry.
    """
    from app.services import backup, chaptarr as chaptarr_svc, oidc_config

    clear_settings(db)
    seed_settings(db)
    spelled_out = {
        **sanitised,
        "chaptarr": {**sanitised["chaptarr"], "api_key": None},
        "oidc": {**sanitised["oidc"], "client_secret": None},
    }
    report = backup.restore_settings(db, spelled_out)

    assert chaptarr_svc.load_config(db).api_key == CHAPTARR_KEY, (
        "an explicit null cleared the Chaptarr API key instead of keeping it"
    )
    assert oidc_config.load_config(db).client_secret == OIDC_SECRET, (
        "an explicit null cleared the stored OIDC client secret"
    )
    assert sorted(report.secrets_missing) == sorted(
        [backup.CHAPTARR_SECRET, backup.OIDC_SECRET]
    ), report.secrets_missing

    # And "" still clears, which is how the UI removes a key.
    backup.restore_settings(db, {**sanitised, "chaptarr": {**sanitised["chaptarr"], "api_key": ""}})
    assert chaptarr_svc.load_config(db).api_key == "", "an empty string no longer clears the key"
    print("✓ null means 'keep what is stored'; \"\" still clears")


def test_sanitised_restore_onto_a_bare_install(db, sanitised: dict) -> None:
    """Enabled but incomplete must stay inert, and must say so."""
    from app.services import backup, chaptarr as chaptarr_svc, oidc_config

    clear_settings(db)
    report = backup.restore_settings(db, sanitised)

    chaptarr = chaptarr_svc.load_config(db)
    assert chaptarr.enabled and not chaptarr.configured
    assert not chaptarr.skip_check_active, (
        "Chaptarr with no API key is answering skip-checks — a download would be "
        "refused on the word of an instance we cannot talk to"
    )
    oidc = oidc_config.load_config(db)
    assert oidc.enabled and not oidc.configured
    assert oidc_config.password_login_enabled(db), (
        "an incomplete restored SSO config switched password sign-in off"
    )
    assert len(report.warnings) >= 2, report.warnings
    assert any("Chaptarr" in w for w in report.warnings)
    assert any("SSO" in w for w in report.warnings)
    print("✓ a secret-less restore leaves both integrations inert and warns about each")


def test_restore_cannot_lock_you_out(db, doc: dict) -> None:
    """A restored config is not proof that SSO works, and must not act like it."""
    from app.services import backup, oidc_config

    clear_settings(db)
    backup.restore_settings(db, doc)
    cfg = oidc_config.load_config(db)
    assert cfg.configured, "the fixture should restore a complete SSO config"
    assert not oidc_config.sso_has_worked(db, cfg)
    assert oidc_config.password_login_enabled(db), (
        "restoring a complete SSO config retired password sign-in — on an install "
        "where nobody has ever signed in through that provider, that is a lockout"
    )
    print("✓ a complete restored SSO config does not retire password sign-in")


def test_section_selection(db, doc: dict) -> None:
    from app.services import backup, chaptarr as chaptarr_svc, oidc_config

    clear_settings(db)
    report = backup.restore_settings(db, doc, sections=["chaptarr"])
    assert report.applied == ["chaptarr"], report.applied
    assert sorted(report.skipped) == ["libation", "oidc"], report.skipped
    assert chaptarr_svc.load_config(db).url == "http://chaptarr.local:8787"
    assert not oidc_config.load_config(db).issuer, "an unselected section was restored anyway"
    print("✓ restoring one section leaves the others alone")


def test_absent_sections_are_reported_not_invented(db, doc: dict) -> None:
    from app.services import backup

    partial = {k: v for k, v in doc.items() if k != "oidc"}
    report = backup.restore_settings(db, partial)
    assert "oidc" in report.skipped and "oidc" not in report.applied
    assert "chaptarr" in report.applied
    print("✓ a section the file does not carry is reported skipped, never applied")


# ── Refusals ─────────────────────────────────────────────────────────────────

def test_rejects_foreign_and_future_documents(db, doc: dict) -> None:
    from app.services import backup

    def refused(payload, because: str) -> str:
        try:
            backup.restore_settings(db, payload)
        except backup.BackupFormatError as exc:
            return str(exc)
        raise AssertionError(f"accepted a document that {because}")

    for needle, payload, because in (
        ("Libation Web settings backup", {"chaptarr": {"url": "http://evil"}},
         "carries no format marker"),
        ("newer version", {**doc, "version": backup.VERSION + 1},
         "declares a newer format version"),
        ("which format version", {**doc, "version": "1"},
         "has a non-integer version"),
    ):
        message = refused(payload, because)
        assert needle in message, (needle, message)
    print("✓ a foreign, versionless or future document is refused before anything is written")


def test_rejects_bad_enumerations_before_writing(db, doc: dict) -> None:
    """A malformed value is told about, not silently coerced to the default.

    Also pins the all-or-nothing property: every selected section is validated
    before any of them is written, so one bad value cannot leave a neighbouring
    section already applied.
    """
    from app.services import appsettings, backup, chaptarr as chaptarr_svc, oidc_config

    for field, value in (("import_mode", "teleport"), ("skip_when", "whenever")):
        clear_settings(db)
        bad = {**doc, "chaptarr": {**doc["chaptarr"], field: value}}
        try:
            backup.restore_settings(db, bad)
            raise AssertionError(f"accepted an invalid {field}")
        except backup.BackupFormatError as exc:
            assert field in str(exc), str(exc)

        assert not chaptarr_svc.load_config(db).url, (
            "the invalid section was partly written before the error surfaced"
        )
        assert not oidc_config.load_config(db).issuer, (
            "a neighbouring section was written before the document was rejected"
        )
        assert appsettings.read_raw() == {}, (
            "the Libation toggles were written before the document was rejected"
        )
    print("✓ an invalid value is refused and no section is written at all")


def test_rejects_unknown_sections(db, doc: dict) -> None:
    from app.services import backup
    try:
        backup.restore_settings(db, doc, sections=["chaptarr", "users"])
        raise AssertionError("accepted a section name that does not exist")
    except backup.BackupFormatError as exc:
        assert "users" in str(exc)
    print("✓ an unknown section name is refused rather than quietly ignored")


def test_environment_pinned_fields_are_reported(db, doc: dict) -> None:
    """A restore that says 'done' while the destination ignored half of it lies."""
    from app.config import settings
    from app.services import backup, oidc_config

    clear_settings(db)
    settings.OIDC_ISSUER = "https://pinned.example.com"
    settings.model_fields_set.add("OIDC_ISSUER")
    try:
        report = backup.restore_settings(db, doc, sections=["oidc"])
        assert report.env_locked == ["issuer"], report.env_locked
        assert any("environment" in w for w in report.warnings), report.warnings
        assert oidc_config.load_config(db).issuer == "https://pinned.example.com", (
            "a restore overwrote a field the environment pins"
        )
        # Everything not pinned still lands.
        assert oidc_config.load_config(db).client_id == "libation-web"
    finally:
        settings.model_fields_set.discard("OIDC_ISSUER")
        settings.OIDC_ISSUER = ""
    print("✓ environment-pinned fields survive a restore, and the report names them")


# ── The HTTP surface ─────────────────────────────────────────────────────────

def test_endpoints(client, db) -> None:
    from app.services import backup, chaptarr as chaptarr_svc

    clear_settings(db)
    seed_settings(db)

    r = client.post("/api/auth/login",
                    json={"username": "admin", "password": "backup-test-admin-pw"})
    assert r.status_code == 200, r.text
    admin = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post("/api/users", headers=admin,
                    json={"username": "plainuser", "password": "plain-user-pw-1", "is_admin": False})
    assert r.status_code in (200, 201), r.text
    r = client.post("/api/auth/login",
                    json={"username": "plainuser", "password": "plain-user-pw-1"})
    plain = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # The file holds live credentials and the restore rewrites how everyone
    # signs in; neither may be reachable without admin.
    assert client.get("/api/settings/backup").status_code == 401
    assert client.get("/api/settings/backup", headers=plain).status_code == 403
    assert client.post("/api/settings/restore", headers=plain, json={"backup": {}}).status_code == 403
    print("✓ both endpoints are admin-only")

    r = client.get("/api/settings/backup", headers=admin)
    assert r.status_code == 200, r.text
    disposition = r.headers.get("content-disposition", "")
    assert "attachment" in disposition and ".json" in disposition, disposition
    assert r.headers.get("cache-control") == "no-store", "a file of secrets is cacheable"
    doc = r.json()
    assert doc["chaptarr"]["api_key"] == CHAPTARR_KEY
    print("✓ GET /api/settings/backup downloads a document, with secrets by default")

    r = client.get("/api/settings/backup", headers=admin, params={"include_secrets": "false"})
    sanitised = r.json()
    assert "api_key" not in sanitised["chaptarr"]
    assert CHAPTARR_KEY not in r.text and OIDC_SECRET not in r.text
    print("✓ include_secrets=false is honoured over HTTP")

    # Over HTTP the document has been through pydantic, which fills every
    # optional field the file omitted. The stored key must survive that, and the
    # report must still say it could not be set — a restore that silently drops
    # the "you still need to enter this" line reads as a complete one.
    clear_settings(db)
    seed_settings(db)
    r = client.post("/api/settings/restore", headers=admin, json={"backup": sanitised})
    assert r.status_code == 200, r.text
    assert chaptarr_svc.load_config(db).api_key == CHAPTARR_KEY, (
        "over HTTP, a sanitised restore cleared the stored Chaptarr key"
    )
    assert backup.CHAPTARR_SECRET in r.json()["secrets_missing"], (
        f"the report did not name the secret it could not set: {r.json()}"
    )
    print("✓ a sanitised restore over HTTP keeps the stored secrets and says so")

    r = client.post("/api/settings/restore", headers=admin,
                    json={"backup": doc, "sections": ["libation"]})
    assert r.status_code == 200 and r.json()["applied"] == ["libation"], r.text
    print("✓ a section-limited restore works over HTTP")

    r = client.post("/api/settings/restore", headers=admin,
                    json={"backup": {"chaptarr": {"url": "http://evil"}}})
    assert r.status_code == 422, (r.status_code, r.text)
    assert "Libation Web settings backup" in r.json()["detail"], r.json()
    print("✓ a foreign document is refused with an explanation, not a field error")


# ── Runner ───────────────────────────────────────────────────────────────────

def main() -> None:
    from app.database import SessionLocal
    from app.migrations import run_migrations

    run_migrations()

    with SessionLocal() as db:
        seed_settings(db)

        doc = test_export_round_trips(db)
        test_export_is_serialisable(doc)
        sanitised = test_sanitised_export_withholds_secrets(db)
        test_sanitised_export_names_only_real_secrets(db)
        test_no_audible_credentials_anywhere(db)

        test_restore_reproduces_the_setup(db, doc)
        test_restored_secret_is_re_encrypted(db)
        test_sanitised_restore_keeps_stored_secrets(db, sanitised)
        test_explicit_null_means_absent_not_clear(db, sanitised)
        test_sanitised_restore_onto_a_bare_install(db, sanitised)
        test_restore_cannot_lock_you_out(db, doc)
        test_section_selection(db, doc)
        test_absent_sections_are_reported_not_invented(db, doc)

        test_rejects_foreign_and_future_documents(db, doc)
        test_rejects_bad_enumerations_before_writing(db, doc)
        test_rejects_unknown_sections(db, doc)
        test_environment_pinned_fields_are_reported(db, doc)

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        with SessionLocal() as db:
            test_endpoints(client, db)

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll backup checks passed.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
