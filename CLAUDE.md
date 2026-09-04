# Libation Web UI — CLAUDE.md

## Project overview
A Dockerized web application that wraps the LibationCli audiobook manager with a professional, mobile-responsive web UI. Built in phases.

## Architecture

### Single-container deployment
- **Backend**: Python 3.12 + FastAPI, served on port 8000
- **Frontend**: React 18 + Vite + Tailwind CSS, built to `/app/static` and served as static files by FastAPI
  - Only `/assets` (Vite's JS/CSS bundles) is mounted via `StaticFiles`. The catch-all `spa_fallback` route in `main.py` checks if the requested path exists as a file under `/app/static` first (serves it directly) before falling back to `index.html` — needed so root-level files in `frontend/public/` (favicons, logos, etc.) actually get served instead of silently returning the SPA shell
- **LibationCli**: Installed from the official `.deb` (`/usr/bin/libationcli`)
- **LibationBridge**: ASP.NET Core 10 sidecar on `localhost:8001`; references Libation DLLs at `/usr/lib/libation/` directly. Handles downloads (with real `StreamingProgressChanged` progress) and scans. Login still uses the `libationcli` PTY subprocess.

### Volume layout
| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `./data` | `/data` | App SQLite DB (`app.db`), session store |
| `./config` | `/config` | Libation config, `appsettings.json`, `LibationContext.db` |
| `./audiobooks` | `/audiobooks` | Downloaded audiobooks |

### Key paths
- Backend entry: `backend/app/main.py`
- Auth API: `backend/app/api/auth.py`
- Auth service: `backend/app/services/auth.py`
- Frontend entry: `frontend/src/main.tsx`
- Auth context: `frontend/src/context/AuthContext.tsx`
- Chaptarr service: `backend/app/services/chaptarr.py`
- Bridge source: `libation-bridge/Program.cs`
- Bridge project: `libation-bridge/LibationBridge.csproj`

## Auth system
- **Access token**: 15-min JWT in response body, stored in memory (React context)
- **Refresh token**: 60-day JWT in httpOnly cookie (`/api/auth` path), hashed in `sessions` table
- **2FA**: TOTP via `pyotp`, optional per user, toggled in Settings
- **Session persistence**: On page load, silently calls `/api/auth/refresh` using the cookie
- **Auto-refresh**: Timer in `AuthContext` refreshes access token 2 min before expiry

## OIDC single sign-on (`backend/app/services/oidc.py`, `backend/app/api/auth.py`)
- Authorization-code flow with PKCE. `begin_login()` stores `state`, `nonce` and the PKCE verifier in `oidc_login_states` (a table, not memory — the callback may land on a different worker), and `complete_login()` marks the row consumed **before** exchanging the code, so a replay loses the race
- ID tokens are verified against the provider's JWKS. `ALLOWED_ALGORITHMS` is asymmetric-only: accepting an HS\* algorithm while verifying against a JWKS would let a token forged with the public key as its HMAC secret validate
- Discovery and JWKS documents are cached 5 minutes (`clear_cache()` on settings change / `POST /api/auth/oidc/test`)
- **`settings.oidc_configured`** requires `OIDC_ENABLED` *and* issuer *and* client id *and* secret. **`settings.password_login_enabled`** is `ALLOW_PASSWORD_LOGIN` when set, else `not oidc_configured`. The two-part rule means a half-filled config can never disable password login and lock everyone out; `ALLOW_PASSWORD_LOGIN=true` is the documented escape hatch
- `POST /api/auth/login` and `/verify-2fa` return 403 (naming the env var) when password login is off
- Users are keyed on `users.oidc_subject`, scoped by `users.oidc_issuer`. First sign-in links an existing local account by email then username; an account already linked to a *different* subject is never taken over. `OIDC_AUTO_CREATE_USERS=false` restricts SSO to pre-existing accounts. SSO-created users get an unusable random password hash
- `OIDC_ADMIN_GROUP` both grants and revokes admin on each login; blank leaves admin managed in-app
- The callback sets the refresh cookie and 303s into the app — the access token never travels in a URL, because `AuthContext` already calls `/api/auth/refresh` on mount. `next` is restricted to same-origin paths (`safe_next_path`) so the callback is not an open redirect
- `GET /api/auth/config` is public (the login page needs it before anyone has credentials) and exposes only `{password_login_enabled, oidc_enabled, oidc_provider_name}` — never the issuer, client id or secret
- `UserResponse.is_sso_user` is a computed field; `oidc_subject` is read from the ORM object but `exclude=True` so it never serialises
- **Behind a reverse proxy set `OIDC_REDIRECT_URL`** — uvicorn is not started with `--proxy-headers`, so the derived callback URL would use the internal scheme and host
- Tests: `scripts/test-oidc.py` runs against an in-process stub provider with a real RSA JWKS. CI job `oidc` gates `merge`

### Auth API endpoints (`backend/app/api/auth.py`)
- `GET /api/auth/default-credentials` — returns `{"using_default_credentials": bool}`; compares logged-in user's username against `ADMIN_USERNAME` env var and verifies stored hash still matches `ADMIN_PASSWORD`. Used by Settings page to show the amber warning banner.
- `PATCH /api/auth/me` — free-form dict body; updates `audible_account_id` and/or `owner_name` on the logged-in user
- `POST /api/auth/change-username` — body: `{new_username, current_password}`; validates ≥3 chars, 409 on conflict; returns updated `UserResponse`
- `POST /api/auth/change-password` — body: `{current_password, new_password}`; revokes all sessions on success
- `GET /api/auth/sessions` / `DELETE /api/auth/sessions/{id}` / `DELETE /api/auth/sessions` — session management for the logged-in user

## Database backends & migrations
- **SQLite by default** (`sqlite:////data/app.db`); **PostgreSQL optional** via `DATABASE_URL`. `database.py::normalized_url()` rewrites `postgres://`, `postgresql://` and `postgresql+psycopg2://` to `postgresql+psycopg` (psycopg 3 is bundled); `engine_kwargs()` branches per dialect because `check_same_thread` is a sqlite3 argument that raises on psycopg
- **Alembic** replaced `Base.metadata.create_all` + the hand-rolled `_migrate_db`. `app/migrations.py::run_migrations()` handles three states: fresh database (run every revision), pre-Alembic database (run `legacy_migrations.migrate_pre_alembic`, stamp `0001`, then upgrade — tables are *adopted*, never rebuilt), and already-migrated (upgrade to head)
- Revisions live in `backend/alembic/versions/`: `0001_baseline` reproduces the pre-Potation schema exactly; `0002_potation_schema` adds the engine's own tables. `env.py` sets `render_as_batch` for SQLite only
- `alembic.ini` and `alembic/` are copied to `/app/` in the Dockerfile, beside `app/`, because `migrations.py` resolves them relative to the package parent
- `seed_system_settings()` uses `ON CONFLICT ... DO NOTHING` rather than SQLite's `INSERT OR IGNORE`, so it runs on both backends
- Timestamps on Potation tables are `DateTime(timezone=True)`; the older tables store tz-aware values in naive columns (which Postgres silently strips), so `api/downloads.py` re-attaches `timezone.utc` on read. Mixed convention until Phase D drops the old tables

## Potation — native Audible engine (in progress)
Replaces LibationCli, the `libation-bridge` C# sidecar, and the direct reads of Libation's SQLite. Phase A foundations are in; auth, library sync and reconciliation are next.
- `services/potation/creds.py` — Fernet encryption for stored Audible credentials. The key is `{LIBATION_CONFIG}/potation.key`, generated 0600 on first use, **not** derived from `SECRET_KEY` so rotating the JWT secret doesn't log out every Audible account. `try_decrypt_json()` returns `None` on a lost key so the account is flagged `needs_reauth` rather than taking startup down
- `models/potation.py` — `audible_accounts`, `books` (incl. multi-part parent/child), `book_files` (replaces `FileLocationsV2.json`, carries `part_index` so parts don't sort lexically), `audible_licenses` (voucher reuse + `drm_type`), `download_jobs` (state machine, classified `error_code`, `cancel_requested`), `download_quota` (daily-cap ledger), `reconciliation_runs`
- `books.liberated_override` is tri-state: NULL derives from `book_files`, 1/0 is an explicit user override
- `services/potation/marketplaces.py` — the frontend still posts LibationCli's marketplace *names* (`"germany"`, not `"de"`); this maps them to `audible` country codes. A wrong marketplace fails late, at device registration, behind a sign-in URL that looked fine
- `services/potation/auth.py` — two-step device registration. `begin_login()` builds the Amazon OAuth URL and stores the PKCE verifier (encrypted) + device serial on an `audible_login_states` row; `complete_login()` consumes the row **before** exchanging the code, so a double submit cannot register two devices (Amazon caps registrations). Replaces the `libationcli login-external` subprocess that was held alive in a module-level dict. `disconnect_account()` calls `deregister_device()` — the old `DELETE /api/accounts/{id}` never did, leaving a registered device behind on every removal
- `services/potation/client.py` — `Authenticator.to_dict()/from_dict()` is the serialisation boundary; the blob is Fernet-encrypted onto `audible_accounts.auth_blob`. `client_for()` re-saves after the block because the authenticator silently refreshes expired access tokens. A blob that will not decrypt sets `needs_reauth` and drops the account from `active_accounts()` rather than raising past the caller
- `services/potation/library.py` — library sync. A `MultiPartBook` parent has no downloadable content; parts become child rows ordered by Audible's `sort` key, which is what stops "Part 10" preceding "Part 2"
- `services/potation/license.py` — `POST content/{asin}/licenserequest`. `content_license.drm_type` is the number the whole plan turns on: `Adrm` (AAX/AAXC) and `Mpeg` are natively downloadable; `Widevine`/`PlayReady`/`FairPlay` need a CDM we do not have. Licenses are persisted so a retry does not buy another one — a Download license counts against Audible's daily allowance
- `scripts/potation-census.py` — `login` / `sync` / `census` subcommands to measure DRM exposure against a real account. Samples 25 titles by default; `--sample 0` sweeps everything and prompts first. Keeps its database and `potation.key` under a gitignored `.potation-census/` because both hold live Audible credentials
- Tests: `scripts/test-potation.py`, run as `PYTHONPATH=backend python scripts/test-potation.py`. Runs the database section on SQLite always, and on PostgreSQL when `POTATION_TEST_POSTGRES_URL` is set. CI job `potation` runs both and gates `merge`

## Database (SQLite at `/data/app.db`)
- `users`: id, username, hashed_password (bcrypt), totp_secret, totp_enabled, is_active, is_admin, permissions (JSON), download_cap (INTEGER), audible_account_id (TEXT), owner_name (TEXT), created_at
- `sessions`: id, user_id, refresh_token_hash (sha256), expires_at, created_at, last_used_at, ip_address, user_agent
- `downloads`: id, book_id, book_title, user_id, status, progress, started_at, completed_at, error_message, created_at
- `scans`: id, status, started_at, completed_at, books_added, output, error_message
- `audible_account_settings`: account_id (TEXT PK), added_by_user_id (INTEGER), auto_download (INTEGER DEFAULT 0) — created via `_migrate_db`; tracks which web UI user added each Audible account and whether auto-download is enabled
- `system_settings`: key (TEXT PK), value (TEXT DEFAULT '') — created via `_migrate_db`; holds `last_auto_download_at` (ISO timestamp of the last auto-download run, empty string when never run) and the `chaptarr_*` keys (see Chaptarr integration below)
- `chaptarr_imports`: id, book_id (ASIN), book_title, status (running/complete/error/skipped), matched_by (`asin`/`folder_scan`), command_id (Chaptarr's command id), file_path (as Chaptarr sees it), message, user_id, created_at, completed_at — created by `Base.metadata.create_all` from `models/chaptarr.py`

## Permissions system
- `DEFAULT_PERMISSIONS` in `models/user.py`: all flags `true` except `can_remove_downloads = false`
- Flags: `can_download`, `can_scan`, `can_manage_accounts`, `can_liberate`, `can_remove_downloads`
- Admins bypass all checks; non-admin users inherit `DEFAULT_PERMISSIONS` if their `permissions` column is NULL
- `PATCH /api/users/{id}/permissions` — admin-only, updates flags + `download_cap`
- `download_cap = null` means unlimited; positive integer = max downloads per 12-hour rolling window
- 12h window enforcement: `COUNT(downloads WHERE user_id=? AND created_at > NOW()-12h)`; 429 response includes `resets_at` ISO timestamp

## Liberate service
- `GET /api/liberate/books` — all books with status from `UserDefinedItem.BookStatus` (0=not_liberated, 1=liberated, 2=error) overlaid with active `downloads` table rows; accepts `account_id`, `search`, `filter_status`, `page`, `page_size` params; `filter_status` values: `all`, `downloaded`, `not_downloaded`, `in_progress`, `audible_plus` (IsAudiblePlus=1), `purchased` (IsAudiblePlus=0)
- `GET /api/liberate/book-ids` — returns all matching book IDs (no pagination) for Select All across pages; accepts same filter params including `purchased`
- `PATCH /api/liberate/books/{book_id}` — sets `UserDefinedItem.BookStatus` (1=liberated, 0=not liberated); INSERTs row if missing (provides all NOT NULL cols: BookStatus, IsFinished, Ratings, Tags)
- `GET /api/liberate/cap` — current cap accounting for logged-in user
- `POST /api/liberate/download-all` — fires `libationcli liberate` (no-args); only available when user has no cap. When the Chaptarr skip-check is active it runs `_run_liberate_checked()` instead — enumerate `not_liberated`, filter through Chaptarr, queue the rest one at a time — because the blanket CLI liberate decides for itself what to fetch and can't be filtered
- Individual downloads still go through `POST /api/downloads` with per-call cap enforcement

## Version endpoint (`backend/app/api/updates.py`)
- `GET /api/updates/version` — returns installed CLI version (parsed from `libationcli --version`); read-only, no GitHub polling
- The in-container self-update mechanism was removed because it is architecturally incompatible with LibationBridge: installing a new `.deb` replaces `/usr/lib/libation/*.dll` but leaves the bridge binary (compiled against the old DLL versions) unchanged, causing runtime `MissingMethodException` or container death on restart. To update LibationCLI, bump `LIBATION_VERSION` in the Dockerfile and rebuild the image.

## Entrypoint restart loop (`docker-entrypoint.sh`)
- Replaced `exec gosu ... uvicorn` with a `while true` loop so the container survives uvicorn crashes
- **Bridge bootstrap**: each iteration pre-seeds `/config/Libation/appsettings.json` with `{"LibationFiles":"/config"}` — Libation's startup bootstrap reads `{CWD}/Libation/appsettings.json` and `Program.cs` sets CWD to `/config`, so this tells it to use `/config` as its files dir (matching `libationcli --libationFiles /config`)
- **Bridge startup**: LibationBridge starts before uvicorn; `wait_for_bridge()` polls `GET /health` on `localhost:8001` up to 30×1s; fatal exit if it never responds
- Both `BRIDGE_PID` and `UVICORN_PID` tracked; bridge is killed when uvicorn exits and restarted on the next loop iteration
- Crash path: loop restarts both after 5s delay
- `SIGTERM` to container (e.g. `docker stop`) sets `SHOULD_EXIT=true`, kills both processes, exits loop cleanly

## Default credentials
Set via env vars `ADMIN_USERNAME` / `ADMIN_PASSWORD` (defaults: `admin` / `admin`).
Admin user is seeded on first startup if no users exist. `_seed_admin` uses raw SQL via `conn.execute()` (same as `_migrate_db`) rather than ORM — avoids the issue where `_migrate_db` calling `db.connection()` leaves a dangling DBAPI transaction that causes subsequent `db.add(User(...))` commits to silently not persist.

`GET /api/auth/default-credentials` detects whether the logged-in user is still on factory defaults by comparing their username/password against the env vars at runtime. When `using_default_credentials` is `true`, SettingsPage shows an amber warning banner and surfaces `UpdateCredentialsSection` — a single form that changes username + password together and signs the user out immediately after.

## Development
```bash
# Backend only
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend only (proxies /api to localhost:8000)
cd frontend && npm install && npm run dev

# Full stack via Docker
docker compose up --build
```

## Library service (`backend/app/services/libation.py`)
- Reads Libation's `LibationContext.db` at `{LIBATION_CONFIG}/LibationContext.db`
- Uses schema discovery (`PRAGMA table_info`) so it handles column name variations across Libation versions
- Returns `empty_reason: "no_accounts"` when no DB exists (user hasn't connected Audible yet)
- Authors/narrators via `BookContributors` + `Contributors`/`Persons` junction (contributor type 0=author, 1=narrator)
- Series via `BookSeries` + `Series` junction
- Cover paths stored in `PictureLarge` column; served via `GET /api/library/covers/{book_id}` (no auth required — images are not sensitive)

## CLI service (`backend/app/services/cli.py`)
- **Downloads and scans** route through LibationBridge HTTP (`BRIDGE_URL = http://localhost:8001`); login (`start_login` / `complete_login`) stays as a PTY subprocess because `libationcli login-external` requires a TTY
- `list_accounts()` → bridge `GET /accounts` (shim over `libationcli list-accounts --bare`; returns parsed tab-separated: account_id, name, locale, scan_library, authenticated)
- `run_liberate(book_ids, on_progress)` → bridge `POST /download/{asin}` (202), then polls `GET /progress/{asin}` every 2s; calls `on_progress(pct, output)` on each change; returns when status is `complete` or `error`
- `run_scan(on_line)` → bridge `POST /scan` (synchronous; 600s timeout); fires `on_line` callbacks by iterating the returned output string
- `login-external` subprocess is kept alive in `_PENDING_LOGINS` dict (keyed by UUID) between the two login steps; auto-expires after 10 min
- `ephemeralSettings: true` in LibationCli means all in-memory config changes (including Serilog sinks) are never persisted to `Settings.json`. The `/config/Logs/` directory is always empty at rest; stack traces only appear on stderr.

## LibationBridge sidecar (`libation-bridge/`)
- ASP.NET Core 10 minimal API on `localhost:8001`; self-contained single-file binary at `/usr/lib/libation/libation-bridge` (symlinked to `/usr/local/bin/libation-bridge`)
- References Libation DLLs at `/usr/lib/libation/` via `<Reference>` with `<Private>false</Private>` — DLLs are not bundled into the binary; loaded at runtime via `AssemblyResolve` hook
- `AssemblyResolve` hook registered before any Libation type is touched; all Libation code in `static class LibationBridgeApp` with `[MethodImpl(MethodImplOptions.NoInlining)]` to prevent JIT resolving DLLs before the hook fires
- Libation scaffolding called at startup: `RunPreConfigMigrations()` → `RunPostConfigMigrations()` → `RunPostMigrationScaffolding(Variety.Chardonnay, config)`; `Directory.SetCurrentDirectory("/config")` set first so bootstrap discovery resolves `{CWD}/Libation/appsettings.json` → `/config/Libation/appsettings.json`
- **Bridge API surface**:
  - `GET /health` — readiness probe (`{"status":"ok"}`)
  - `GET /debug` — diagnostic: DB path + book count + sample ASINs
  - `GET /accounts` — shim over `libationcli list-accounts --bare --libationFiles /config`
  - `POST /scan` — synchronous: runs `libationcli scan --libationFiles /config`, awaits exit, returns `{"exit_code","output"}`; Kestrel keepalive set to 12 min
  - `POST /download/{asin}` — 202 immediately; starts `DownloadDecryptBook.Create(config).ProcessAsync(book)` in background Task; `StreamingProgressChanged` handler updates in-memory `_progress[asin].Progress` (real 0–100%)
  - `GET /progress/{asin}` — returns `{"asin","progress","status","output"}` or 404
  - `POST /download-all` — 202 immediately; fires `libationcli liberate --force --libationFiles /config` in background
- Completed progress entries expire after 1 hour via background cleanup Task
- **Dockerfile**: `bridge-builder` stage (between frontend-builder and runtime) installs Libation `.deb` so MSBuild resolves `<HintPath>/usr/lib/libation/*.dll>` at compile time; builds with `dotnet publish -r linux-x64 --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=false`; binary copied to runtime image at `/usr/lib/libation/libation-bridge`

## Docker / LibationCli quirks
- `libicu76` must be installed in the image. LibationCli uses .NET 10 which does NOT bundle its own ICU. Without ICU, `CultureInfo.GetCultures()` returns only the Invariant Culture (ID 0x7F), causing `new RegionInfo(c)` to throw `System.ArgumentException: There is no region associated with the Invariant Culture` inside `LocaleDto.GetRegion()` → called from `DownloadOptions..ctor` (line 82) → crash surfaces as "Error processing book. Skipping." with no file written. Never set `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1`.
- InProgress directories land in `/tmp/Libation-{username}` (WinTemp default). Both `/tmp/Libation-root/` and `/tmp/Libation-libation/` may exist depending on which user ran the CLI.
- `DownloadDecryptBook.ProcessAsync` fires `OnCompleted` in a `finally` block, so "DownloadDecryptBook Completed" always appears in output even when an exception propagated — "Error processing book" follows immediately after from the outer `catch`.

## Accounts (`backend/app/api/accounts.py`)
- `GET /api/accounts` — lists Audible accounts from bridge, enriched with `owner_name`, `owner_username` (from `users` table), `auto_download`, and `added_by_user_id` (from `audible_account_settings`)
- `POST /api/accounts/login/start` / `POST /api/accounts/login/complete` — OAuth login flow via PTY subprocess; on `complete`, inserts a row into `audible_account_settings` marking which web UI user added the account
- `PATCH /api/accounts/{account_id}/auto-download` — body: `{auto_download: bool}`; updates `audible_account_settings.auto_download`; only callable by admin or the user who added that account (`added_by_user_id`)
- `DELETE /api/accounts/{account_id}` — removes account from `AccountsSettings.json`
- **Auto-scan after OAuth**: `AccountsPage.tsx` fires `POST /api/downloads/scan` silently in the background immediately after `login/complete` succeeds, then shows an info banner linking to `/liberate`
- **Owner name input**: when the logged-in user added an account (`added_by_user_id === user.id`) but has no `owner_name` set, an editable inline input appears in the Owner column (placeholder: "Fill in your first name"); saves via `PATCH /api/auth/me` on blur/Enter
- **Amber banner**: shown at top of AccountsPage when the current user has added at least one account but has no `owner_name`; text: "Fill in owner name to use split libraries."

## Downloads & Scan (`backend/app/api/downloads.py`)
- `POST /api/downloads/scan` creates a `Scan` row, fires `asyncio.create_task` to call `cli.run_scan()` → bridge `POST /scan`
- `POST /api/downloads` creates a `Download` row with `user_id`, fires task to call `cli.run_liberate(asin)` → bridge `POST /download/{asin}` + poll `GET /progress/{asin}`
- Background tasks update DB rows as progress changes; frontend polls `/api/downloads` every 2s
- Duplicate active downloads blocked with 409
- **Chaptarr pre-check**: after the duplicate check, `POST /api/downloads` calls `chaptarr.filter_new_books()` unless `force` is set; a hit returns 409 with `detail = {message, reason: "already_in_chaptarr", chaptarr: {...}}`. A no-op (no HTTP at all) unless `skip_check_active`
- **Auto-download after scan** (`_auto_download_if_enabled`): called via `asyncio.create_task` after every successful scan. Reads `audible_account_settings` for accounts with `auto_download=1`; enforces a 30-minute global cooldown via `system_settings.last_auto_download_at`; for each opted-in account fetches `not_liberated` book IDs, runs them through `chaptarr.filter_new_books()`, and queues the rest as individual downloads under the admin user, skipping any already active.

## Chaptarr integration (`backend/app/services/chaptarr.py`, `backend/app/api/chaptarr.py`)
Pushes downloaded audiobooks into a self-hosted [Chaptarr](https://github.com/Chaptarr/chaptarr) (a Readarr fork for audiobook/eBook libraries) so they land in its library **even when nothing is monitoring for them**.

### Matching
Chaptarr's canonical provider prefix for Amazon/Audible ids is `az:`, and every Libation book is keyed by its Audible ASIN, so no fuzzy matching is needed:
- `GET /api/v1/book/lookup?term=az:{ASIN}&mediaType=audiobook` → `BookResource` with `foreignBookId`, `author.foreignAuthorId`, `editions[].foreignEditionId`
- `_pick_edition_id` prefers the edition whose ASIN equals the one we downloaded, then the monitored one, then the first
- A hit without `foreignAuthorId` counts as a miss — `ManualImport` cannot work without it

### Import
`POST /api/v1/command` with:
```json
{"name": "ManualImport", "importMode": "auto", "replaceExistingFiles": false,
 "files": [{"path": "...", "foreignAuthorId": "az:...", "foreignBookId": "az:...",
            "foreignEditionId": "az:...", "selectionSource": 1}]}
```
`selectionSource: 1` is `ManualImportSelectionSource.UserMetadataSuggestion`. It routes the request through Chaptarr's `MaterializeUserSelectedEditionAsync` / `AddAuthorAsync` path, which creates the author, book and edition from provider metadata instead of requiring an existing library entry — **this is what makes an unmonitored book importable**.

**Fallback**: when Chaptarr's metadata server doesn't know the ASIN, the containing folder is sent to `DownloadedBooksScan` with `requireDefaultRootFolderForMissingAuthors: true`, letting Chaptarr match from tags/filenames and still create missing authors.

Commands are polled via `GET /api/v1/command/{id}` every 2s for up to 5 minutes. Auth is the `X-Api-Key` header. `_summarize()` maps Chaptarr's `CommandStatus`/`CommandResult` onto our status: `Result` stays `Unknown` until a handler says otherwise and `Complete()` promotes it to `Successful`, so only an explicit `Unsuccessful` is treated as failure. **Caveat**: `ManualImport` never reports `Unsuccessful` — it completes even when every file was rejected — so a `complete` row means Chaptarr *ran* the import, not that it accepted the file. Chaptarr's own History view has the per-file detail.

### Finding the downloaded file
`libation.get_audio_file_paths(book_id)` reads Libation's `FileLocationsV2.json` (`LibationFileManager.FilePathCache`) at `{LIBATION_CONFIG}/FileLocationsV2.json`. Shape: `{"Dictionary": {"<ASIN>": [{"Id","FileType","Path"}]}}`, where `FileType` is the `LibationFileManager.FileType` ordinal (`Unknown=0, Audio=1, AAXC=2, PDF=3, Zip=4, Cue=5`) — non-audio entries are dropped. Falls back to globbing `AUDIOBOOKS_DIR` for `*{ASIN}*` when the cache has no usable entry (Libation's default file template embeds the ASIN).

### Pre-download check ("does Chaptarr already have it?")
The traffic runs both ways: with `chaptarr_skip_existing` on, a book Chaptarr already holds is never pulled from Audible again.

- `fetch_library()` pages `GET /api/v1/book/paged?offset=&pageSize=500&includeUnmonitored=true&mediaType=audiobook`, falling back to `GET /api/v1/book?mediaType=audiobook` when a Chaptarr build has no paged route. Both return `BookResource`
- `_asins_of()` folds every Audible id on a record into one ASIN index — `asin`, `audibleASIN`, the `az:`-prefixed `foreignBookId`/`foreignEditionId`, and the same fields plus `asins[]` on each edition. `_strip_provider_prefix` drops non-`az:` providers (`gr:`, `ol:`, `gb:`) so a Goodreads id can never match an ASIN; a 10-char-alnum guard keeps slugs and row ids out of the index
- `mediaType=audiobook` matters — Chaptarr keeps **separate audiobook and eBook rows**, and `_index_records` also drops eBook records client-side, so owning the eBook never suppresses the audiobook
- `_LIBRARY_CACHE` (60 s TTL, keyed by `base_url`) makes a bulk check one round trip; invalidated on a successful import and on any settings PUT
- `chaptarr_skip_when`: `has_file` (default — Chaptarr has a file on disk, via `hasFiles` / `statistics.bookFileCount` / any edition's `bookFileCount`) or `in_library` (the book exists at all)
- **Fails open.** `filter_new_books()` swallows every error and returns the full download list — a metadata server being down must never cost an audiobook. `check_books()` is the raising variant, used by the API endpoint
- Hooked into all three download paths: `POST /api/downloads` (409 `{"reason": "already_in_chaptarr"}`, overridable with `force: true`), `_auto_download_if_enabled()` after a scan, and `POST /api/liberate/download-all` — which switches from the blanket `libationcli liberate` to `_run_liberate_checked()` (enumerate → filter → queue individually) whenever the check is active, since the CLI's own liberate can't be filtered
- Skips are recorded on `chaptarr_imports` with `status="skipped"`, `matched_by="already_in_chaptarr"`. `record_skipped_download()` refreshes the existing row per book rather than inserting one per sweep

### Settings (`system_settings` keys)
`chaptarr_enabled`, `chaptarr_url`, `chaptarr_api_key`, `chaptarr_import_mode` (`auto`/`copy`/`move`), `chaptarr_auto_import`, `chaptarr_path_from`, `chaptarr_path_to`, `chaptarr_skip_existing`, `chaptarr_skip_when` (`has_file`/`in_library`). Seeded by `_migrate_db` from `SETTING_KEYS`. `path_from` → `path_to` rewrites the path prefix when the shared volume is mounted differently in the two containers. `import_mode` only matters when the file sits *outside* a Chaptarr root folder; inside one, Chaptarr treats it as an existing file and links it in place.

### API endpoints
- `GET/PUT /api/chaptarr/settings` — admin-only; the API key is never returned (`api_key_set: bool` instead). On PUT, an omitted `api_key` keeps the stored value, `""` clears it
- `GET /api/chaptarr/status` — any authenticated user; `{enabled, configured, skip_existing, skip_when}` only, so the Liberate page can decide whether to offer the action without exposing the URL or key. `skip_existing` here is `cfg.skip_check_active` (enabled **and** configured **and** the setting on), not the raw flag
- `POST /api/chaptarr/test` — admin-only; returns Chaptarr's app name, version and root folders
- `POST /api/chaptarr/import` — body `{book_ids: [...]}`; requires `can_download`; 202 with one `running` record per book, batch worked sequentially in the background
- `POST /api/chaptarr/check` — body `{book_ids: [...]}` (max 1000); returns `{skip_existing, skip_when, results: [{book_id, in_chaptarr, has_file, title, chaptarr_book_id, would_skip}]}`. Any authenticated user; answers regardless of whether skipping is switched on
- `GET /api/chaptarr/imports` — recent attempts and skips (default 50)

### Hook
`downloads.py::_run_download` fires `chaptarr.import_after_download()` after a successful download. It no-ops unless `enabled` *and* `auto_import` *and* `configured`. All failures are recorded on the `chaptarr_imports` row — a bad Chaptarr config can never break a download.

### UI
- `frontend/src/components/settings/ChaptarrSection.tsx` — admin-only Settings card: enable toggle, URL, API key (write-only), auto-import toggle, import mode, **Skip books Chaptarr already has** toggle + `skip_when` selector, path mapping, Test connection, and a Recent activity list (imports *and* skipped downloads) that polls every 3s while anything is running
- Liberate page Multi Select mode gains **Send to Chaptarr** and **Check Chaptarr** bulk actions, shown only when `GET /api/chaptarr/status` says it's usable. `Check Chaptarr` badges the affected tiles ("In Chaptarr" / "Tracked") without changing anything
- A download refused by the skip-check surfaces the reason in the page notice with a **Download anyway** action that re-posts with `force: true`; `chaptarrSkipOf()` is the single typed reader for that 409 body

### Test
`scripts/test-chaptarr.py` runs the whole flow against an in-process stub Chaptarr — no test framework, only `backend/requirements.txt`. Run with `PYTHONPATH=backend python scripts/test-chaptarr.py`. Wired into `.github/workflows/docker-ghcr.yml` as the `chaptarr` job, which gates `merge`. Covers both directions: the import payloads and fallbacks, and the pre-download check (each `skip_when` mode, the eBook exclusion, `force`, one-fetch caching, the `/book` fallback route, fail-open on an unreachable Chaptarr, and that a repeated skip refreshes its row instead of adding one).

## User management (`backend/app/api/users.py`)
- Admin-only routes behind `require_admin` dependency
- `GET /api/users`, `POST /api/users`, `PATCH /api/users/{id}`, `DELETE /api/users/{id}`
- Cannot delete own account, cannot revoke own admin status
- `is_admin` column added via startup migration (`_migrate_db`) using `ALTER TABLE` + `PRAGMA table_info`

## Settings & Stats (`backend/app/api/settings.py`)
- `GET/PUT /api/settings/libation` — reads/writes `/config/appsettings.json` (resilient: merges only known keys)
- `GET /api/settings/stats` — total_books (LibationContext.db), total_downloads (our DB), accounts_count (bridge `/accounts`), downloads_per_user (JOIN)
- Field map: Python snake_case ↔ Libation PascalCase key names

## Logs API (`backend/app/api/logs.py`)
- `GET /api/logs?lines=200&level=all` — admin-only; reads `/config/logs/libation-web.log`, filters lines by `[LEVEL]` substring match, returns `{"lines": [...], "total": int, "truncated": bool}`; max 2000 lines per request
- `GET /api/logs/download` — admin-only; serves the full log file as `text/plain` download (`libation-web.log`)
- **LogsSection in Settings**: dark monospace terminal viewer (h-96), level filter tabs (ALL / INFO / WARN / ERROR / DEBUG), line count selector (100/200/500/1000), manual Refresh button, Auto-refresh toggle (polls every 5s), Download button; admin-only, shown at the bottom of SettingsPage
- **ApiDocsSection in Settings**: two links to FastAPI's built-in `/docs` (Swagger UI) and `/redoc`; admin-only, below LogsSection

## Logging (`backend/app/services/logger.py`)
- Writes to `{LIBATION_CONFIG}/logs/libation-web.log` — `/config/logs/libation-web.log` in the container (on the mapped `/config` volume, so it survives restarts). `log_file_path()` is the single source of truth; `api/logs.py` reads it rather than naming the path again
- `RotatingFileHandler`: 5 MB per file, 3 backups (`libation-web.log`, `.1`, `.2`, `.3`)
- Log format: `YYYY-MM-DD HH:MM:SS [LEVEL] message`
- Logged events:
  - **Startup**: server starting, stuck downloads/scans reset, ready
  - **list-accounts**: bridge `/accounts` call + duration
  - **Login**: start (email, locale), URL generated, completion success/failure
  - **Scan**: start, bridge `/scan` output, exit code, duration
  - **Liberate**: book IDs (or "all"), bridge `/download/{asin}` progress polling, final status
- OAuth URLs and response URLs are intentionally NOT logged (contain auth tokens)
- On Unraid: readable at `/mnt/user/appdata/libation/config/logs/libation-web.log`

## Rate limiting
- `slowapi` on `/api/auth/login` (20/min) and `/api/auth/verify-2fa` (10/min)
- Limiter instance in `backend/app/limiter.py` (separate to avoid circular imports)

## Dark mode
- `tailwind.config.js` has `darkMode: "class"` — `dark` class applied to `<html>` element
- `ThemeContext.tsx` persists choice to `localStorage`, toggles `<html class="dark">`
- Toggle button in sidebar (Moon/Sun icon)
- Dark mode variants added to: Layout, Sidebar, Card, Input components, and book grid pages

## PUID/PGID (Unraid support)
- `docker-entrypoint.sh` creates/modifies `libation` user/group at runtime using env vars PUID/PGID
- Uses `gosu` to drop privileges before exec'ing uvicorn
- Defaults to PUID=1000, PGID=1000; runs as root if PUID=0
- `unraid-template.xml` — Unraid Community Applications template (PUID=99, PGID=100 defaults for Unraid)

## Health check
- `GET /api/health` — public endpoint returning `{"status": "ok", "version": "0.4.0"}`
- Dockerfile HEALTHCHECK uses `/api/health` instead of `/api/auth/me`

## Phase history
- **Phase 1** (complete): Project foundation, Docker setup, full auth system (login, 2FA, 60-day sessions, change password), React UI shell with sidebar navigation.
- **Phase 2** (complete): Library view — reads Libation SQLite DB, grid/list book view with cover art, search, sort, pagination, book detail slide-over, empty states.
- **Phase 3** (complete): Accounts & Downloads — add Audible accounts via `login-external` OAuth flow, library scan, per-book downloads via `liberate`, downloads page with progress polling, download button on book cards.
- **Phase 4** (complete): Settings & Polish — dashboard stat cards, Libation settings passthrough, multiple user management (admin CRUD), session management (list/revoke), dark mode toggle, PUID/PGID support, Unraid CA template, rate limiting on auth endpoints, improved health check.
- **Phase 5** (complete): Liberate view, My Books, per-user permissions, and download caps. New `/liberate` page shows all books with status overlays (green ✓ downloaded, red ✕ not downloaded, animated spinner for in-progress) and filter tabs. New `/my-books` page filters books by the user's linked Audible account. Per-user permission flags (`can_download`, `can_scan`, `can_manage_accounts`, `can_liberate`, `can_remove_downloads`) stored as JSON on users row; admin toggle matrix in Settings. 12-hour rolling window download cap: uncapped users get "Download All" (fires `libationcli liberate`), capped users get "Download Next N" auto-selecting books; cap enforced on both individual and bulk downloads (429 with `resets_at`). Enhanced book metadata via `UserDefinedItem` JOIN (BookStatus, Subtitle, ContentType, Language, IsAbridged, community ratings).
- **Phase 5 bug fix** (complete): Root cause of "Error processing book. Skipping." identified and fixed. `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` broke `CultureInfo.GetCultures()`, causing `RegionInfo` crash in `LocaleDto.GetRegion()` during every download attempt. Fix: removed the env var, added `libicu76` to Dockerfile apt-get install.
- **Phase 6** (complete, then partially removed): CLI self-update was built — entrypoint restart loop, GitHub Releases API polling, `.deb` download+install, rollback. Subsequently removed because it is architecturally incompatible with LibationBridge: `dpkg -i` replaces `/usr/lib/libation/*.dll` but the bridge binary (compiled against the old DLL versions) is not rebuilt, causing `MissingMethodException` or container death on the next restart. The in-container update mechanism is replaced by: bump `LIBATION_VERSION` in the Dockerfile and rebuild the image. A read-only About card in Settings still shows the installed CLI version via `GET /api/updates/version`.
- **Phase 5 Extended** (complete): User Management gains inline `owner_name` field and Audible Account dropdown (sets `users.audible_account_id`). Liberate page gains owner filter tabs, centered search bar (300ms debounce), per-book Mark Downloaded/Not Downloaded (`PATCH /api/liberate/books/{book_id}`), Multi Select mode with Select All spanning all pages (via `GET /api/liberate/book-ids`) plus bulk mark actions, and per-page selector [24/48/96/200]. Accounts page shows post-login "go to Downloads → Scan Library" info banner. Liberate moved to top of sidebar and set as default view (`/` redirects to `/liberate`). Library and My Books removed from sidebar nav and routes entirely (pages still exist in codebase but are not linked).
- **Phase 7 seed fix** (complete): `_seed_admin` in `main.py` rewritten to use raw SQL (`conn.execute()`) instead of ORM (`db.add(User(...))`). Root cause: `_migrate_db` calls `db.connection()` which acquires a DBAPI connection and begins a transaction; if no migrations run, no `db.commit()` is called, leaving the session with a dangling connection. The subsequent ORM `db.commit()` in the old `_seed_admin` did not reliably persist the row. The raw SQL approach shares the same connection path as `_migrate_db` and works correctly. Also added try/except with explicit logger.error logging and flush=True on print so failures are never silent.
- **Phase 7** (complete): LibationBridge ASP.NET Core 10 sidecar replaces subprocess calls for downloads and scans. New `libation-bridge/` directory with `LibationBridge.csproj` and `Program.cs`. Bridge references Libation DLLs at `/usr/lib/libation/` directly via `AssemblyResolve` hook + `[MethodImpl(NoInlining)]` isolation. Real `StreamingProgressChanged` events (0–100%) replace fake 5/95 progress jumps from stdout parsing. Dockerfile gains a `bridge-builder` stage (Stage 2) that installs the Libation `.deb` for compile-time DLL resolution then publishes a self-contained single-file binary. Entrypoint pre-seeds `/config/Libation/appsettings.json` with `{"LibationFiles":"/config"}` so the bridge's Libation scaffolding uses the same config path as `libationcli`. `cli.py` rewritten to route downloads and scans through bridge HTTP; login stays PTY subprocess. `BRIDGE_URL` added to `config.py`.
- **Phase 8** (complete): Operational hardening — auto-download, default-credentials UX, log viewer, and sidebar polish. Per-Audible-account auto-download toggle stored in new `audible_account_settings` table; `_auto_download_if_enabled()` fires after every successful scan with a 30-min global cooldown via `system_settings`. OAuth flow auto-triggers a library scan and shows a dismissable info banner on completion. `GET /api/auth/default-credentials` detects factory-default credentials; SettingsPage shows amber warning banner + `UpdateCredentialsSection` (change username + password in one step, then signs out). `POST /api/auth/change-username` added. Logs API (`GET /api/logs`, `GET /api/logs/download`) + `LogsSection` embedded in Settings (level filter, line count, auto-refresh, download). `ApiDocsSection` in Settings links to `/docs` and `/redoc`. Sidebar nav renamed "Accounts" → "Audible Accounts". `UserAdminResponse.created_at` made Optional to handle NULL rows from early-seeded users. `AccountResponse` gains `auto_download` and `added_by_user_id` fields.
- **Phase 9** (complete): Chaptarr import. Downloaded audiobooks are pushed into a self-hosted Chaptarr library, matched by Audible ASIN via Chaptarr's `az:` provider prefix. New `services/chaptarr.py`, `api/chaptarr.py`, `models/chaptarr.py`, `schemas/chaptarr.py`; new `chaptarr_imports` table and `chaptarr_*` `system_settings` keys. Uses Chaptarr's `ManualImport` command with `selectionSource: 1` (UserMetadataSuggestion) so books import even when Chaptarr isn't monitoring for them, falling back to `DownloadedBooksScan` when the ASIN is unknown upstream. `libation.get_audio_file_paths()` reads Libation's `FileLocationsV2.json` to find what was actually written. Auto-import fires after every successful download; a **Send to Chaptarr** bulk action on the Liberate page covers on-demand pushes. `scripts/test-chaptarr.py` exercises the flow against a stub Chaptarr and gates CI.
- **Phase 9 Extended** (complete): Chaptarr as the source of truth for what's already owned. New `chaptarr_skip_existing` / `chaptarr_skip_when` settings; with them on, every download path asks Chaptarr first and skips books it already has. `services/chaptarr.py` gains a library index (`fetch_library`, paged with a `/book` fallback, 60 s cache), ASIN folding across works and editions with non-`az:` providers filtered out, `check_books` / `filter_new_books` (the latter fails open), and `record_skipped_download` (one row per book, refreshed rather than duplicated). Wired into `POST /api/downloads` (409 `already_in_chaptarr`, `force: true` overrides), `_auto_download_if_enabled`, and `download-all` (which switches to `_run_liberate_checked`). New `POST /api/chaptarr/check`; `GET /api/chaptarr/status` gains `skip_existing`/`skip_when`. Settings card gains the toggle and mode selector and its history is relabelled Recent activity; Liberate gains a **Check Chaptarr** bulk action, "In Chaptarr"/"Tracked" tile badges, and a **Download anyway** action on a refused download. `scripts/test-chaptarr.py` grows 11 checks covering it.
- **Phase 8 Extended** (complete): Owner name editable input added directly to AccountsPage for accounts the logged-in user added (`added_by_user_id === user.id`); saves via `PATCH /api/auth/me` on blur/Enter; amber banner shown when `owner_name` is unset. "Purchased" filter tab added to Liberate page between All and Audible Plus; filters on `LibraryBooks.IsAudiblePlus=0` in both `get_liberate_books()` and `get_liberate_book_ids()`.

## Pre-push sanitization (REQUIRED before any `git push`)

Before pushing to GitHub, the working tree must be fully sanitized. The container
should be in a clean "factory default" state — only the default `admin/admin`
credentials remain, no real Audible accounts, no real library data, no downloads.

### Files/directories to delete

| Path (host) | Reason |
|-------------|--------|
| `./config/AccountsSettings.json` | Real Audible OAuth tokens |
| `./config/LibationContext.db` | Real library data tied to a real Audible account |
| `./config/FileLocationsV2.json` | Libation's ASIN → file-path cache for a real library |
| `./config/SearchEngine/` | Lucene index built from real library |
| `./config/potation.key` | Fernet key that decrypts stored Audible credentials in `audible_accounts.auth_blob` |
| `./config/logs/` | Log files that may contain real email addresses |
| `./data/app.db` | Real user accounts and sessions, plus the Chaptarr URL and API key in `system_settings`; container recreates it with default `admin/admin` on next start |
| `./audiobooks/` (contents) | Downloaded audiobook files — purge all content, keep the directory |

### Inside the running container (ephemeral, non-volume)

| Path (container) | Reason |
|-----------------|--------|
| `/tmp/Libation-*/` | In-progress download staging directories |

### Files to keep / verify

| Path | Expected content |
|------|-----------------|
| `./config/Settings.json` | Only `{"Books": "/audiobooks"}` — no credentials |
| `./config/appsettings.json` | Libation download toggles only — no credentials |
| `./config/Libation/appsettings.json` | Only `{"LibationFiles":"/config"}` — recreated by entrypoint anyway |
| `docker-compose.yml` | `SECRET_KEY` must still be the placeholder `change-me-use-a-long-random-string`; `ADMIN_USERNAME`/`ADMIN_PASSWORD` must be `admin`/`admin` |

### Post-purge verification

After deleting the above, restart the container (`docker compose restart`). On startup:
- `_seed_admin` recreates `app.db` with only the default `admin/admin` user
- No Audible accounts are connected
- The Liberate page shows "no accounts" empty state
- `/audiobooks/` directory exists but is empty

> **Important:** Always do a final `docker compose restart` after sanitizing, even if the container was already restarted mid-process. Deleting `app.db` while the container is live causes a disk I/O error on the stale file handle; the entrypoint restart loop recovers and re-seeds the DB, but a subsequent sanitization pass will delete that freshly-seeded file too — leaving the container running with no database and login broken. The final restart ensures `app.db` is cleanly re-created after all deletions are complete.

## Conventions
- API routes: `/api/<resource>/<action>`
- All API responses use snake_case JSON
- Frontend uses `@/` alias for `frontend/src/`
- No ads, no telemetry, no external dependencies at runtime
