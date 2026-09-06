#!/usr/bin/env python3
"""First-run admin seeding and the forced password change.

A fresh install used to come up as admin/admin. It now generates a random
password, prints it to the container log, and holds the account at a change
prompt until it is replaced. That is a security-shaped change to the one
account that owns the instance, so the failure modes matter:

  * a deployment that sets ADMIN_PASSWORD must be completely unaffected —
    breaking those on upgrade would lock people out of working installs
  * the generated password must never reach the log *file*, which lives on a
    mounted volume and would outlive the password by years
  * losing the password before first sign-in must be recoverable, since a
    bcrypt hash cannot be read back
  * changing the password must actually clear the flag, or the account is
    stuck at the prompt forever

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-firstrun.py
"""
import io
import os
import re
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="firstrun-test-"))
CONFIG = WORKDIR / "config"
DATA = WORKDIR / "data"
BOOKS = WORKDIR / "audiobooks"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

# Must be set before `app.config` is imported. ADMIN_PASSWORD is deliberately
# absent: that is the case under test.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "firstrun-test-only-not-a-real-secret")
os.environ.pop("ADMIN_PASSWORD", None)

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


PASSWORD_RE = re.compile(r"Password:\s+(\S+)")


def seed_capturing(seed_admin, db) -> tuple[str, str | None]:
    """Run the seeder with stdout captured; return (output, password or None)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        seed_admin(db)
    out = buf.getvalue()
    match = PASSWORD_RE.search(out)
    return out, match.group(1) if match else None


def wipe_users(db) -> None:
    from sqlalchemy import text
    db.connection().execute(text("DELETE FROM users"))
    db.commit()


def admin_row(db):
    from sqlalchemy import text
    return db.connection().execute(
        text("SELECT username, hashed_password, must_change_password"
             " FROM users WHERE username = 'admin'")
    ).first()


# ── Generation ───────────────────────────────────────────────────────────────

def test_password_shape() -> None:
    from app.main import generate_password

    seen = {generate_password() for _ in range(200)}
    assert len(seen) == 200, "generated passwords repeated within 200 draws"

    sample = generate_password()
    assert re.fullmatch(r"[A-Za-z2-9]{5}(-[A-Za-z2-9]{5}){3}", sample), sample

    # Characters that are easy to misread out of a terminal must not appear:
    # this password gets transcribed by hand from `docker logs`.
    ambiguous = set("il1oO0")
    body = "".join(seen)
    assert not (ambiguous & set(body)), sorted(ambiguous & set(body))
    print("✓ generated passwords are unique, grouped, and free of look-alike characters")


# ── Seeding ──────────────────────────────────────────────────────────────────

def test_generated_seed(db) -> str:
    from app.main import _seed_admin
    from app.services.auth import verify_password

    wipe_users(db)
    out, password = seed_capturing(_seed_admin, db)
    assert password, f"no password printed:\n{out}"
    assert "first-run administrator account" in out, out

    row = admin_row(db)
    assert row is not None, "admin was not created"
    assert verify_password(password, row[1]), "the printed password does not open the account"
    assert row[2] == 1, "must_change_password was not set on a generated password"
    print("✓ a first start with no ADMIN_PASSWORD generates one, prints it, and flags the account")
    return password


def test_regenerates_while_unchanged(db, previous: str) -> str:
    from app.main import _seed_admin
    from app.services.auth import verify_password

    out, password = seed_capturing(_seed_admin, db)
    assert password, f"restart printed no password:\n{out}"
    assert password != previous, "restart reprinted the same password instead of a fresh one"

    row = admin_row(db)
    assert verify_password(password, row[1]), "the new password does not open the account"
    assert not verify_password(previous, row[1]), "the superseded password still works"
    assert row[2] == 1, "must_change_password should still be set"
    print("✓ restarting before the first change rolls a new password — losing it is recoverable")
    return password


def test_supplied_password_is_untouched(db) -> None:
    """The upgrade path: a deployment that sets ADMIN_PASSWORD sees no change."""
    from app.config import settings
    from app.main import _seed_admin
    from app.services.auth import verify_password

    settings.ADMIN_PASSWORD = "pinned-by-the-deployment"
    try:
        wipe_users(db)
        out, printed = seed_capturing(_seed_admin, db)
        assert printed is None, "a supplied password must never be echoed to the log"
        row = admin_row(db)
        assert verify_password("pinned-by-the-deployment", row[1])
        assert row[2] == 0, "a supplied password must not force a change prompt"
        print("✓ a supplied ADMIN_PASSWORD is used as-is, never printed, and forces no prompt")

        # And a restart leaves it alone rather than rolling it over.
        out, printed = seed_capturing(_seed_admin, db)
        assert printed is None, out
        assert verify_password("pinned-by-the-deployment", admin_row(db)[1]), (
            "a restart changed a password the deployment had pinned"
        )
        print("✓ a restart does not disturb a pinned password")
    finally:
        settings.ADMIN_PASSWORD = ""


def test_password_stays_out_of_the_log_file(db) -> None:
    """The log file is on the /config volume; the password must not land there."""
    from app.main import _seed_admin
    from app.services.logger import log_file_path

    wipe_users(db)
    _, password = seed_capturing(_seed_admin, db)
    assert password

    path = Path(log_file_path())
    contents = path.read_text(errors="replace") if path.exists() else ""
    assert password not in contents, (
        f"the generated password was written to {path} — it lives on a mounted "
        "volume and would outlive the password"
    )
    print("✓ the generated password reaches stdout only, never the log file on /config")


# ── The HTTP flow ────────────────────────────────────────────────────────────

def test_forced_change_flow(client, password: str) -> None:
    r = client.post("/api/auth/login", json={"username": "admin", "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["must_change_password"] is True, body["user"]
    token = body["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    print("✓ signing in with the generated password reports must_change_password")

    # There is no factory default in play, so the old admin/admin banner must
    # stay quiet rather than reporting against an empty ADMIN_PASSWORD.
    r = client.get("/api/auth/default-credentials", headers=h)
    assert r.status_code == 200 and r.json()["using_default_credentials"] is False, r.json()
    print("✓ the default-credentials banner stays quiet when no password was supplied")

    # Re-using the same password would satisfy the prompt without changing
    # anything, which is the one way to defeat it.
    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": password, "new_password": password})
    assert r.status_code == 422, (r.status_code, r.text)
    print("✓ re-submitting the same password is refused")

    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": "not-the-password", "new_password": "a-good-one-1"})
    assert r.status_code == 401, (r.status_code, r.text)
    print("✓ a wrong current password is refused")

    # The refresh cookie from that login works right up until the change.
    assert client.post("/api/auth/refresh").status_code == 200

    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": password, "new_password": "a-good-one-1"})
    assert r.status_code == 200, r.text
    print("✓ the password change is accepted")

    # Changing the password revokes every session, so the refresh token can no
    # longer be exchanged. The access token it already issued is a stateless
    # 15-minute JWT and stays valid until it expires — that is the app's
    # existing design, and the client signs out immediately rather than relying
    # on it; what matters here is that the session cannot be renewed.
    assert client.post("/api/auth/refresh").status_code == 401, (
        "the old refresh token survived a password change"
    )
    r = client.post("/api/auth/login", json={"username": "admin", "password": "a-good-one-1"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["must_change_password"] is False, r.json()["user"]
    print("✓ the flag clears, old sessions are revoked, and the new password signs in cleanly")

    # And the account is not re-flagged on a later restart.
    from app.database import SessionLocal
    from app.main import _seed_admin
    with SessionLocal() as db:
        out, printed = seed_capturing(_seed_admin, db)
        assert printed is None, f"a settled account was handed a new password:\n{out}"
        assert admin_row(db)[2] == 0
    r = client.post("/api/auth/login", json={"username": "admin", "password": "a-good-one-1"})
    assert r.status_code == 200, "a restart invalidated the password the user had chosen"
    print("✓ once changed, a restart leaves the account alone")


def main() -> None:
    from app.database import SessionLocal
    from app.migrations import run_migrations

    run_migrations()
    test_password_shape()

    with SessionLocal() as db:
        password = test_generated_seed(db)
        password = test_regenerates_while_unchanged(db, password)
        test_supplied_password_is_untouched(db)
        test_password_stays_out_of_the_log_file(db)

    # Boot the app for the HTTP half. Its lifespan re-seeds, which rolls the
    # password again — capture that one rather than assuming the earlier value.
    # Only startup is captured: wrapping the whole block would swallow the
    # progress output of every check below it.
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    buf = io.StringIO()
    with redirect_stdout(buf):
        client.__enter__()  # runs the lifespan, and with it _seed_admin
    try:
        match = PASSWORD_RE.search(buf.getvalue())
        assert match, f"app startup printed no password:\n{buf.getvalue()}"
        test_forced_change_flow(client, match.group(1))
    finally:
        client.__exit__(None, None, None)

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll first-run checks passed.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
