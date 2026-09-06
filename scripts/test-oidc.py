#!/usr/bin/env python3
"""End-to-end test of OIDC single sign-on, against a stub identity provider.

Boots the FastAPI app against throwaway directories and points it at an
in-process provider that serves a real discovery document, a real JWKS, and
RSA-signed ID tokens — so the token validation path is genuinely exercised
rather than mocked out.

Covers the flow (PKCE, state, nonce, code exchange, provisioning) and, more
importantly, the ways it must refuse: a replayed state, an expired one, a
mismatched nonce, a token signed by the wrong key or for the wrong audience or
issuer, and an HMAC-signed token offered against a JWKS.

Also covers the sign-in policy the deployment asked for: enabling SSO turns
password login off, and ALLOW_PASSWORD_LOGIN=true brings it back.

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-oidc.py
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WORKDIR = Path(tempfile.mkdtemp(prefix="oidc-test-"))
CONFIG = WORKDIR / "config"
BOOKS = WORKDIR / "audiobooks"
DATA = WORKDIR / "data"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

PROVIDER_PORT = 8799
ISSUER = f"http://127.0.0.1:{PROVIDER_PORT}"
CLIENT_ID = "libation-web"
CLIENT_SECRET = "stub-client-secret"

# Must be set before `app.config` is imported.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "oidc-test-only-not-a-real-secret")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin")

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from jose import jwt  # noqa: E402

# ── A signing key for the stub provider ───────────────────────────────────────

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "stub-key-1"
PRIVATE_PEM = KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

# A second key the provider never publishes, for the wrong-signature case.
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PEM = OTHER_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def _b64u(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def jwks_document() -> dict:
    numbers = KEY.public_key().public_numbers()
    return {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
        "n": _b64u(numbers.n), "e": _b64u(numbers.e),
    }]}


def make_id_token(
    *,
    subject="sso-user-1",
    nonce="",
    audience=CLIENT_ID,
    issuer=ISSUER,
    key=None,
    algorithm="RS256",
    extra=None,
    expires_in=300,
) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "nonce": nonce,
        "preferred_username": "alice",
        "email": "alice@example.com",
    }
    claims.update(extra or {})
    signing_key = key if key is not None else PRIVATE_PEM
    headers = {"kid": KID} if algorithm.startswith(("RS", "ES", "PS")) else None
    return jwt.encode(claims, signing_key, algorithm=algorithm, headers=headers)


# ── Stub provider ─────────────────────────────────────────────────────────────

# The token endpoint returns whatever the current test put here, so each case
# can control exactly what comes back.
next_token_response: dict = {}
received: dict = {"token_requests": []}


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the test output clean
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/.well-known/openid-configuration":
            return self._json({
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
                "id_token_signing_alg_values_supported": ["RS256"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                "scopes_supported": ["openid", "profile", "email"],
            })
        if path == "/jwks":
            return self._json(jwks_document())
        return self._json({"error": "not_found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/token":
            return self._json({"error": "not_found"}, 404)
        length = int(self.headers.get("Content-Length", 0))
        body = parse_qs(self.rfile.read(length).decode())
        received["token_requests"].append({k: v[0] for k, v in body.items()})
        if not next_token_response:
            return self._json({"error": "invalid_grant"}, 400)
        return self._json(next_token_response)


def start_provider() -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", PROVIDER_PORT), Provider)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ── Helpers ───────────────────────────────────────────────────────────────────

def configure(**overrides) -> None:
    """Point settings at the stub provider, then drop cached documents."""
    from app.config import settings
    from app.services import oidc as oidc_svc

    settings.OIDC_ENABLED = overrides.get("enabled", True)
    settings.OIDC_ISSUER = overrides.get("issuer", ISSUER)
    settings.OIDC_CLIENT_ID = overrides.get("client_id", CLIENT_ID)
    settings.OIDC_CLIENT_SECRET = overrides.get("client_secret", CLIENT_SECRET)
    settings.OIDC_REDIRECT_URL = overrides.get(
        "redirect_url", "http://testserver/api/auth/oidc/callback"
    )
    settings.OIDC_ADMIN_GROUP = overrides.get("admin_group", "")
    settings.OIDC_AUTO_CREATE_USERS = overrides.get("auto_create", True)
    settings.ALLOW_PASSWORD_LOGIN = overrides.get("allow_password", None)
    oidc_svc.clear_cache()


def start_flow(client, next_path=None):
    """Kick off a login and return (state, nonce) from the redirect."""
    url = "/api/auth/oidc/login" + (f"?next={next_path}" if next_path else "")
    r = client.get(url, follow_redirects=False)
    assert r.status_code == 303, (r.status_code, r.text)
    params = parse_qs(urlparse(r.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"], params
    assert params["client_id"] == [CLIENT_ID]
    return params["state"][0], params["nonce"][0]


def callback(client, state, id_token, code="stub-code"):
    global next_token_response
    next_token_response = {
        "access_token": "stub-access-token",
        "token_type": "Bearer",
        "id_token": id_token,
    }
    return client.get(
        f"/api/auth/oidc/callback?code={code}&state={state}",
        follow_redirects=False,
    )


def sso_error(response) -> str:
    assert response.status_code == 303, (response.status_code, response.text)
    location = response.headers["location"]
    assert location.startswith("/login?"), location
    return parse_qs(urlparse(location).query).get("sso_error", [""])[0]


def main() -> None:
    srv = start_provider()
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.database import SessionLocal
    from app.models.user import User
    from app.services import oidc as oidc_svc

    try:
        with TestClient(app_module()) as client:
            # ── Sign-in policy ────────────────────────────────────────────
            settings.OIDC_ENABLED = False
            settings.ALLOW_PASSWORD_LOGIN = None
            cfg = client.get("/api/auth/config").json()
            assert cfg == {"password_login_enabled": True, "oidc_enabled": False,
                           "oidc_provider_name": "SSO"}, cfg
            assert client.post("/api/auth/login",
                               json={"username": "admin", "password": "admin"}).status_code == 200
            print("✓ password login works and SSO is off by default")

            # Half-configured SSO must not be able to lock anyone out.
            settings.OIDC_ENABLED = True
            settings.OIDC_ISSUER = ISSUER
            settings.OIDC_CLIENT_ID = ""
            settings.OIDC_CLIENT_SECRET = ""
            cfg = client.get("/api/auth/config").json()
            assert cfg["oidc_enabled"] is False and cfg["password_login_enabled"] is True, cfg
            print("✓ an incomplete SSO config leaves password login alone")

            configure()
            cfg = client.get("/api/auth/config").json()
            assert cfg["oidc_enabled"] is True and cfg["password_login_enabled"] is False, cfg
            r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
            assert r.status_code == 403, (r.status_code, r.text)
            assert "ALLOW_PASSWORD_LOGIN" in r.json()["detail"]
            r2 = client.post("/api/auth/verify-2fa", json={"temp_token": "x", "code": "000000"})
            assert r2.status_code == 403, r2.status_code
            print("✓ enabling SSO disables password login, and says how to undo it")

            configure(allow_password=True)
            cfg = client.get("/api/auth/config").json()
            assert cfg["oidc_enabled"] is True and cfg["password_login_enabled"] is True, cfg
            assert client.post("/api/auth/login",
                               json={"username": "admin", "password": "admin"}).status_code == 200
            print("✓ ALLOW_PASSWORD_LOGIN=true re-enables password login alongside SSO")

            # ── The happy path ────────────────────────────────────────────
            configure()
            state, nonce = start_flow(client)
            r = callback(client, state, make_id_token(nonce=nonce))
            assert r.status_code == 303 and r.headers["location"] == "/", r.headers
            assert "refresh_token" in r.cookies, r.cookies
            sent = received["token_requests"][-1]
            assert sent["grant_type"] == "authorization_code"
            assert sent["code_verifier"], "PKCE verifier was not sent"
            print("✓ a full sign-in exchanges the code with PKCE and sets the refresh cookie")

            with SessionLocal() as db:
                user = db.query(User).filter(User.oidc_subject == "sso-user-1").first()
                assert user is not None and user.username == "alice", user
                assert user.oidc_issuer == ISSUER
                assert user.is_admin is False, "a later user must not inherit admin"
            print("✓ the SSO user was provisioned and linked by subject")

            # The refresh cookie alone is enough to get an access token, which
            # is why the token never has to travel in the redirect URL.
            me = client.post("/api/auth/refresh")
            assert me.status_code == 200, me.text
            body = me.json()
            assert body["user"]["username"] == "alice"
            assert body["user"]["is_sso_user"] is True, body["user"]
            assert "oidc_subject" not in body["user"], "the subject must not be exposed"
            print("✓ the SSO session refreshes, and reports is_sso_user without leaking the subject")
            client.cookies.clear()

            # ── Refusals ──────────────────────────────────────────────────
            state, nonce = start_flow(client)
            callback(client, state, make_id_token(nonce=nonce))
            replay = callback(client, state, make_id_token(nonce=nonce))
            assert "already been used" in sso_error(replay), sso_error(replay)
            print("✓ a replayed state is refused")
            client.cookies.clear()

            forged = callback(client, "never-issued", make_id_token(nonce="x"))
            assert "not one we issued" in sso_error(forged), sso_error(forged)
            print("✓ a state we never issued is refused")

            state, nonce = start_flow(client)
            wrong_nonce = callback(client, state, make_id_token(nonce="not-the-nonce"))
            assert "nonce" in sso_error(wrong_nonce).lower(), sso_error(wrong_nonce)
            print("✓ a mismatched nonce is refused")

            state, nonce = start_flow(client)
            bad_sig = callback(client, state, make_id_token(nonce=nonce, key=OTHER_PEM))
            assert "could not be verified" in sso_error(bad_sig), sso_error(bad_sig)
            print("✓ a token signed by an unpublished key is refused")

            state, nonce = start_flow(client)
            bad_aud = callback(client, state, make_id_token(nonce=nonce, audience="someone-else"))
            assert "could not be verified" in sso_error(bad_aud), sso_error(bad_aud)
            print("✓ a token issued for another audience is refused")

            state, nonce = start_flow(client)
            bad_iss = callback(client, state, make_id_token(nonce=nonce, issuer="https://evil.test"))
            assert "could not be verified" in sso_error(bad_iss), sso_error(bad_iss)
            print("✓ a token from another issuer is refused")

            # Algorithm confusion: an HMAC token whose "secret" is public key
            # material must never validate against a JWKS.
            state, nonce = start_flow(client)
            hs = make_id_token(nonce=nonce, key="a-shared-secret", algorithm="HS256")
            hs_error = sso_error(callback(client, state, hs))
            assert hs_error, "an HS256 token must not be accepted"
            print("✓ an HMAC-signed token is refused against a JWKS")

            state, nonce = start_flow(client)
            expired = callback(client, state, make_id_token(nonce=nonce, expires_in=-60))
            assert "could not be verified" in sso_error(expired), sso_error(expired)
            print("✓ an expired token is refused")

            # A state that timed out server-side.
            state, nonce = start_flow(client)
            with SessionLocal() as db:
                from app.models.user import OidcLoginState
                row = db.query(OidcLoginState).filter(OidcLoginState.state == state).first()
                row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                db.commit()
            stale = callback(client, state, make_id_token(nonce=nonce))
            assert "too long" in sso_error(stale), sso_error(stale)
            print("✓ a sign-in left too long is refused")

            # ── Provisioning rules ────────────────────────────────────────
            configure(auto_create=False)
            state, nonce = start_flow(client)
            r = callback(client, state, make_id_token(
                nonce=nonce, subject="brand-new",
                extra={"preferred_username": "nobody", "email": "nobody@example.com"}))
            assert "creation is disabled" in sso_error(r), sso_error(r)
            print("✓ auto-creation off means unknown users are turned away")

            # A username already tied to a different subject must never be
            # taken over, whatever the provider asserts.
            configure()
            state, nonce = start_flow(client)
            r = callback(client, state, make_id_token(
                nonce=nonce, subject="impostor",
                extra={"preferred_username": "alice", "email": "alice@example.com"}))
            assert "already linked to a different SSO identity" in sso_error(r), sso_error(r)
            print("✓ a claimed username cannot hijack an account linked to another subject")

            # An existing local account is adopted rather than duplicated.
            configure()
            with SessionLocal() as db:
                from app.services.auth import hash_password
                db.add(User(username="bob", hashed_password=hash_password("pw"),
                            is_active=True, is_admin=False))
                db.commit()
            state, nonce = start_flow(client)
            r = callback(client, state, make_id_token(
                nonce=nonce, subject="bob-sub",
                extra={"preferred_username": "bob", "email": "bob@example.com"}))
            assert r.status_code == 303 and r.headers["location"] == "/", r.headers
            with SessionLocal() as db:
                assert db.query(User).filter(User.username == "bob").count() == 1
                bob = db.query(User).filter(User.username == "bob").first()
                assert bob.oidc_subject == "bob-sub"
            print("✓ an existing local account is linked, not duplicated")
            client.cookies.clear()

            # Group mapping grants and revokes admin.
            configure(admin_group="libation-admins")
            state, nonce = start_flow(client)
            callback(client, state, make_id_token(
                nonce=nonce, subject="bob-sub", extra={"groups": ["libation-admins"]}))
            with SessionLocal() as db:
                assert db.query(User).filter(User.username == "bob").first().is_admin is True
            client.cookies.clear()

            state, nonce = start_flow(client)
            callback(client, state, make_id_token(
                nonce=nonce, subject="bob-sub", extra={"groups": ["other"]}))
            with SessionLocal() as db:
                assert db.query(User).filter(User.username == "bob").first().is_admin is False
            print("✓ the admin group claim both grants and revokes admin")
            client.cookies.clear()

            # ── Redirect safety ───────────────────────────────────────────
            assert oidc_svc.safe_next_path("/liberate") == "/liberate"
            for hostile in ("//evil.test", "https://evil.test", "evil", None, ""):
                assert oidc_svc.safe_next_path(hostile) is None, hostile
            configure()
            state, nonce = start_flow(client, next_path="/liberate")
            r = callback(client, state, make_id_token(nonce=nonce))
            assert r.headers["location"] == "/liberate", r.headers
            print("✓ next paths are honoured but cannot point off-site")
            client.cookies.clear()

            # ── Disabled provider ─────────────────────────────────────────
            settings.OIDC_ENABLED = False
            assert client.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404
            assert client.get("/api/auth/oidc/callback?code=a&state=b",
                              follow_redirects=False).status_code == 404
            print("✓ the SSO endpoints are absent when it is switched off")
    finally:
        srv.shutdown()

    assert_no_stray_dirs()
    print("\n✓ no stray top-level directories created")
    print("\nAll OIDC checks passed.")


def app_module():
    from app.main import app
    return app


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
