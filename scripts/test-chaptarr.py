#!/usr/bin/env python3
"""End-to-end test of the Chaptarr integration, against a stub Chaptarr server.

Boots the FastAPI app against throwaway directories and points it at an
in-process HTTP server that speaks just enough of Chaptarr's v1 API to assert
what we send it: the ASIN lookup, the ManualImport payload (including the
provider ids and `selectionSource` that let an *unmonitored* book import), the
DownloadedBooksScan fallback for an ASIN Chaptarr's metadata server doesn't
know, path mapping, and that the API key never comes back over our own API.

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-chaptarr.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

WORKDIR = Path(tempfile.mkdtemp(prefix="chaptarr-test-"))
CONFIG = WORKDIR / "config"
BOOKS = WORKDIR / "audiobooks"
for d in (WORKDIR / "data", CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

# Must be set before `app.config` is imported.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{WORKDIR / 'data' / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "chaptarr-test-only-not-a-real-secret")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin")

API_KEY = "test-api-key-123"
ASIN = "B002V0QUOC"
UNKNOWN_ASIN = "B0UNKNOWN1"

received = {"commands": [], "lookups": [], "auth": []}
# Flipped by a test to make the stub report an unsuccessful command.
command_result = {"value": "successful"}


class StubChaptarr(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        received["auth"].append(self.headers.get("X-Api-Key"))
        u = urlparse(self.path)
        if u.path == "/api/v1/system/status":
            return self._json(200, {"appName": "Chaptarr", "version": "1.2.3"})
        if u.path == "/api/v1/rootfolder":
            return self._json(200, [{"id": 1, "path": "/audiobooks", "name": "Audiobooks"}])
        if u.path == "/api/v1/book/lookup":
            term = parse_qs(u.query).get("term", [""])[0]
            received["lookups"].append(term)
            if term == f"az:{ASIN}":
                return self._json(200, [{
                    "title": "Mistborn: The Final Empire",
                    "foreignBookId": "az:B002V0QUOC",
                    "author": {"authorName": "Brandon Sanderson", "foreignAuthorId": "az:B000APZ33E"},
                    "editions": [
                        {"foreignEditionId": "az:B0OTHEREDN", "asin": "B0OTHEREDN", "monitored": True},
                        {"foreignEditionId": "az:B002V0QUOC", "asin": "B002V0QUOC", "monitored": False},
                    ],
                }])
            return self._json(200, [])
        if u.path.startswith("/api/v1/command/"):
            return self._json(200, {"id": 42, "status": "completed",
                                    "result": command_result["value"],
                                    "message": "Imported 1 file"})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        received["auth"].append(self.headers.get("X-Api-Key"))
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if urlparse(self.path).path == "/api/v1/command":
            received["commands"].append(body)
            return self._json(201, {"id": 42, "name": body.get("name"), "status": "queued"})
        return self._json(404, {"error": "not found"})


def start_stub():
    srv = HTTPServer(("127.0.0.1", 0), StubChaptarr)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def main():
    srv, base = start_stub()

    from fastapi.testclient import TestClient
    from app.main import app

    book_file = BOOKS / "Brandon Sanderson" / "Mistborn [B002V0QUOC]" / "Mistborn [B002V0QUOC].m4b"
    book_file.parent.mkdir(parents=True, exist_ok=True)
    book_file.write_bytes(b"not really an m4b")
    unknown_file = BOOKS / "Someone" / f"Mystery [{UNKNOWN_ASIN}].m4b"
    unknown_file.parent.mkdir(parents=True, exist_ok=True)
    unknown_file.write_bytes(b"nope")

    # Libation's own file cache — the source of truth for "where did that land?"
    (CONFIG / "FileLocationsV2.json").write_text(json.dumps({
        "Dictionary": {
            ASIN: [
                {"Id": ASIN, "FileType": 1, "Path": str(book_file)},
                {"Id": ASIN, "FileType": 3, "Path": str(book_file.with_suffix(".pdf"))},
            ],
        }
    }))

    # The app must honour DATABASE_URL rather than reaching for a hardcoded
    # /data, which an unprivileged host (CI) cannot create.
    from app.database import db_directory
    assert Path(db_directory()) == WORKDIR / "data", db_directory()
    print("✓ app database stays inside DATABASE_URL, not a hardcoded /data")

    with TestClient(app) as client:
        r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        # Defaults
        r = client.get("/api/chaptarr/settings", headers=h)
        assert r.status_code == 200, r.text
        assert r.json() == {"enabled": False, "url": "", "import_mode": "auto",
                            "auto_import": False, "path_from": "", "path_to": "",
                            "api_key_set": False}, r.json()
        print("✓ settings default to off")

        # Not configured yet → import is refused with a useful message
        r = client.post("/api/chaptarr/import", json={"book_ids": [ASIN]}, headers=h)
        assert r.status_code == 409, (r.status_code, r.text)
        print("✓ import refused while unconfigured")

        # Configure
        r = client.put("/api/chaptarr/settings", headers=h, json={
            "enabled": True, "url": base + "/", "api_key": API_KEY,
            "auto_import": True, "import_mode": "auto",
        })
        assert r.status_code == 200, r.text
        assert r.json()["api_key_set"] is True
        assert "api_key" not in r.json(), "API key must never be returned"
        print("✓ settings saved, key withheld from response")

        # Test connection
        r = client.post("/api/chaptarr/test", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["version"] == "1.2.3", r.json()
        assert r.json()["root_folders"][0]["path"] == "/audiobooks"
        print("✓ connection test reports Chaptarr version and root folders")

        # Import a known ASIN
        r = client.post("/api/chaptarr/import", json={"book_ids": [ASIN]}, headers=h)
        assert r.status_code == 202, (r.status_code, r.text)
        assert r.json()[0]["status"] == "running"
        print("✓ import accepted, record opened as running")

        for _ in range(100):
            time.sleep(0.1)
            rows = client.get("/api/chaptarr/imports", headers=h).json()
            if rows and rows[0]["status"] != "running":
                break
        assert rows[0]["status"] == "complete", rows[0]
        assert rows[0]["matched_by"] == "asin", rows[0]
        assert rows[0]["command_id"] == 42, rows[0]
        print(f"✓ import completed: {rows[0]['message']}")

        assert received["lookups"] == [f"az:{ASIN}"], received["lookups"]
        cmd = received["commands"][0]
        assert cmd["name"] == "ManualImport", cmd
        assert cmd["importMode"] == "auto"
        assert len(cmd["files"]) == 1, "only the audio file, not the PDF"
        f = cmd["files"][0]
        assert f["path"] == str(book_file), f
        assert f["foreignBookId"] == "az:B002V0QUOC", f
        assert f["foreignAuthorId"] == "az:B000APZ33E", f
        assert f["foreignEditionId"] == "az:B002V0QUOC", "must prefer the edition matching our ASIN"
        assert f["foreignAuthorName"] == "Brandon Sanderson", f
        assert f["selectionSource"] == 1, "UserMetadataSuggestion unlocks unmonitored imports"
        assert set(received["auth"]) == {API_KEY}, received["auth"]
        print("✓ ManualImport payload carries the Audible metadata Chaptarr needs")

        # Unknown ASIN → folder-scan fallback (found via the books-dir glob, no cache entry)
        received["commands"].clear()
        r = client.post("/api/chaptarr/import", json={"book_ids": [UNKNOWN_ASIN]}, headers=h)
        assert r.status_code == 202, r.text
        for _ in range(100):
            time.sleep(0.1)
            rows = client.get("/api/chaptarr/imports", headers=h).json()
            row = next(x for x in rows if x["book_id"] == UNKNOWN_ASIN)
            if row["status"] != "running":
                break
        assert row["status"] == "complete", row
        assert row["matched_by"] == "folder_scan", row
        cmd = received["commands"][0]
        assert cmd["name"] == "DownloadedBooksScan", cmd
        assert cmd["path"] == str(unknown_file.parent), cmd
        assert cmd["importMode"] == "auto", cmd
        assert cmd["requireDefaultRootFolderForMissingAuthors"] is True, cmd
        print("✓ unknown ASIN falls back to DownloadedBooksScan on the containing folder")

        # A book with no file at all
        received["commands"].clear()
        r = client.post("/api/chaptarr/import", json={"book_ids": ["B0NOFILE00"]}, headers=h)
        assert r.status_code == 202
        for _ in range(50):
            time.sleep(0.1)
            row = next(x for x in client.get("/api/chaptarr/imports", headers=h).json()
                       if x["book_id"] == "B0NOFILE00")
            if row["status"] != "running":
                break
        assert row["status"] == "error" and "No downloaded audio file" in row["message"], row
        assert not received["commands"], "must not bother Chaptarr when there is no file"
        print("✓ missing file reported as an error without contacting Chaptarr")

        # An explicitly unsuccessful command is an error on our side
        for value in ("unknown", "unsuccessful"):
            command_result["value"] = value
            received["commands"].clear()
            client.post("/api/chaptarr/import", json={"book_ids": [ASIN]}, headers=h)
            for _ in range(100):
                time.sleep(0.1)
                row = client.get("/api/chaptarr/imports", headers=h).json()[0]
                if row["status"] != "running":
                    break
            expected = "complete" if value == "unknown" else "error"
            assert row["status"] == expected, (value, row)
        command_result["value"] = "successful"
        print("✓ result 'unknown' counts as success, 'unsuccessful' as an error")

        # Path mapping
        r = client.put("/api/chaptarr/settings", headers=h,
                       json={"path_from": str(BOOKS), "path_to": "/books"})
        assert r.status_code == 200 and r.json()["api_key_set"] is True, r.json()
        received["commands"].clear()
        client.post("/api/chaptarr/import", json={"book_ids": [ASIN]}, headers=h)
        for _ in range(100):
            time.sleep(0.1)
            if received["commands"]:
                break
        f = received["commands"][0]["files"][0]
        assert f["path"].startswith("/books/"), f["path"]
        assert f["path"] == "/books/Brandon Sanderson/Mistborn [B002V0QUOC]/Mistborn [B002V0QUOC].m4b", f
        print("✓ path mapping rewrites the prefix Chaptarr sees")

        # Clearing the key
        r = client.put("/api/chaptarr/settings", headers=h, json={"api_key": ""})
        assert r.json()["api_key_set"] is False, r.json()
        print("✓ empty api_key clears the stored key")

        # Non-admin cannot read settings, but can see availability and history
        client.post("/api/users", headers=h, json={"username": "bob", "password": "password123",
                                                  "is_admin": False})
        t2 = client.post("/api/auth/login", json={"username": "bob", "password": "password123"}).json()
        h2 = {"Authorization": f"Bearer {t2['access_token']}"}
        assert client.get("/api/chaptarr/settings", headers=h2).status_code == 403
        assert client.get("/api/chaptarr/imports", headers=h2).status_code == 200
        r = client.get("/api/chaptarr/status", headers=h2)
        assert r.status_code == 200 and set(r.json()) == {"enabled", "configured"}, r.json()
        assert r.json()["configured"] is False, "key was just cleared"
        print("✓ settings are admin-only; status and history are not")

    # The post-download hook stays quiet unless Chaptarr is enabled and configured.
    import asyncio
    from app.services import chaptarr as chaptarr_svc
    received["commands"].clear()
    asyncio.run(chaptarr_svc.import_after_download(ASIN, "Mistborn", 1))
    assert not received["commands"], "auto-import must not fire while the key is cleared"
    print("✓ auto-import hook no-ops when Chaptarr is not usable")

    srv.shutdown()
    print("\nAll Chaptarr integration checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
