# Libation Web UI — Roadmap

## Phase 1 — Foundation & Auth ✅
- [x] Project structure (FastAPI backend + React frontend, single container)
- [x] Docker multi-stage build (Node build → Python runtime)
- [x] SQLite database with SQLAlchemy ORM
- [x] User model + session model
- [x] Password hashing (bcrypt)
- [x] JWT access tokens (15-min) + refresh tokens (60-day httpOnly cookie)
- [x] Silent token refresh on page load and on timer
- [x] 2FA via TOTP (Google Authenticator, Authy compatible)
- [x] 2FA setup flow with QR code
- [x] 2FA toggle (enable / disable) in Settings
- [x] Change password
- [x] Login page with show/hide password
- [x] 2FA verification page with 6-digit input grid + paste support
- [x] Protected routes with auth guard
- [x] Responsive sidebar layout (hamburger menu on mobile)
- [x] Settings page
- [x] Dashboard shell with stat cards
- [x] CLAUDE.md + roadmap.md

---

## Phase 2 — Library View ✅
- [x] LibationCli integration layer (`backend/app/services/libation.py`)
- [x] Read Libation's `LibationData.db` SQLite for book metadata (schema-discovery, resilient to column name variations)
- [x] `GET /api/library/books` — paginated book list with search + sort
- [x] Cover art serving (`GET /api/library/covers/{book_id}`, reads path from DB)
- [x] Library grid view (cover art, title, author, series, duration)
- [x] Library list view toggle
- [x] Search by title (debounced)
- [x] Sort by title, date added, length (asc/desc)
- [x] Book detail slide-over panel (cover, title, author, narrator, series, duration, description)
- [x] Empty state — "no accounts" vs "no results" vs "empty library"
- [x] Loading skeleton for grid and list views
- [x] Pagination (prev/next with page indicator)

---

## Phase 3 — Accounts & Downloads ✅
- [x] `GET /api/accounts` — list connected accounts (via `libationcli list-accounts --bare`)
- [x] `POST /api/accounts/login/start` — start external login flow, return login URL
- [x] `POST /api/accounts/login/complete` — feed response URL, complete OAuth
- [x] `POST /api/downloads/scan` — trigger library scan (`libationcli scan`)
- [x] `GET /api/downloads/scan/latest` — latest scan status (polling)
- [x] `POST /api/downloads` — queue a book download (stores `user_id`); starts `libationcli liberate --id`
- [x] `GET /api/downloads` — list all downloads with status + progress
- [x] `DELETE /api/downloads/{id}` — remove completed/failed download
- [x] Download progress parsed from `liberate` output (% pattern)
- [x] 2-second polling for active downloads and running scans
- [x] Download button on library BookCards (appears on hover)
- [x] Accounts management page (3-step login flow: form → copy URL → paste response)
- [x] Downloads page (active queue, failed, completed, scan status banner)
- [x] Account removal implemented in Phase 7 via direct edit of `/config/AccountsSettings.json` (LibationCLI has no remove-account command)

---

## Phase 4 — Settings & Polish ✅
- [x] Dashboard stat cards: total books, total downloads, connected accounts, **downloads per user** (top downloader shown; sources from `user_id` on downloads table — replaces the removed "Listening hours" card)
- [x] Libation settings passthrough (read/write `appsettings.json` — toggle-based UI for 8 key settings)
- [x] Multiple user support — admin can create, enable/disable, and delete users; `is_admin` field with crown badge
- [x] Session management — list active sessions with device/IP/last-used; revoke individual or all sessions
- [x] Dark mode toggle — persisted to localStorage, applied via `dark` class on `<html>`; toggle in sidebar
- [x] Unraid Community Applications template XML (`unraid-template.xml`)
- [x] Unraid PUID/PGID support — `docker-entrypoint.sh` creates runtime user; `gosu` for privilege drop
- [x] API documentation page — links to Swagger UI (`/docs`) and ReDoc (`/redoc`) in Settings (admin)
- [x] Health check endpoint improvements — public `GET /api/health` endpoint; Dockerfile uses it
- [x] Rate limiting on auth endpoints — `slowapi`: login 20/min, verify-2fa 10/min
- [ ] Output directory configuration (read-only for now — controlled via Docker volume)
- [ ] Mobile/foldable phone layout polish (deferred — needs physical device testing)

---

## Phase 5 — Liberate View, My Books & Per-User Permissions ✅

### Sidebar additions ✅
- [x] **Liberate** (`/liberate`) added as primary nav item and default view
- [x] My Books and Library tabs later removed as redundant — Liberate covers both use cases

### Liberate View (`/liberate`) ✅
- [x] All books from LibationData.db in the same grid layout as Library
- [x] Status badge overlay on bottom-left of each cover:
  - Green ✓ = `UserDefinedItem.BookStatus = 1` (liberated/downloaded)
  - Red ✕ = `BookStatus = 0` (not downloaded) or `2` (error)
  - Animated blue spinner = actively present in `downloads` table with `status = queued/running`
- [x] Filter tabs: All / Downloaded / Not Downloaded / In Progress
- [x] Download All (no-cap users) / Download Next N + auto-select + confirm queue (capped users)
- [x] Individual one-click download button on hover (subject to cap)
- [x] 2-second polling while any book is actively downloading
- [x] Backend: `GET /api/liberate/books` — JOIN `Books` + `UserDefinedItem` + active downloads overlay

### My Books (`/my-books`) ✅
- [x] Same grid/list UI as Library filtered to `LibraryBooks WHERE Account = ?`
- [x] User self-selects their Audible account from the connected accounts list (stored as `audible_account_id` on users row, set via `PATCH /api/auth/me`)
- [x] If no account linked, shows account-picker prompt
- [x] "Change account" button to unlink and re-pick

### Enhanced Metadata ✅
- [x] `UserDefinedItem.BookStatus` JOIN for liberate status per book
- [x] New fields: Subtitle, ContentType (mapped to string), Language, IsAbridged
- [x] "Abridged" badge on Liberate book tiles
- [x] Community ratings via `Rating` table JOIN (schema-discovery resilient)

### Per-User Permissions ✅
- [x] `permissions` JSON + `download_cap` INTEGER + `audible_account_id` TEXT added to `users` via startup migration
- [x] Flags: `can_download`, `can_scan`, `can_manage_accounts`, `can_liberate`, `can_remove_downloads`
- [x] Default for new non-admin users: all `true` except `can_remove_downloads = false`
- [x] Admins bypass all permission checks
- [x] Admin permission matrix in Settings — toggle per-flag per-user + download cap number input
- [x] `PATCH /api/users/{id}/permissions` enforces permissions on downloads, scan, delete, liberate

### Download Cap (12-hour rolling window) ✅
- [x] Rolling window: `COUNT(downloads WHERE user_id = ? AND created_at > NOW() − 12h)`
- [x] No cap → "Download All" button fires `POST /api/liberate/download-all`
- [x] Cap set, remaining > 0 → "Download Next N" auto-selects books; confirm queues each via `POST /api/downloads`
- [x] Cap exhausted → all download buttons disabled; reset time shown
- [x] Individual book downloads also decrement window; cap enforced server-side with 429 + `resets_at`
- [x] `download_cap = null` and `is_admin = true` both bypass entirely

---

## Phase 5 — Post-Release Bug Fix ✅
- [x] Root cause of "Error processing book. Skipping." (downloads completed instantly with no file written)
  - `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` caused `CultureInfo.GetCultures()` to return only Invariant Culture (ID 0x7F); `new RegionInfo(c)` throws `ArgumentException` inside `LocaleDto.GetRegion()` → propagates through `DownloadOptions..ctor` → `BuildDownloadOptions` → `DownloadDecryptBook.ProcessAsync`
  - Fix: removed `ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` from Dockerfile; added `libicu76` to apt-get install
- [x] Per-book license pipeline: `_fetch_license(asin)` → `get-license <asin>` piped to `liberate -l -` for accounts with `DecryptKey: null` (no local activation bytes); 16-byte ADRM key pairs from Audible used directly
- [x] Rebuilt and redeployed image; downloads confirmed working end-to-end

---

## Phase 5 — Extended UX (continued) ✅

### User Management enhancements ✅
- [x] Inline **Owner Name** field per user row in Settings > User Management (saves on blur/Enter)
- [x] **Audible Account** dropdown per user row — links `users.audible_account_id` to a connected account so the Accounts page owner display is correct

### Liberate Page ✅
- [x] **Owner filter tabs** — one tab per owner (using `owner_name` + `audible_account_id`), filters books by `LibraryBooks.Account`; search and account_id combined in a WHERE clause on the backend
- [x] **Centered search bar** with 300ms debounce; passes `search` param to `GET /api/liberate/books`
- [x] **Mark as Downloaded / Not Downloaded** directly from the UI — `PATCH /api/liberate/books/{book_id}` writes to `UserDefinedItem.BookStatus` in LibationData.db; INSERT if row doesn't exist (populates all NOT NULL cols)
- [x] **Multi Select mode** — checkbox overlay on each tile; Select All fetches all matching IDs via `GET /api/liberate/book-ids` (no pagination, spans all pages); bulk "Mark Downloaded" and "Mark Not Downloaded" actions
- [x] **Per-page selector** — PAGE_SIZES [24, 48, 96, 200]; placed in pagination row

### Accounts Page ✅
- [x] **Post-login banner** — after successful Audible login, shows brand-blue info banner: "Account connected — one more step! Go to Downloads and click Scan Library"; dismissable with ✕

### Navigation ✅
- [x] **Liberate moved to top** of sidebar nav and made the **default view** (`/` → redirect `/liberate`)
- [x] **Library and My Books removed** from sidebar and routes; stale URLs redirect to `/liberate`

---

## Phase 6 — CLI Self-Update ~~✅~~ (removed — architecturally incompatible with LibationBridge)

> **Why removed:** Installing a new Libation `.deb` via `dpkg -i` replaces all DLLs in `/usr/lib/libation/` but leaves the LibationBridge binary unchanged. The bridge is a compiled binary built against a specific Libation version's type signatures; after a `.deb` swap it loads mismatched DLLs and fails at runtime with `MissingMethodException` or dies before `/health` responds (killing the container). The correct upgrade path is to bump `LIBATION_VERSION` in the Dockerfile and rebuild the image — the `bridge-builder` stage recompiles the bridge against the new DLLs automatically.
>
> **What was kept:** The `while true` restart loop in `docker-entrypoint.sh` (needed for crash recovery). The Settings page **About** card (read-only, shows installed CLI version via `GET /api/updates/version`).
>
> **Cleanup note for existing users:** If you previously used the update feature, you may have `.deb` files in `./config/updates/` (up to ~200 MB each). That directory is no longer used and can be deleted to reclaim disk space.

### Entrypoint restart loop ✅ (kept — crash recovery only)
- [x] `docker-entrypoint.sh` replaced `exec gosu uvicorn` with a `while true` restart loop
- [x] Crash path: 5-second delay before restarting both bridge and uvicorn to avoid tight loop
- [x] `SIGTERM` to container exits cleanly (no zombie processes)
- ~~[x] On each loop iteration: installs `/config/updates/pending` `.deb` as root via `dpkg -i`~~ (removed)
- ~~[x] Restart sentinel `/tmp/libation-restart` for planned restarts vs crash restarts~~ (removed)

### Update service (removed)
- ~~`GET /api/updates/status`, `POST /api/updates/check`, `POST /api/updates/install`, `POST /api/updates/rollback`~~ (removed)
- ~~`backend/app/services/update.py`~~ (deleted)
- `GET /api/updates/version` ✅ — read-only; returns installed CLI version from `libationcli --version`

### Settings — About section ✅ (simplified)
- [x] Read-only card visible to all users: installed CLI version
- ~~[x] Admin-only update/rollback controls~~ (removed)

---

## Phase 6 — Observability, Distribution & Branding ✅

### Persistent CLI logging ✅
- [x] `backend/app/services/logger.py` — `RotatingFileHandler` writing to `/config/logs/libation-web.log` (5MB × 3 files), so Unraid users get debuggable logs without SSH
- [x] `log_cli()` helper logs command, exit code, elapsed time, and output (truncated at 20KB)
- [x] Wired into `list_accounts`, `start_login`, `complete_login`, `run_scan`, `_fetch_license`, `run_liberate`
- [x] OAuth tokens/response URLs intentionally excluded from logs (security)
- [x] Startup/shutdown lifecycle events logged in `main.py`

### Log viewer (Settings page) ✅
- [x] `GET /api/logs` (admin only) — tail with line-count and level filters; `GET /api/logs/download` for raw file download
- [x] Embedded as a Card in Settings below the About section (visible only to `is_admin`) — not a separate route/sidebar tab
- [x] Level filter tabs, line count selector, auto-refresh toggle, color-coded log lines

### React resilience ✅
- [x] Root `ErrorBoundary` component wraps the whole app — catches render crashes instead of leaving a blank white page with a dead back button

### Docker Hub publishing ✅
- [x] Image published to Docker Hub (`jtechguru1993/libation-web:latest`) on every push
- [x] README and Unraid template updated to reference Docker Hub

### Branding fix ✅
- [x] Replaced default favicon and sidebar headphones icon with official Libation branding (`libation_logo_dark.svg` for sidebar, `favicon.svg` + `libation.ico` fallback for browser tab)
- [x] Root-cause fix: `spa_fallback` route in `main.py` was returning `index.html` for every static asset not under `/assets` (favicons, logos, etc.) — now checks if the file exists on disk first
- [x] Unraid template `<Icon>` URL fixed (was pointing at a 404'd `libation-square.png`; now points at the actual colored Libation app icon)

---

## Phase 7 — LibationBridge Sidecar ✅

Replaced `libationcli` subprocess calls for downloads and scans with a LibationBridge ASP.NET Core 10 sidecar that references Libation DLLs directly. Gives real `StreamingProgressChanged` events (0–100%) instead of fake 5/95 progress jumps from stdout parsing, and eliminates per-download subprocess overhead.

### Bridge binary ✅
- [x] `libation-bridge/` — new C# minimal API project (ASP.NET Core 10); built as self-contained single-file binary placed at `/usr/lib/libation/libation-bridge` (symlinked from `/usr/local/bin/libation-bridge`)
- [x] References Libation DLLs at `/usr/lib/libation/` via `<Reference>` with `<Private>false</Private>` — DLLs not bundled into binary, loaded at runtime via `AssemblyResolve` hook
- [x] `AssemblyResolve` hook registered before any Libation type is touched; all Libation code isolated in `static class LibationBridgeApp` with `[MethodImpl(MethodImplOptions.NoInlining)]` to prevent JIT resolving DLLs before hook fires
- [x] Libation scaffolding called via `AppScaffolding.LibationScaffolding.RunPreConfigMigrations()` + `RunPostConfigMigrations()` + `RunPostMigrationScaffolding()` at startup
- [x] Config path fix: `Directory.SetCurrentDirectory("/config")` in `Program.cs`; entrypoint pre-seeds `/config/Libation/appsettings.json` with `{"LibationFiles":"/config"}` so Libation's bootstrap discovery finds `/config` as its files dir — the same path `libationcli --libationFiles /config` uses, meaning both share `/config/LibationContext.db`

### Bridge API surface ✅
- [x] `GET /health` — readiness probe (`{"status":"ok"}`)
- [x] `GET /debug` — diagnostic: DB path + book count + sample ASINs
- [x] `GET /accounts` — shim over `libationcli list-accounts --bare`; returns parsed tab-separated account list
- [x] `POST /scan` — synchronous: holds connection until `libationcli scan` exits; returns `{"exit_code","output"}`; Kestrel keepalive set to 12 min
- [x] `POST /download/{asin}` — 202 immediately; starts `DownloadDecryptBook.ProcessAsync(book)` in background Task; `StreamingProgressChanged` handler updates in-memory `_progress[asin]`
- [x] `GET /progress/{asin}` — returns `{"asin","progress","status","output"}` or 404
- [x] `POST /download-all` — 202 immediately; fires `libationcli liberate --force` in background Task
- [x] Completed progress entries expire after 1 hour via background cleanup Task

### Dockerfile ✅
- [x] `bridge-builder` stage (Stage 2, between frontend-builder and runtime): installs Libation `.deb` so MSBuild can resolve `<HintPath>/usr/lib/libation/*.dll>` at compile time; builds self-contained single-file binary with `dotnet publish -r linux-x64 --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=false`; arch auto-detected (`amd64`/`arm64`)
- [x] Runtime stage copies binary to `/usr/lib/libation/libation-bridge`; symlinked to `/usr/local/bin/libation-bridge`

### Entrypoint integration ✅
- [x] Bridge starts before uvicorn on each loop iteration; `wait_for_bridge()` polls `GET /health` up to 30s
- [x] Both `BRIDGE_PID` and `UVICORN_PID` tracked; bridge killed when uvicorn exits and restarted on next iteration
- [x] `cleanup()` SIGTERM handler kills both processes
- [x] Entrypoint pre-seeds `/config/Libation/appsettings.json` with correct files dir every startup

### Python integration ✅
- [x] `BRIDGE_URL = "http://localhost:8001"` added to `backend/app/config.py`
- [x] `backend/app/services/cli.py`: `run_liberate()` → bridge `POST /download/{asin}` + poll `GET /progress/{asin}` every 2s; `run_scan()` → bridge `POST /scan`; `list_accounts()` → bridge `GET /accounts`
- [x] Login flow (`start_login` / `complete_login`) stays as PTY subprocess — `libationcli login-external` requires PTY allocation

### Accounts ✅
- [x] `DELETE /api/accounts/{account_id}` — directly edits `/config/AccountsSettings.json`, filters out the matching entry, writes back; resolves "account removal not implemented" from Phase 3

---

---

## Phase 8 — Operational Hardening ✅
- [x] Per-Audible-account auto-download toggle — `audible_account_settings.auto_download`; `_auto_download_if_enabled()` fires after every successful scan with 30-min global cooldown via `system_settings.last_auto_download_at`
- [x] Default-credentials detection — `GET /api/auth/default-credentials`; SettingsPage shows amber warning banner + `UpdateCredentialsSection` (change username + password in one step, signs out after)
- [x] `POST /api/auth/change-username` endpoint added
- [x] OAuth flow auto-triggers library scan + dismissable info banner on completion
- [x] Logs API — `GET /api/logs` (admin, filterable by level, up to 2000 lines) + `GET /api/logs/download`
- [x] `LogsSection` embedded in Settings — dark monospace viewer, level filter tabs, line count selector, auto-refresh, download button
- [x] `ApiDocsSection` in Settings — links to `/docs` (Swagger) and `/redoc`
- [x] Sidebar nav renamed "Accounts" → "Audible Accounts"
- [x] `UserAdminResponse.created_at` made Optional (handles NULL from early-seeded users)
- [x] `AccountResponse` gains `auto_download` and `added_by_user_id` fields

---

## Phase 8 Extended ✅
- [x] **Owner name input on Accounts page** — inline editable input in Owner column for accounts the logged-in user added; saves via `PATCH /api/auth/me` on blur/Enter; placeholder "Fill in your first name"
- [x] **Amber banner on Accounts page** — shown when current user has added an account but `owner_name` is unset; text: "Fill in owner name to use split libraries."
- [x] **Purchased filter tab on Liberate page** — between All and Audible Plus; filters `LibraryBooks.IsAudiblePlus=0` in both `get_liberate_books()` and `get_liberate_book_ids()`

---

## Future (not built)
- [ ] Cap usage banner: "N / cap downloads used · Resets in Xh Ym" (use `resets_at` from 429 response)
