#!/usr/bin/env python3
"""End-to-end test of the Chaptarr integration, against a stub Chaptarr server.

Boots the FastAPI app against throwaway directories and points it at an
in-process HTTP server that speaks just enough of Chaptarr's v1 API to assert
what we send it: the ASIN lookup, the ManualImport payload (including the
provider ids and `selectionSource` that let an *unmonitored* book import), the
DownloadedBooksScan fallback for an ASIN Chaptarr's metadata server doesn't
know, path mapping, and that the API key never comes back over our own API.

It also covers the other direction — asking Chaptarr whether it already has a
book before downloading it from Audible: what counts as "already has it" under
each `skip_when` mode, that an eBook copy never suppresses the audiobook, that
`force` overrides the check, that the library index is fetched once and cached,
and that a broken Chaptarr fails open rather than blocking downloads.

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

# The app must confine itself to the directories its settings name. A hardcoded
# "/config" or "/data" merely succeeds on a root dev box while failing on an
# unprivileged host (CI), so record what exists up front and fail if the app
# creates any of them.
PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )

API_KEY = "test-api-key-123"
ASIN = "B002V0QUOC"
UNKNOWN_ASIN = "B0UNKNOWN1"

# Books the stub Chaptarr already holds, for the pre-download check.
OWNED_ASIN = "B00OWNED01"      # audiobook with a file on disk
TRACKED_ASIN = "B00TRACK01"    # audiobook in the library, no file yet
EBOOK_ASIN = "B00EBOOK01"      # only the eBook — the audiobook is still wanted

# Deliberately not filtered by mediaType: a stand-in for a Chaptarr build that
# ignores the query param, so the eBook assertion tests *our* guard, not theirs.
CHAPTARR_LIBRARY = [
    {
        "id": 11, "title": "The Owned One", "mediaType": "audiobook",
        "foreignBookId": f"az:{OWNED_ASIN}", "hasFiles": True,
        "statistics": {"bookFileCount": 1},
        "editions": [{"foreignEditionId": f"az:{OWNED_ASIN}", "asin": OWNED_ASIN}],
    },
    {
        "id": 12, "title": "The Tracked One", "mediaType": "audiobook",
        "foreignBookId": "az:B00OTHERID", "hasFiles": False,
        "statistics": {"bookFileCount": 0},
        # The ASIN we care about is only on the edition, not the work.
        "editions": [{"foreignEditionId": f"az:{TRACKED_ASIN}",
                      "audibleASIN": TRACKED_ASIN, "bookFileCount": 0}],
    },
    {
        "id": 13, "title": "The eBook One", "mediaType": "ebook",
        "foreignBookId": f"az:{EBOOK_ASIN}", "hasFiles": True,
        "statistics": {"bookFileCount": 1},
        "editions": [{"foreignEditionId": f"az:{EBOOK_ASIN}", "asin": EBOOK_ASIN}],
    },
    {
        # A Goodreads-sourced book: not an Audible id, must never match an ASIN.
        "id": 14, "title": "Someone Else's Edition", "mediaType": "audiobook",
        "foreignBookId": "gr:1234567890", "hasFiles": True,
        "statistics": {"bookFileCount": 1}, "editions": [],
    },
]

received = {"commands": [], "lookups": [], "auth": [], "library_fetches": []}
# Flipped by a test to make the stub report an unsuccessful command.
command_result = {"value": "successful"}
# Flipped by a test to make the library index unreadable.
library_broken = {"value": False}
# Flipped by a test to stand in for an older Chaptarr with no /book/paged route.
library_paged_missing = {"value": False}


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
        if u.path == "/api/v1/book" and library_paged_missing["value"]:
            received["library_fetches"].append("book?" + u.query)
            return self._json(200, CHAPTARR_LIBRARY)
        if u.path == "/api/v1/book/paged":
            if library_paged_missing["value"]:
                return self._json(404, {"error": "no such route"})
            received["library_fetches"].append(u.query)
            if library_broken["value"]:
                return self._json(500, {"error": "metadata server on fire"})
            q = parse_qs(u.query)
            offset = int(q.get("offset", ["0"])[0])
            size = int(q.get("pageSize", ["500"])[0])
            page = CHAPTARR_LIBRARY[offset:offset + size]
            return self._json(200, {"records": page, "totalCount": len(CHAPTARR_LIBRARY),
                                    "offset": offset, "pageSize": size})
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
    from app.services import chaptarr as chaptarr_svc
    from app.database import SessionLocal

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
    from app.services.logger import log_file_path
    assert Path(db_directory()) == WORKDIR / "data", db_directory()
    assert log_file_path().parent == CONFIG / "logs", log_file_path()
    assert_no_stray_dirs()
    print("✓ database and log paths follow the settings, not hardcoded /data or /config")

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
                            "skip_existing": False, "skip_when": "has_file",
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

        # ── Checking Chaptarr before downloading from Audible ─────────────────

        # The check endpoint reports what Chaptarr holds, whatever the setting.
        all_asins = [OWNED_ASIN, TRACKED_ASIN, EBOOK_ASIN, ASIN]
        r = client.post("/api/chaptarr/check", json={"book_ids": all_asins}, headers=h)
        assert r.status_code == 200, r.text
        by_id = {x["book_id"]: x for x in r.json()["results"]}
        assert by_id[OWNED_ASIN]["in_chaptarr"] and by_id[OWNED_ASIN]["has_file"]
        assert by_id[OWNED_ASIN]["title"] == "The Owned One", by_id[OWNED_ASIN]
        assert by_id[TRACKED_ASIN]["in_chaptarr"] and not by_id[TRACKED_ASIN]["has_file"]
        assert not by_id[EBOOK_ASIN]["in_chaptarr"], "an eBook copy is not an audiobook"
        assert not by_id[ASIN]["in_chaptarr"], "never downloaded into Chaptarr"
        assert r.json()["skip_existing"] is False, "reported, not enforced, while off"
        assert by_id[OWNED_ASIN]["would_skip"] and not by_id[TRACKED_ASIN]["would_skip"]
        print("✓ check reports what Chaptarr holds, ignoring its eBook rows")

        # One library fetch serves the whole batch, and the index is cached.
        fetches_before = len(received["library_fetches"])
        client.post("/api/chaptarr/check", json={"book_ids": all_asins}, headers=h)
        assert len(received["library_fetches"]) == fetches_before, \
            f"library re-fetched: {received['library_fetches'][fetches_before:]}"
        assert "mediaType=audiobook" in received["library_fetches"][0], \
            received["library_fetches"][0]
        assert "includeUnmonitored=true" in received["library_fetches"][0]
        print("✓ library index fetched once per batch and cached, audiobooks only")

        # While the check is off, a book Chaptarr owns still downloads.
        r = client.post("/api/downloads", headers=h,
                        json={"book_id": OWNED_ASIN, "book_title": "The Owned One"})
        assert r.status_code == 201, (r.status_code, r.text)
        print("✓ downloads are unaffected while the skip-check is off")

        # Turn it on: a book Chaptarr has a file for is refused with a reason.
        r = client.put("/api/chaptarr/settings", headers=h,
                       json={"skip_existing": True, "skip_when": "has_file"})
        assert r.status_code == 200 and r.json()["skip_existing"] is True, r.json()
        r = client.post("/api/downloads", headers=h,
                        json={"book_id": OWNED_ASIN, "book_title": "The Owned One"})
        assert r.status_code == 409, (r.status_code, r.text)
        detail = r.json()["detail"]
        assert detail["reason"] == "already_in_chaptarr", detail
        assert detail["chaptarr"]["title"] == "The Owned One", detail
        assert "The Owned One" in detail["message"], detail
        print("✓ a book Chaptarr already has a file for is not pulled from Audible")

        # The skip is recorded rather than silently swallowed…
        rows = [x for x in client.get("/api/chaptarr/imports", headers=h).json()
                if x["book_id"] == OWNED_ASIN and x["matched_by"] == "already_in_chaptarr"]
        assert len(rows) == 1 and rows[0]["status"] == "skipped", rows
        # …and skipping it again refreshes that row rather than adding another,
        # so a repeating auto-download sweep can't bury the history.
        client.post("/api/downloads", headers=h, json={"book_id": OWNED_ASIN})
        client.post("/api/downloads", headers=h, json={"book_id": OWNED_ASIN})
        rows = [x for x in client.get("/api/chaptarr/imports", headers=h).json()
                if x["book_id"] == OWNED_ASIN and x["matched_by"] == "already_in_chaptarr"]
        assert len(rows) == 1, rows
        print("✓ the skipped download shows up once in the Chaptarr history")

        # has_file mode leaves a book Chaptarr merely tracks alone.
        r = client.post("/api/downloads", headers=h, json={"book_id": TRACKED_ASIN})
        assert r.status_code == 201, (r.status_code, r.text)
        # in_library mode treats tracking it as enough.
        client.put("/api/chaptarr/settings", headers=h, json={"skip_when": "in_library"})
        r = client.post("/api/downloads", headers=h, json={"book_id": "B00TRACK02"})
        assert r.status_code == 201, "an unrelated book is still downloadable"
        r = client.post("/api/downloads", headers=h, json={"book_id": TRACKED_ASIN})
        assert r.status_code == 409, (r.status_code, r.text)
        assert r.json()["detail"]["reason"] == "already_in_chaptarr"
        print("✓ skip_when picks between 'has a file' and 'is in the library'")

        # An eBook in Chaptarr never suppresses the audiobook, even here.
        r = client.post("/api/downloads", headers=h, json={"book_id": EBOOK_ASIN})
        assert r.status_code == 201, (r.status_code, r.text)
        print("✓ owning the eBook never suppresses the audiobook download")

        # "Download anyway" overrides the check.
        client.put("/api/chaptarr/settings", headers=h, json={"skip_when": "has_file"})
        r = client.post("/api/downloads", headers=h,
                        json={"book_id": OWNED_ASIN, "force": True})
        assert r.status_code == 201, (r.status_code, r.text)
        print("✓ force downloads a book Chaptarr already has")

        # A broken Chaptarr must not block downloads.
        chaptarr_svc.invalidate_library_cache()
        library_broken["value"] = True
        try:
            r = client.post("/api/downloads", headers=h, json={"book_id": "B00OWNED02"})
            assert r.status_code == 201, (r.status_code, r.text)
            r = client.post("/api/chaptarr/check", json={"book_ids": [OWNED_ASIN]}, headers=h)
            assert r.status_code == 502, (r.status_code, r.text)
        finally:
            library_broken["value"] = False
            chaptarr_svc.invalidate_library_cache()
        print("✓ an unreachable Chaptarr fails open — the download still runs")

        # An older Chaptarr with no /book/paged still answers, via /book.
        chaptarr_svc.invalidate_library_cache()
        library_paged_missing["value"] = True
        try:
            r = client.post("/api/chaptarr/check", headers=h,
                            json={"book_ids": [OWNED_ASIN, EBOOK_ASIN]})
            assert r.status_code == 200, (r.status_code, r.text)
            got = {x["book_id"]: x["in_chaptarr"] for x in r.json()["results"]}
            assert got == {OWNED_ASIN: True, EBOOK_ASIN: False}, got
            assert received["library_fetches"][-1].startswith("book?"), \
                received["library_fetches"][-1]
        finally:
            library_paged_missing["value"] = False
            chaptarr_svc.invalidate_library_cache()
        print("✓ falls back to /book when Chaptarr has no paged route")

        # An unknown skip_when is refused rather than silently stored.
        r = client.put("/api/chaptarr/settings", headers=h, json={"skip_when": "whenever"})
        assert r.status_code == 422, (r.status_code, r.text)
        assert client.get("/api/chaptarr/settings", headers=h).json()["skip_when"] == "has_file"
        client.put("/api/chaptarr/settings", headers=h, json={"skip_existing": False})
        print("✓ an invalid skip_when is rejected")

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
        assert r.status_code == 200, r.status_code
        assert set(r.json()) == {"enabled", "configured", "skip_existing", "skip_when"}, r.json()
        assert "url" not in r.json() and "api_key" not in r.json(), r.json()
        assert r.json()["configured"] is False, "key was just cleared"
        print("✓ settings are admin-only; status and history are not")

    # The post-download hook stays quiet unless Chaptarr is enabled and configured.
    import asyncio

    # The bulk split the auto-download and "Download All" paths rely on.
    with SessionLocal() as db:
        cfg = chaptarr_svc.save_config(db, {"chaptarr_api_key": API_KEY,
                                            "chaptarr_skip_existing": True})
    chaptarr_svc.invalidate_library_cache()
    batch = [OWNED_ASIN, TRACKED_ASIN, EBOOK_ASIN, ASIN]
    wanted, skipped = asyncio.run(chaptarr_svc.filter_new_books(cfg, batch))
    assert wanted == [TRACKED_ASIN, EBOOK_ASIN, ASIN], wanted
    assert list(skipped) == [OWNED_ASIN], skipped
    assert "skipped downloading it from Audible" in chaptarr_svc.skip_reason(skipped[OWNED_ASIN])
    print("✓ bulk filter keeps only the books Chaptarr does not already have")

    # Same batch with Chaptarr unreachable: everything stays on the download list.
    chaptarr_svc.invalidate_library_cache()
    library_broken["value"] = True
    wanted, skipped = asyncio.run(chaptarr_svc.filter_new_books(cfg, batch))
    library_broken["value"] = False
    assert wanted == batch and not skipped, (wanted, skipped)
    print("✓ bulk filter fails open when Chaptarr cannot be read")

    with SessionLocal() as db:
        chaptarr_svc.save_config(db, {"chaptarr_api_key": "", "chaptarr_skip_existing": False})
    received["commands"].clear()
    asyncio.run(chaptarr_svc.import_after_download(ASIN, "Mistborn", 1))
    assert not received["commands"], "auto-import must not fire while the key is cleared"
    print("✓ auto-import hook no-ops when Chaptarr is not usable")

    srv.shutdown()
    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll Chaptarr integration checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
