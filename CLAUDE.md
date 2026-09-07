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
### Where the config comes from (`backend/app/services/oidc_config.py`)
SSO is configurable **in Settings → Sign-in**, not only by env var. `load_config(db)` resolves the effective config and `OidcConfig` is threaded through every function in `oidc.py` — nothing reads `settings.OIDC_*` directly any more.

- **Environment wins, per field.** `settings.model_fields_set` distinguishes a value someone supplied (env or `.env`) from a default nobody chose. Env-supplied fields are authoritative, listed in `cfg.env_locked`, rendered read-only in the UI, and ignored by `save_config` — so a deployment pinning its config as code keeps it, and a half-and-half setup (secret from env, rest from the UI) is legitimate
- **The client secret is Fernet-encrypted** at rest under `system_settings.oidc_client_secret_enc`, using the same `potation.key` as Audible credentials. `creds.try_decrypt` returns `None` on a lost key, which reads as *unconfigured* — failing toward password login staying on. It is never returned by the API (`client_secret_set: bool` instead), so the UI cannot echo it back; an omitted `client_secret` on PUT keeps the stored one, `""` clears it
- **`password_login_enabled(db)` is `ALLOW_PASSWORD_LOGIN` when set, else on unless `cfg.configured` AND `sso_has_worked(db, cfg)`.** This is the lockout guard: password sign-in retires only once someone has *actually* signed in through the provider (proved by `users.oidc_subject` with a matching `oidc_issuer`), never merely because the form is filled in or a connection test passed. A wrong client secret or a mismatched redirect URL both survive a test but cannot produce a login. `sso_has_worked` is scoped by issuer, so switching providers does not inherit the old one's proof; a mismatch reports False, which keeps password login on — the safe direction
- `settings.oidc_configured` / `settings.password_login_enabled` were **removed** from `config.py`: both now need the database, and answering from env alone would answer the wrong question
- `GET/PUT /api/auth/oidc/settings` — admin-only. The GET also returns `sso_has_worked`, `password_login_enabled` and `password_login_forced` so the UI can explain *why* password login is where it is, rather than showing a toggle that will not move
- `POST /api/auth/login` and `/verify-2fa` return 403 (naming the env var) when password login is off
- Users are keyed on `users.oidc_subject`, scoped by `users.oidc_issuer`. First sign-in links an existing local account by email then username; an account already linked to a *different* subject is never taken over. `OIDC_AUTO_CREATE_USERS=false` restricts SSO to pre-existing accounts. SSO-created users get an unusable random password hash
- `OIDC_ADMIN_GROUP` both grants and revokes admin on each login; blank leaves admin managed in-app
- The callback sets the refresh cookie and 303s into the app — the access token never travels in a URL, because `AuthContext` already calls `/api/auth/refresh` on mount. `next` is restricted to same-origin paths (`safe_next_path`) so the callback is not an open redirect
- `GET /api/auth/config` is public (the login page needs it before anyone has credentials) and exposes only `{password_login_enabled, oidc_enabled, oidc_provider_name}` — never the issuer, client id or secret
- `UserResponse.is_sso_user` is a computed field; `oidc_subject` is read from the ORM object but `exclude=True` so it never serialises
- **Behind a reverse proxy set `OIDC_REDIRECT_URL`** — uvicorn is not started with `--proxy-headers`, so the derived callback URL would use the internal scheme and host
- Tests: `scripts/test-oidc.py` runs against an in-process stub provider with a real RSA JWKS — 31 checks covering the flow, every refusal, the lockout guard, and the database-configured path (env-locked fields, encryption at rest, the secret never being returned). CI job `oidc` gates `merge`

### Auth API endpoints (`backend/app/api/auth.py`)
- `GET /api/auth/default-credentials` — returns `{"using_default_credentials": bool}`; compares logged-in user's username against `ADMIN_USERNAME` env var and verifies stored hash still matches `ADMIN_PASSWORD`. Used by Settings page to show the amber warning banner.
- `PATCH /api/auth/me` — free-form dict body; updates `audible_account_id` and/or `owner_name` on the logged-in user
- `POST /api/auth/change-username` — body: `{new_username, current_password}`; validates ≥3 chars, 409 on conflict; returns updated `UserResponse`
- `POST /api/auth/change-password` — body: `{current_password, new_password}`; revokes all sessions on success
- `GET /api/auth/sessions` / `DELETE /api/auth/sessions/{id}` / `DELETE /api/auth/sessions` — session management for the logged-in user

## Database backends & migrations
- **SQLite by default** (`sqlite:////data/app.db`); **PostgreSQL optional** via `DATABASE_URL`. `database.py::normalized_url()` rewrites `postgres://`, `postgresql://` and `postgresql+psycopg2://` to `postgresql+psycopg` (psycopg 3 is bundled); `engine_kwargs()` branches per dialect because `check_same_thread` is a sqlite3 argument that raises on psycopg
- **Alembic** replaced `Base.metadata.create_all` + the hand-rolled `_migrate_db`. `app/migrations.py::run_migrations()` handles three states: fresh database (run every revision), pre-Alembic database (run `legacy_migrations.migrate_pre_alembic`, stamp `0001`, then upgrade — tables are *adopted*, never rebuilt), and already-migrated (upgrade to head)
- Revisions live in `backend/alembic/versions/`: `0001_baseline` reproduces the pre-Potation schema exactly; `0002_potation_schema` adds the engine's own tables; `0003_oidc` adds SSO identity on `users` plus `oidc_login_states`; `0004_audible_login_state` adds `audible_login_states`; `0005_must_change_password` adds the first-run password flag on `users`. `env.py` sets `render_as_batch` for SQLite only — SQLite cannot `ALTER` much, and on PostgreSQL batch mode would rebuild tables for nothing
- `alembic.ini` and `alembic/` are copied to `/app/` in the Dockerfile, beside `app/`, because `migrations.py` resolves them relative to the package parent
- `seed_system_settings()` uses `ON CONFLICT ... DO NOTHING` rather than SQLite's `INSERT OR IGNORE`, so it runs on both backends
- Timestamps on Potation tables are `DateTime(timezone=True)`; the older tables store tz-aware values in naive columns (which Postgres silently strips), so `api/downloads.py` re-attaches `timezone.utc` on read. Mixed convention until Phase D drops the old tables

## Potation — native Audible engine (in progress)
Replaces LibationCli, the `libation-bridge` C# sidecar, and the direct reads of Libation's SQLite. Foundations, native auth, library sync, the DRM census, reconciliation and the `liberated` derivation are in. **Nothing is wired into the API yet** — LibationCli is still the engine, and `services/cli.py` / `services/libation.py` remain the live path. The download/decrypt pipeline and the API cut-over are next.
- `services/potation/creds.py` — Fernet encryption for stored Audible credentials. The key is `{LIBATION_CONFIG}/potation.key`, generated 0600 on first use, **not** derived from `SECRET_KEY` so rotating the JWT secret doesn't log out every Audible account. `try_decrypt_json()` returns `None` on a lost key so the account is flagged `needs_reauth` rather than taking startup down
- `models/potation.py` — `audible_accounts`, `books` (incl. multi-part parent/child), `book_files` (replaces `FileLocationsV2.json`, carries `part_index` so parts don't sort lexically), `audible_licenses` (voucher reuse + `drm_type`), `download_jobs` (state machine, classified `error_code`, `cancel_requested`), `download_quota` (daily-cap ledger), `reconciliation_runs`
- `books.liberated_override` is tri-state: NULL derives from `book_files`, 1/0 is an explicit user override
- `services/potation/marketplaces.py` — the frontend still posts LibationCli's marketplace *names* (`"germany"`, not `"de"`); this maps them to `audible` country codes. A wrong marketplace fails late, at device registration, behind a sign-in URL that looked fine
- `services/potation/auth.py` — two-step device registration. `begin_login()` builds the Amazon OAuth URL and stores the PKCE verifier (encrypted) + device serial on an `audible_login_states` row; `complete_login()` consumes the row **before** exchanging the code, so a double submit cannot register two devices (Amazon caps registrations). Replaces the `libationcli login-external` subprocess that was held alive in a module-level dict. `disconnect_account()` calls `deregister_device()` — the old `DELETE /api/accounts/{id}` never did, leaving a registered device behind on every removal
- `services/potation/client.py` — `Authenticator.to_dict()/from_dict()` is the serialisation boundary; the blob is Fernet-encrypted onto `audible_accounts.auth_blob`. `client_for()` re-saves after the block because the authenticator silently refreshes expired access tokens. A blob that will not decrypt sets `needs_reauth` and drops the account from `active_accounts()` rather than raising past the caller
- `services/potation/library.py` — library sync. Parts of a `MultiPartBook` become child rows ordered by Audible's `sort` key, which is what stops "Part 10" preceding "Part 2". **The plan's claim that "a `MultiPartBook` parent has no downloadable content" is out of date** — it came from Libation's source, and Audible now answers a licence request for a part ASIN with `404 Audio Part asins are no longer supported`. The licensable unit is the *parent*; the child rows are ours, for ordering. Phase B must license the parent — which is not yet verified to succeed, only inferred from that refusal
- `services/potation/license.py` — `POST content/{asin}/licenserequest`. `content_license.drm_type` is the number the whole plan turns on: `Adrm` (AAX/AAXC) and `Mpeg` are natively downloadable; `Widevine`/`PlayReady`/`FairPlay` need a CDM we do not have. Licenses are persisted so a retry does not buy another one — a Download license counts against Audible's daily allowance
  - **The census samples `parent_asin IS NULL`** — owned titles, never individual parts. Sampling parts is what made the first real run return 23 refusals out of 25 and then report a percentage off the two that answered
  - **A CDM answer is re-asked before it counts as blocked.** The first probe advertises `ALL_DRM_TYPES`, so Audible replies with what it would *prefer* to serve; the download pipeline will only ever claim `NATIVE_DRM_TYPES`, and Audible may fall back to AAXC for such a client. `_reprobe_as_native()` asks again on those terms, and only a title refused *there* is genuinely out of reach. Counting the first answer alone overstates the blocked share
  - `DrmCensus.verdict()` refuses to give a percentage on fewer than 5 answers, and names the part-ASIN refusal by sight (`PART_ASIN_REFUSED`) rather than burying it in a list of failures
- `services/potation/reconcile.py` — matches audiobook files already on disk back to books, so `liberated` can be trusted once Potation owns it. Walks `AUDIOBOOKS_DIR` **and** the Chaptarr roots (discovered via `/rootfolder`, reverse-mapped through `chaptarr.unmap_path`) because `import_mode=move` relocates files out of our directory entirely. Matching ladder: embedded ASIN tag → ASIN shape in the path → `.metadata.json` sidecar → fuzzy title+author/duration. A candidate only counts if `books` actually holds that ASIN, which is what stops a stray regex hit becoming a match; a fuzzy hit is stored `confirmed=False` and never counts as downloaded
  - **Two pruning guards, both load-bearing.** Rows are only pruned under a root that was actually walked, and only when `os.stat` positively reports ENOENT — "I could not stat it" is never "it is gone". And a walk that reported *any* read error prunes nothing at all: an unreadable directory is indistinguishable from a directory of deletions, and getting it wrong hands a whole shelf back to the auto-download loop
  - `(path, size, mtime)` on `book_files` is the skip cache. It only covers files that matched a book — an unmatched file has no row to hold its signature and is re-read every run
  - `reconciliation_complete()` is the gate and `check_bulk_enqueue()` the valve (refuses >25 without explicit confirmation, and refuses anything at all before a clean pass). **Deliberately not wired into the live paths** — LibationCli still owns `liberated`; these exist for the Phase C cut-over to call
- `services/potation/liberated.py` — `liberated = liberated_override IF NOT NULL ELSE EXISTS(book_files WHERE kind='audio' AND confirmed)`. `liberated_map()` answers a whole page in one query; `audio_paths()` orders by `part_index` and replaces `libation.get_audio_file_paths` at cut-over; `unconfirmed_files()` / `confirm_file()` are the queue for fuzzy matches awaiting a person
- `scripts/potation-census.py` — `login` / `sync` / `census` / `disconnect` subcommands to measure DRM exposure against a real account. **`disconnect` is not optional housekeeping**: `login` registers a device on the Amazon account, Amazon caps how many one may hold, and deleting the scratch directory does not release it — the registration lives at Amazon, so removing the local credentials only makes the slot unrecoverable. Samples 25 titles by default; `--sample 0` sweeps everything and prompts first. Keeps its database and `potation.key` under a gitignored `.potation-census/` because both hold live Audible credentials
- Tests: `scripts/test-potation.py`, run as `PYTHONPATH=backend python scripts/test-potation.py`. Runs the database section on SQLite always, and on PostgreSQL when `POTATION_TEST_POSTGRES_URL` is set. CI job `potation` runs both and gates `merge`
- Tests: `scripts/test-reconcile.py` — reconciliation and `liberated`, against files `mutagen` really tags (so the tag path is exercised, not mocked). Covers the ladder, a Chaptarr-moved file found by tag alone, part ordering, the skip cache, fuzzy matches staying inert, both pruning guards, the tri-state override, and the gate/valve. CI job `reconcile` gates `merge`

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
- `GET /api/liberate/books` — all books with status from `UserDefinedItem.BookStatus` (0=not_liberated, 1=liberated, 2=error) overlaid with active `downloads` table rows; accepts `account_id`, `search`, `filter_status`, `page`, `page_size` params
- `GET /api/liberate/book-ids` — returns all matching book IDs (no pagination) for Select All across pages; accepts the same filter params
- **`filter_status`** values: `all`, `liberated`, `not_liberated`, `error`, `downloading`, `audible_plus` (IsAudiblePlus=1), `purchased` (IsAudiblePlus=0). Both endpoints build their WHERE clause from the one `libation._liberate_filter()` helper, so the grid, its `total` and "Select All (N)" always describe the same set. Filtering the page in Python after LIMIT/OFFSET is what used to make them disagree
  - The four *download-state* tabs (`liberated`/`not_liberated`/`error`/`downloading`) partition the library, and an in-flight download wins over the stored `BookStatus` — so they exclude anything currently queued or running. The two *ownership* tabs are a separate axis and deliberately do not
  - An unrecognised `filter_status` matches **nothing**. Silently ignoring it would hand the whole library to `_run_liberate_checked` / `_auto_download_if_enabled`, both of which enumerate through `get_liberate_book_ids`
- `PATCH /api/liberate/books/{book_id}` — sets `UserDefinedItem.BookStatus` (1=liberated, 0=not liberated); INSERTs row if missing (provides all NOT NULL cols: BookStatus, IsFinished, Ratings, Tags)
- `GET /api/liberate/cap` — current cap accounting for logged-in user
- `POST /api/liberate/download-all` — fires `libationcli liberate` (no-args); only available when user has no cap. When the Chaptarr skip-check is active it runs `_run_liberate_checked()` instead — enumerate `not_liberated`, filter through Chaptarr, queue the rest one at a time — because the blanket CLI liberate decides for itself what to fetch and can't be filtered
- Individual downloads still go through `POST /api/downloads` with per-call cap enforcement
- Tests: `scripts/test-liberate.py`, run as `PYTHONPATH=backend python scripts/test-liberate.py`. Builds a synthetic v13 `LibationContext.db` and pages through every tab asserting the count, the pages and the id list line up; also covers the natural sort of `get_audio_file_paths`. CI job `liberate` gates `merge`

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

## First-run admin credentials
`ADMIN_USERNAME` defaults to `admin`. **`ADMIN_PASSWORD` defaults to empty, meaning "not supplied"** — `settings.admin_password_supplied` is the predicate everything branches on.

- **Not supplied** → `_seed_admin` mints a random password via `generate_password()` and prints it with `_announce_generated_password()`. The seeded row gets `must_change_password = 1`
- **Supplied** → exactly the old behaviour, `must_change_password = 0`. This is the upgrade guarantee: a deployment that pins `ADMIN_PASSWORD` is untouched, which is why `docker-compose.yml` and `unraid-template.xml` now leave it blank but keep the variable documented
- **On restart while `must_change_password` is still set** (and no password supplied), the password is *regenerated and reprinted*. A bcrypt hash cannot be read back, so without this the only recovery from closing the terminal is deleting `app.db`. The printed banner says so, so the behaviour is predictable rather than surprising
- `generate_password()` draws from an alphabet with `i l 1 o O 0` removed and hyphenates into groups — this gets transcribed by hand out of `docker logs`
- **The password goes to stdout only, never `get_logger()`.** The log file lives on the mapped `/config` volume and would outlive the password; container stdout is not persisted by us. `scripts/test-firstrun.py` asserts it never appears in the log file
- `_seed_admin` uses raw SQL via `db.connection()` (same as the legacy migrations) rather than ORM — avoids the issue where a dangling DBAPI transaction causes a subsequent `db.add(User(...))` commit to silently not persist. **`db.commit()` returns the connection to the pool**, so the handle must be re-acquired after every commit rather than cached across one

`users.must_change_password` (revision `0005`, `server_default` false so existing rows are unaffected) is surfaced on `UserResponse`. `ProtectedRoute` in `App.tsx` renders `ChangePasswordRequiredPage` instead of *any* protected route while it is set — the password was printed to a log, so nothing behind it should be reachable. SSO accounts are exempt: they have no usable password, so the prompt would be a dead end. `POST /api/auth/change-password` clears the flag, and now also rejects a new password equal to the current one — otherwise the prompt could be satisfied without changing anything.

`GET /api/auth/default-credentials` detects whether the logged-in user is still on an env-supplied factory default. It returns `false` outright when no `ADMIN_PASSWORD` was supplied: there is no default to be on, `must_change_password` covers that case instead, and checking anyway would bcrypt an empty string on every call. When `true`, the Settings **Account** tab surfaces `UpdateCredentialsSection` — a single form that changes username + password together and signs the user out immediately after.

Tests: `scripts/test-firstrun.py`. CI job `firstrun` gates `merge`.

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

Paths come back **naturally sorted** (`_natural_key`, digit runs compared as numbers). Chaptarr imports the list in the order it is given, and neither the cache nor the glob carries a part index — so ordering has to come out of the path, and plain lexical order puts "Part 10" before "Part 2".

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

## Settings page layout (`frontend/src/pages/SettingsPage.tsx`)
`SettingsPage` is a thin tabbed shell; every section lives in its own file under `frontend/src/components/settings/`. Tabs, in order: **Account** (update-credentials when on defaults, 2FA, username, password, sessions), **Library** (Libation download toggles), **Integrations** (Chaptarr), **Users** (management + permissions), **Sign-in** (OIDC), **System** (about, backup & restore, logs, API docs).

- `adminOnly` tabs are hidden outright for non-admins rather than rendered empty. Sections *within* a visible tab are still individually gated — `System` shows About to everyone but Logs and API docs only to admins
- The active tab is held in `?tab=` so a section can be linked to and survives a reload. An unknown or now-forbidden id falls back to the first tab the user can see; the fallback never rewrites the URL, so an admin deep-link still resolves once `user` finishes loading
- The default-credentials banner renders above the tab content on **every** tab — it is the one warning that must not be possible to tab away from — and links to the Account tab

## Settings & Stats (`backend/app/api/settings.py`)
- `GET/PUT /api/settings/libation` — reads/writes `/config/appsettings.json` (resilient: merges only known keys)
- `GET /api/settings/stats` — total_books (LibationContext.db), total_downloads (our DB), accounts_count (bridge `/accounts`), downloads_per_user (JOIN)
- The appsettings read/parse/write and the snake_case ↔ PascalCase field map live in `services/appsettings.py`, not in the API module — the settings backup needs the same file, and two copies of the field map would drift

## Settings backup & restore (`backend/app/services/backup.py`)
One JSON document holding the **settings**, not the data: the Chaptarr connection, the OIDC provider and Libation's download toggles. Users, sessions, download history and the library are not settings and are not in it.

- `GET /api/settings/backup?include_secrets=true` — admin-only; `Content-Disposition: attachment`, `Cache-Control: no-store`
- `POST /api/settings/restore` — admin-only; body `{backup, sections?}`, returns `{applied, skipped, env_locked, secrets_missing, warnings}`. `sections` omitted restores every section the file carries
- **The OIDC client secret leaves as plaintext and is re-encrypted on the way in.** It is stored Fernet-encrypted under `potation.key`, which deliberately does *not* travel with the backup — shipping the ciphertext would restore to a secret the destination cannot read, and a bad secret looks exactly like a working one until someone tries to sign in
- **Secrets are opt-out, not opt-in.** A restore that leaves you retyping the Chaptarr API key has not restored much. `include_secrets=false` produces a shareable copy, lists what it withheld in `secrets_omitted` (only secrets that actually exist), and the document says so about itself in `_warning`
- **Absent means "keep what is stored", and `null` counts as absent** — `""` is how you clear a key, same as the UI. Anything that has been through a schema (the API's own pydantic model included) arrives with every optional field present and null, so reading null as "clear" would make a sanitised backup wipe the secrets it deliberately declined to carry
- **Audible accounts are excluded in both directions.** The stored blob is a *device registration* encrypted under `potation.key`, and Amazon caps registrations — moving it would be either useless (a different key) or wrong (two installs sharing one device). Reconnecting is a login, not data loss
- Restoring routes every section through the same `save_config` the Settings page uses, so the OIDC encryption and the env-lock rule apply for free; `env_locked` in the report names the fields the destination refused rather than reporting a restore that silently did half of it
- **A restore cannot lock you out.** `password_login_enabled` still requires `sso_has_worked`, which a restored configuration cannot fake. An enabled-but-incomplete Chaptarr or OIDC section is inert and produces a warning
- Every selected section is validated before any is written, so one bad value cannot leave a neighbour applied
- Tests: `scripts/test-backup.py` (21 checks). CI job `backup` gates `merge`

**Authenticated file downloads** — `downloadFile()` in `frontend/src/lib/api.ts` fetches as a blob and clicks a synthetic object-URL link. A plain `<a href download>` cannot be used: the access token lives in memory and is attached by the request interceptor, so a link navigation arrives with no `Authorization` header and is refused. Used by both the settings backup and the log download.

## Logs API (`backend/app/api/logs.py`)
- `GET /api/logs?lines=200&level=all` — admin-only; reads `/config/logs/libation-web.log`, filters lines by `[LEVEL]` substring match, returns `{"lines": [...], "total": int, "truncated": bool}`; max 2000 lines per request
- `GET /api/logs/download` — admin-only; serves the full log file as `text/plain` download (`libation-web.log`). Fetched with `downloadFile()`, not a plain link — see Settings backup & restore
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
- **Liberate filtering and file-order fixes** (complete): two long-standing bugs in `services/libation.py`. (1) `get_liberate_books()` computed `total` from the *unfiltered* WHERE, applied LIMIT/OFFSET, then filtered the page in Python — so with any tab active the grid, the page count, the "x–y of N" range and "Select All (N)" all disagreed, and pages after the first were part-empty. Both endpoints now share one `_liberate_filter()` predicate applied in SQL; ownership tabs mirror the same `LIMIT 1` expression the row renders from (an `EXISTS` put a book held on two accounts in *both* the Plus and Purchased tabs), and an unrecognised tab matches nothing instead of everything. (2) `get_audio_file_paths()` sorted lexically, so Chaptarr received a multi-part or chapter-split book with "Part 10" ahead of "Part 2" — now sorted with `_natural_key`. New `scripts/test-liberate.py` (15 checks against a synthetic v13 `LibationContext.db`) and CI job `liberate` gating `merge`. No frontend change: the page already derived everything from `total`.
- **Potation A7/A8 — reconciliation and `liberated`** (complete): the last of Phase A. New `services/potation/reconcile.py` walks both audiobook roots and matches files back to books tag-first, and `services/potation/liberated.py` derives "do I already have this?" from `book_files` with a tri-state override. `chaptarr.unmap_path()` added as the inverse of `map_path` so Chaptarr's own root folders can be walked. Adds `mutagen` to requirements. New `scripts/test-reconcile.py` (23 checks against really-tagged files) and CI job `reconcile` gating `merge`. Still additive: the gate and valve exist but nothing calls them, and LibationCli continues to own `liberated` until the Phase C cut-over.

- **Settings tabs** (complete): `SettingsPage` split into Account / Library / Integrations / Users / System tabs, each section moved into its own file under `components/settings/`. Active tab in `?tab=`; admin-only tabs hidden outright; the default-credentials banner renders on every tab.
- **OIDC in Settings** (complete): SSO moves from environment-only to editable in **Settings → Sign-in**. New `services/oidc_config.py` resolves env-over-database per field (`model_fields_set` distinguishes supplied from default), Fernet-encrypts the client secret under `system_settings`, and owns `password_login_enabled`. `OidcConfig` is threaded through every function in `oidc.py`; `settings.oidc_configured` / `password_login_enabled` removed from `config.py`. **The behaviour change that matters**: password sign-in no longer retires when SSO is merely *configured* — only once someone has actually signed in through the provider, since a wrong secret or redirect URL survives a connection test. New `GET/PUT /api/auth/oidc/settings`, `OidcSection.tsx`, and 10 further checks in `scripts/test-oidc.py`.
- **Settings backup & restore** (complete): `GET /api/settings/backup` / `POST /api/settings/restore` (both admin-only) round-trip the Chaptarr, OIDC and Libation settings through one JSON document, restorable whole or by section. New `services/backup.py`, `services/appsettings.py` (extracted from `api/settings.py` so the backup and the API share one field map), `schemas/backup.py`, `BackupSection.tsx` on the System tab, and `downloadFile()` in `lib/api.ts` — which also fixes the log download, whose plain `<a href>` could never have carried the bearer token the endpoint requires. Secrets are included by default with an opt-out; the OIDC client secret is decrypted out and re-encrypted in, because `potation.key` stays behind. Audible device registrations are excluded either way. `scripts/test-backup.py` (21 checks), CI job `backup`. `APP_VERSION` moved to `config.py`, ending the three-way duplication of `"0.4.0"`.
- **Generated first-run password** (complete): a fresh install no longer comes up as `admin/admin`. With `ADMIN_PASSWORD` unset, `_seed_admin` generates a random password, prints it to **stdout only** (never the log file on `/config`), and sets the new `users.must_change_password` flag; `ProtectedRoute` then holds the account at `ChangePasswordRequiredPage` until it is replaced. Restarting before that first change regenerates and reprints, so losing the password is recoverable without deleting `app.db`. Supplying `ADMIN_PASSWORD` keeps the old behaviour exactly, which is the upgrade guarantee. New revision `0005`, `scripts/test-firstrun.py` (14 checks), CI job `firstrun`.

## Pre-push sanitization (REQUIRED before any `git push`)

Before pushing to GitHub, the working tree must be fully sanitized. The container
should be in a clean "factory default" state — no real user accounts, no real
Audible accounts, no real library data, no downloads.

### Files/directories to delete

| Path (host) | Reason |
|-------------|--------|
| `./config/AccountsSettings.json` | Real Audible OAuth tokens |
| `./config/LibationContext.db` | Real library data tied to a real Audible account |
| `./config/FileLocationsV2.json` | Libation's ASIN → file-path cache for a real library |
| `./config/SearchEngine/` | Lucene index built from real library |
| `./config/potation.key` | Fernet key that decrypts stored Audible credentials in `audible_accounts.auth_blob` |
| `./config/logs/` | Log files that may contain real email addresses |
| `./data/app.db` | Real user accounts and sessions, plus the Chaptarr URL and API key in `system_settings`; the container recreates it on next start with a freshly generated admin password printed to stdout |
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
| `docker-compose.yml` | `SECRET_KEY` must still be the placeholder `change-me-use-a-long-random-string`; `ADMIN_USERNAME` must be `admin`, and **`ADMIN_PASSWORD` must stay commented out** — uncommenting it turns off the generated first-run password for everyone who deploys from this file |

### Post-purge verification

After deleting the above, restart the container (`docker compose restart`). On startup:
- `_seed_admin` recreates `app.db` with one admin user, whose password is generated and printed to stdout (`docker compose logs libation`)
- No Audible accounts are connected
- The Liberate page shows "no accounts" empty state
- `/audiobooks/` directory exists but is empty

> **Important:** Always do a final `docker compose restart` after sanitizing, even if the container was already restarted mid-process. Deleting `app.db` while the container is live causes a disk I/O error on the stale file handle; the entrypoint restart loop recovers and re-seeds the DB, but a subsequent sanitization pass will delete that freshly-seeded file too — leaving the container running with no database and login broken. The final restart ensures `app.db` is cleanly re-created after all deletions are complete.

## Pull request workflow
**Merge a PR as soon as it is green — don't wait to be asked.** Open it as a draft, then once CI passes: mark it ready for review and merge it. No confirmation step.

What "green" means here:
- Every job in `.github/workflows/docker-ghcr.yml` that actually ran has `conclusion: success`. **`merge` reporting `skipped` on a PR is expected, not a failure** — that job is gated on `github.event_name != 'pull_request'` and only runs on a push to `main`.
- `mergeable_state` is `clean` (no conflict with `main`).
- No unresolved review comments.

Anything short of that is work, not a reason to wait: fix it and push. Merge with `expectedHeadSha` pinned to the commit whose CI you actually verified, so a race can't merge something unverified. Afterwards, reset the working branch onto the new `main` (`git checkout -B <branch> origin/main`) and confirm the `main` publish republished the GHCR tags to a fresh digest.

This is a single-maintainer repository and there is no human review gate; the CI suites (`chaptarr`, `liberate`, `reconcile`, `firstrun`, `backup`, `potation`, `oidc`, `smoke`) are the gate. Adding a check to one of them is how you make a new invariant enforceable.

## Conventions
- API routes: `/api/<resource>/<action>`
- All API responses use snake_case JSON
- Frontend uses `@/` alias for `frontend/src/`
- No ads, no telemetry, no external dependencies at runtime
