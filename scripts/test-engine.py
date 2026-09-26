#!/usr/bin/env python3
"""The POTATION_ENGINE switch, on the accounts API.

Two things are being pinned here, and the second matters more than the first.

1. **With the flag off, nothing changed.** The LibationCli path is what every
   existing deployment is running, and a new image must not move anyone onto a
   migration they did not ask for. The checks below drive the API with the flag
   off and assert it still goes to `cli.list_accounts()`.

2. **With the flag on, the silent failures are loud.** Re-authorising mints a
   fresh account id, and three things still point at the old one — none of which
   error. `GET /api/accounts/unlinked` is what turns "My Books is mysteriously
   empty" into a named, fixable reference, so it is checked against a database
   that really holds a stale pointer.

Run: PYTHONPATH=backend python scripts/test-engine.py
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

WORK = Path(tempfile.mkdtemp(prefix="engine-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{WORK / 'app.db'}"
os.environ["LIBATION_CONFIG"] = str(WORK / "config")
os.environ["AUDIOBOOKS_DIR"] = str(WORK / "audiobooks")
os.environ["SECRET_KEY"] = "engine-test-only"
os.environ["ADMIN_PASSWORD"] = "engine-test-admin-password"
(WORK / "config").mkdir(parents=True, exist_ok=True)
(WORK / "audiobooks").mkdir(parents=True, exist_ok=True)

#: Snapshot before the app is imported, so the check below reports only what
#: this run created. A CI image may ship one of these already.
PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}

PASSES: list[str] = []


def ok(label: str) -> None:
    PASSES.append(label)
    print(f"✓ {label}")


# ── Fixtures ─────────────────────────────────────────────────────────────────

#: The account the API checks sign in as. Created directly rather than relying on
#: the app's own seeding, so a check does not depend on whether TestClient ran
#: the lifespan.
ADMIN_USERNAME = "engine-test-admin"
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

_MIGRATED = False


def fresh_db():
    """A database holding nothing but the admin.

    The engine is bound once at import, so every check shares one file and
    "fresh" has to mean *emptied* rather than *recreated*. Clearing explicitly
    beats letting rows accumulate: a check that counts what it created is only
    meaningful if nothing earlier is still there.
    """
    global _MIGRATED
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.migrations import run_migrations

    if not _MIGRATED:
        run_migrations()
        _MIGRATED = True

    with SessionLocal() as db:
        conn = db.connection()
        for table in ("audible_accounts", "audible_account_settings",
                      "audible_login_states", "users"):
            conn.execute(text(f"DELETE FROM {table}"))
        db.commit()
        make_user(db, ADMIN_USERNAME, is_admin=True, password=ADMIN_PASSWORD)

    return SessionLocal


def make_account(db, account_id: str, **kw):
    from app.models.potation import AudibleAccount

    account = AudibleAccount(
        account_id=account_id,
        locale=kw.pop("locale", "us"),
        account_name=kw.pop("account_name", None),
        email=kw.pop("email", None),
        auth_blob=kw.pop("auth_blob", "ciphertext"),
        **kw,
    )
    db.add(account)
    db.commit()
    return account


def make_user(db, username: str, *, is_admin=False, audible_account_id=None,
              password="password-for-tests"):
    from app.models.user import User
    from app.services.auth import hash_password

    user = User(
        username=username,
        hashed_password=hash_password(password),
        is_admin=is_admin,
        is_active=True,
        audible_account_id=audible_account_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ── The response mapping ─────────────────────────────────────────────────────

def test_every_account_is_listed_including_broken_ones() -> None:
    """`active_accounts()` hides what needs re-authorising. The page must not.

    An account that has fallen out of authorisation is precisely the one
    somebody needs to see and act on; filtering it out would present a missing
    account rather than a broken one.
    """
    from app.services.potation import accounts as svc

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "ACC-GOOD", account_name="Working")
        make_account(db, "ACC-BROKEN", account_name="Stale", needs_reauth=True)
        make_account(db, "ACC-OFF", account_name="Disabled", is_active=False)

        listed = svc.list_accounts(db)
        by_id = {a["account_id"]: a for a in listed}

    assert set(by_id) == {"ACC-GOOD", "ACC-BROKEN", "ACC-OFF"}, by_id
    ok("an account needing re-auth is listed, not hidden")

    assert by_id["ACC-GOOD"]["authenticated"] is True
    assert by_id["ACC-BROKEN"]["authenticated"] is False, (
        "needs_reauth must surface as authenticated=false, which is what the "
        "page renders a reconnect prompt from"
    )
    assert by_id["ACC-OFF"]["scan_library"] is False
    assert by_id["ACC-GOOD"]["scan_library"] is True
    ok("needs_reauth maps to authenticated, is_active maps to scan_library")


def test_a_nameless_account_still_has_something_to_show() -> None:
    """A blank name column is unusable — you cannot tell which row to remove."""
    from app.services.potation import accounts as svc

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "ACC-NONAME")
        make_account(db, "ACC-EMAIL", email="someone@example.com")
        by_id = {a["account_id"]: a for a in svc.list_accounts(db)}

    assert by_id["ACC-NONAME"]["name"] == "ACC-NONAME"
    assert by_id["ACC-EMAIL"]["name"] == "someone@example.com"
    ok("a nameless account falls back to its email, then its id")


def test_owner_is_joined_from_users() -> None:
    from app.services.potation import accounts as svc

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "ACC-1", account_name="Shared")
        make_user(db, "jo", audible_account_id="ACC-1")
        user = make_user(db, "sam")
        user.owner_name = "Sam"
        user.audible_account_id = "ACC-1"
        db.commit()

        listed = svc.list_accounts(db)

    assert len(listed) == 1
    assert listed[0]["owner_username"] in {"jo", "sam"}
    ok("the owner is joined on from users.audible_account_id")


def test_auto_download_moves_to_the_account_row() -> None:
    from app.services.potation import accounts as svc

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "ACC-1", added_by_user_id=None)

        svc.set_auto_download(db, "ACC-1", True)
        assert svc.list_accounts(db)[0]["auto_download"] is True

        svc.set_auto_download(db, "ACC-1", False)
        assert svc.list_accounts(db)[0]["auto_download"] is False
        ok("auto-download round-trips through the account row")

        try:
            svc.set_auto_download(db, "NO-SUCH-ACCOUNT", True)
        except LookupError:
            ok("configuring an account that does not exist raises, not creates")
        else:
            raise AssertionError(
                "set_auto_download invented an account row; under LibationCli the "
                "settings table was created on the fly, but a configuration flag "
                "must not conjure the account it configures"
            )


# ── The continuity hazard ────────────────────────────────────────────────────

def test_a_stale_account_pointer_is_reported() -> None:
    """The failure this prevents returns zero rows rather than an error."""
    from app.services.potation import accounts as svc

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        # The user still points at the id LibationCli minted; the account that
        # exists now is the one re-authorising created.
        make_account(db, "NEW-ACCOUNT-ID", account_name="Reconnected")
        make_user(db, "jo", audible_account_id="OLD-LIBATION-ID")

        stale = svc.unlinked_references(db)

    assert len(stale) == 1, stale
    assert stale[0]["account_id"] == "OLD-LIBATION-ID"
    assert stale[0]["users"] == ["jo"], (
        "the report has to name who is affected — a bare id leaves someone "
        "hunting through the users table for the one to repoint"
    )
    ok("a user pointing at a vanished account id is reported, by name")


def test_nothing_is_reported_once_everything_lines_up() -> None:
    from app.services.potation import accounts as svc

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "ACC-1")
        make_user(db, "jo", audible_account_id="ACC-1")
        assert svc.unlinked_references(db) == []
    ok("a correctly linked account produces no warning")


def test_a_user_with_no_account_is_not_a_stale_reference() -> None:
    """NULL means "not linked yet", which is the ordinary state, not a fault."""
    from app.services.potation import accounts as svc

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "ACC-1")
        make_user(db, "jo", audible_account_id=None)
        assert svc.unlinked_references(db) == []
    ok("a user with no linked account raises no false alarm")


# ── The flag itself ──────────────────────────────────────────────────────────

def _client_and_token(engine_on: bool):
    """A TestClient with a logged-in admin, at the requested engine setting."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    settings.POTATION_ENGINE = engine_on
    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return client, resp.json()["access_token"]


def test_the_flag_off_still_goes_to_libationcli() -> None:
    """The upgrade guarantee: a new image must not migrate anyone by itself."""
    from app.services import cli

    fresh_db()
    client, token = _client_and_token(False)
    called: list[bool] = []

    async def fake_list_accounts():
        called.append(True)
        return [{
            "account_id": "CLI-ACCOUNT", "name": "From LibationCli",
            "locale": "us", "scan_library": True, "authenticated": True,
        }]

    original = cli.list_accounts
    cli.list_accounts = fake_list_accounts
    try:
        resp = client.get("/api/accounts", headers={"Authorization": f"Bearer {token}"})
    finally:
        cli.list_accounts = original

    assert resp.status_code == 200, resp.text
    assert called, "the LibationCli path was not taken with POTATION_ENGINE off"
    assert resp.json()[0]["account_id"] == "CLI-ACCOUNT"
    ok("with the flag off, accounts still come from LibationCli")

    resp = client.get("/api/accounts/unlinked", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.json() == [], resp.text
    ok("with the flag off, no migration warning is produced")


def test_the_flag_on_reads_the_database_and_never_shells_out() -> None:
    from app.services import cli

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "DB-ACCOUNT", account_name="From Potation")

    client, token = _client_and_token(True)

    async def explode():
        raise AssertionError(
            "the Potation path shelled out to libationcli; with the engine on, "
            "the CLI must never be reached"
        )

    original = cli.list_accounts
    cli.list_accounts = explode
    try:
        resp = client.get("/api/accounts", headers={"Authorization": f"Bearer {token}"})
    finally:
        cli.list_accounts = original

    assert resp.status_code == 200, resp.text
    ids = [a["account_id"] for a in resp.json()]
    assert ids == ["DB-ACCOUNT"], ids
    ok("with the flag on, accounts come from the database and the CLI is untouched")


def test_delete_under_potation_reports_a_missing_account() -> None:
    fresh_db()
    client, token = _client_and_token(True)
    resp = client.delete(
        "/api/accounts/NOT-A-REAL-ACCOUNT", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
    ok("removing an account that does not exist is a 404, not a 500")


def test_delete_under_potation_deregisters_the_device() -> None:
    """The bug this cut-over fixes.

    `DELETE` used to edit `AccountsSettings.json` and nothing else, so every
    removal left a registered device at Amazon — and Amazon caps how many an
    account may hold, so the slot was gone for good.
    """
    from app.services.potation import client as potation_client

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "ACC-TO-REMOVE", account_name="Doomed")

    deregistered: list[str] = []

    class FakeAuthenticator:
        def deregister_device(self):
            deregistered.append("called")

    original = potation_client.load_authenticator
    potation_client.load_authenticator = lambda db, account: FakeAuthenticator()
    try:
        client, token = _client_and_token(True)
        resp = client.delete(
            "/api/accounts/ACC-TO-REMOVE", headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        potation_client.load_authenticator = original

    assert resp.status_code == 200, resp.text
    assert deregistered, (
        "the account was removed without deregistering the device — the exact "
        "bug the LibationCli path had, and the reason this path exists"
    )

    with SessionLocal() as db:
        from app.models.potation import AudibleAccount
        assert db.query(AudibleAccount).filter(
            AudibleAccount.account_id == "ACC-TO-REMOVE"
        ).first() is None
    ok("removing an account deregisters the device and drops the row")


def test_a_failed_deregistration_still_removes_the_account() -> None:
    """Best-effort, deliberately: an unreachable Amazon must not strand a row
    the user has asked to be rid of."""
    from app.services.potation import client as potation_client

    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_account(db, "ACC-OFFLINE", account_name="Unreachable")

    def explode(db, account):
        raise RuntimeError("Amazon is unreachable")

    original = potation_client.load_authenticator
    potation_client.load_authenticator = explode
    try:
        client, token = _client_and_token(True)
        resp = client.delete(
            "/api/accounts/ACC-OFFLINE", headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        potation_client.load_authenticator = original

    assert resp.status_code == 200, resp.text
    with SessionLocal() as db:
        from app.models.potation import AudibleAccount
        assert db.query(AudibleAccount).filter(
            AudibleAccount.account_id == "ACC-OFFLINE"
        ).first() is None
    ok("a failed deregistration still removes the account locally")


def test_unlinked_is_served_over_the_api() -> None:
    SessionLocal = fresh_db()
    with SessionLocal() as db:
        make_user(db, "stranded", audible_account_id="GONE-ACCOUNT-ID")

    client, token = _client_and_token(True)
    resp = client.get("/api/accounts/unlinked", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    stale = {row["account_id"]: row for row in body}
    assert "GONE-ACCOUNT-ID" in stale, body
    assert "stranded" in stale["GONE-ACCOUNT-ID"]["users"]
    ok("the stale reference is served over the API, naming the affected user")


def test_no_stray_directories_created() -> None:
    """The suite runs unprivileged; nothing may write to the packaged paths."""
    created = sorted(str(p) for p in PRODUCTION_PATHS
                     if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )
    ok("no stray top-level directories created")


def main() -> int:
    tests = [
        test_every_account_is_listed_including_broken_ones,
        test_a_nameless_account_still_has_something_to_show,
        test_owner_is_joined_from_users,
        test_auto_download_moves_to_the_account_row,
        test_a_stale_account_pointer_is_reported,
        test_nothing_is_reported_once_everything_lines_up,
        test_a_user_with_no_account_is_not_a_stale_reference,
        test_the_flag_off_still_goes_to_libationcli,
        test_the_flag_on_reads_the_database_and_never_shells_out,
        test_delete_under_potation_reports_a_missing_account,
        test_delete_under_potation_deregisters_the_device,
        test_a_failed_deregistration_still_removes_the_account,
        test_unlinked_is_served_over_the_api,
        test_no_stray_directories_created,
    ]
    for test in tests:
        test()

    print(f"\nAll {len(PASSES)} engine checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
