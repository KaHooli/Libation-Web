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
- Bridge source: `libation-bridge/Program.cs`
- Bridge project: `libation-bridge/LibationBridge.csproj`

## Auth system
- **Access token**: 15-min JWT in response body, stored in memory (React context)
- **Refresh token**: 60-day JWT in httpOnly cookie (`/api/auth` path), hashed in `sessions` table
- **2FA**: TOTP via `pyotp`, optional per user, toggled in Settings
- **Session persistence**: On page load, silently calls `/api/auth/refresh` using the cookie
- **Auto-refresh**: Timer in `AuthContext` refreshes access token 2 min before expiry

## Database (SQLite at `/data/app.db`)
- `users`: id, username, hashed_password (bcrypt), totp_secret, totp_enabled, is_active, is_admin, permissions (JSON), download_cap (INTEGER), audible_account_id (TEXT), owner_name (TEXT), created_at
- `sessions`: id, user_id, refresh_token_hash (sha256), expires_at, created_at, last_used_at, ip_address, user_agent
- `downloads`: id, book_id, book_title, user_id, status, progress, started_at, completed_at, error_message, created_at
- `scans`: id, status, started_at, completed_at, books_added, output, error_message

## Permissions system
- `DEFAULT_PERMISSIONS` in `models/user.py`: all flags `true` except `can_remove_downloads = false`
- Flags: `can_download`, `can_scan`, `can_manage_accounts`, `can_liberate`, `can_remove_downloads`
- Admins bypass all checks; non-admin users inherit `DEFAULT_PERMISSIONS` if their `permissions` column is NULL
- `PATCH /api/users/{id}/permissions` — admin-only, updates flags + `download_cap`
- `download_cap = null` means unlimited; positive integer = max downloads per 12-hour rolling window
- 12h window enforcement: `COUNT(downloads WHERE user_id=? AND created_at > NOW()-12h)`; 429 response includes `resets_at` ISO timestamp

## Liberate service
- `GET /api/liberate/books` — all books with status from `UserDefinedItem.BookStatus` (0=not_liberated, 1=liberated, 2=error) overlaid with active `downloads` table rows; accepts `account_id`, `search`, `filter_status`, `page`, `page_size` params
- `GET /api/liberate/book-ids` — returns all matching book IDs (no pagination) for Select All across pages; accepts same filter params
- `PATCH /api/liberate/books/{book_id}` — sets `UserDefinedItem.BookStatus` (1=liberated, 0=not liberated); INSERTs row if missing (provides all NOT NULL cols: BookStatus, IsFinished, Ratings, Tags)
- `GET /api/liberate/cap` — current cap accounting for logged-in user
- `POST /api/liberate/download-all` — fires `libationcli liberate` (no-args); only available when user has no cap
- Individual downloads still go through `POST /api/downloads` with per-call cap enforcement

## Update service (`backend/app/services/update.py` + `backend/app/api/updates.py`)
- `GET /api/updates/status` — returns installed CLI version (parsed from `libationcli --version` stderr), latest GitHub release version, up-to-date flag, release date, and whether a rollback `.deb` is available
- `POST /api/updates/check` — same as status but force-bypasses the 1-hour in-memory GitHub API cache
- `POST /api/updates/install` — admin only; downloads the `linux-chardonnay-{arch}.deb` asset from GitHub to `/config/updates/`, writes `/config/updates/pending`, then SIGTERMs uvicorn via background task (response is sent first)
- `POST /api/updates/rollback` — admin only; re-stages the previous `.deb` and triggers restart
- Latest release cached in-process for 3600s to avoid GitHub API rate limits
- Architecture detected via `platform.machine()`: `aarch64`/`arm64` → `arm64`, else `amd64`
- `httpx` (async) used for GitHub API and `.deb` download (streamed in 64KB chunks)

## Entrypoint restart loop (`docker-entrypoint.sh`)
- Replaced `exec gosu ... uvicorn` with a `while true` loop so the container survives uvicorn restarts
- On each iteration: checks `/config/updates/pending`; if present, runs `dpkg -i` as root, writes `/config/updates/current`, removes pending flag
- Previous `.deb` filename saved to `/config/updates/rollback` before each update for one-step rollback
- **Bridge bootstrap**: each iteration pre-seeds `/config/Libation/appsettings.json` with `{"LibationFiles":"/config"}` — Libation's startup bootstrap reads `{CWD}/Libation/appsettings.json` and `Program.cs` sets CWD to `/config`, so this tells it to use `/config` as its files dir (matching `libationcli --libationFiles /config`)
- **Bridge startup**: LibationBridge starts before uvicorn; `wait_for_bridge()` polls `GET /health` on `localhost:8001` up to 30×1s; fatal exit if it never responds
- Both `BRIDGE_PID` and `UVICORN_PID` tracked; bridge is killed when uvicorn exits and restarted on the next loop iteration
- Restart path: Python writes `/tmp/libation-restart` sentinel → SIGTERMs uvicorn → loop kills bridge, detects sentinel → installs update → restarts both
- Crash path (no sentinel): loop restarts both after 5s delay
- `SIGTERM` to container (e.g. `docker stop`) sets `SHOULD_EXIT=true`, kills both processes, exits loop cleanly

## Default credentials
Set via env vars `ADMIN_USERNAME` / `ADMIN_PASSWORD` (defaults: `admin` / `admin`).
Admin user is seeded on first startup if no users exist. `_seed_admin` uses raw SQL via `conn.execute()` (same as `_migrate_db`) rather than ORM — avoids the issue where `_migrate_db` calling `db.connection()` leaves a dangling DBAPI transaction that causes subsequent `db.add(User(...))` commits to silently not persist.

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

## Downloads & Scan (`backend/app/api/downloads.py`)
- `POST /api/downloads/scan` creates a `Scan` row, fires `asyncio.create_task` to call `cli.run_scan()` → bridge `POST /scan`
- `POST /api/downloads` creates a `Download` row with `user_id`, fires task to call `cli.run_liberate(asin)` → bridge `POST /download/{asin}` + poll `GET /progress/{asin}`
- Background tasks update DB rows as progress changes; frontend polls `/api/downloads` every 2s
- Duplicate active downloads blocked with 409

## User management (`backend/app/api/users.py`)
- Admin-only routes behind `require_admin` dependency
- `GET /api/users`, `POST /api/users`, `PATCH /api/users/{id}`, `DELETE /api/users/{id}`
- Cannot delete own account, cannot revoke own admin status
- `is_admin` column added via startup migration (`_migrate_db`) using `ALTER TABLE` + `PRAGMA table_info`

## Settings & Stats (`backend/app/api/settings.py`)
- `GET/PUT /api/settings/libation` — reads/writes `/config/appsettings.json` (resilient: merges only known keys)
- `GET /api/settings/stats` — total_books (LibationContext.db), total_downloads (our DB), accounts_count (bridge `/accounts`), downloads_per_user (JOIN)
- Field map: Python snake_case ↔ Libation PascalCase key names

## Logging (`backend/app/services/logger.py`)
- Writes to `/config/logs/libation-web.log` (on the mapped `/config` volume — survives container restarts)
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
- **Phase 6** (complete): CLI self-update. `docker-entrypoint.sh` replaced with a restart loop that installs `/config/updates/pending` `.deb` as root on each iteration. New `update` service + API: `GET /status`, `POST /check`, `POST /install` (admin), `POST /rollback` (admin). GitHub Releases API queried for `linux-chardonnay-{arch}.deb` asset; 1hr in-memory cache. Previous `.deb` retained for rollback. Settings page gains an About section (all users) showing installed vs latest version with update/rollback controls for admins. Frontend polls `/api/health` after triggering restart and refreshes when server comes back.
- **Phase 5 Extended** (complete): User Management gains inline `owner_name` field and Audible Account dropdown (sets `users.audible_account_id`). Liberate page gains owner filter tabs, centered search bar (300ms debounce), per-book Mark Downloaded/Not Downloaded (`PATCH /api/liberate/books/{book_id}`), Multi Select mode with Select All spanning all pages (via `GET /api/liberate/book-ids`) plus bulk mark actions, and per-page selector [24/48/96/200]. Accounts page shows post-login "go to Downloads → Scan Library" info banner. Liberate moved to top of sidebar and set as default view (`/` redirects to `/liberate`). Library and My Books removed from sidebar nav and routes entirely (pages still exist in codebase but are not linked).
- **Phase 7 seed fix** (complete): `_seed_admin` in `main.py` rewritten to use raw SQL (`conn.execute()`) instead of ORM (`db.add(User(...))`). Root cause: `_migrate_db` calls `db.connection()` which acquires a DBAPI connection and begins a transaction; if no migrations run, no `db.commit()` is called, leaving the session with a dangling connection. The subsequent ORM `db.commit()` in the old `_seed_admin` did not reliably persist the row. The raw SQL approach shares the same connection path as `_migrate_db` and works correctly. Also added try/except with explicit logger.error logging and flush=True on print so failures are never silent.
- **Phase 7** (complete): LibationBridge ASP.NET Core 10 sidecar replaces subprocess calls for downloads and scans. New `libation-bridge/` directory with `LibationBridge.csproj` and `Program.cs`. Bridge references Libation DLLs at `/usr/lib/libation/` directly via `AssemblyResolve` hook + `[MethodImpl(NoInlining)]` isolation. Real `StreamingProgressChanged` events (0–100%) replace fake 5/95 progress jumps from stdout parsing. Dockerfile gains a `bridge-builder` stage (Stage 2) that installs the Libation `.deb` for compile-time DLL resolution then publishes a self-contained single-file binary. Entrypoint pre-seeds `/config/Libation/appsettings.json` with `{"LibationFiles":"/config"}` so the bridge's Libation scaffolding uses the same config path as `libationcli`. `cli.py` rewritten to route downloads and scans through bridge HTTP; login stays PTY subprocess. `BRIDGE_URL` added to `config.py`.

## Conventions
- API routes: `/api/<resource>/<action>`
- All API responses use snake_case JSON
- Frontend uses `@/` alias for `frontend/src/`
- No ads, no telemetry, no external dependencies at runtime
