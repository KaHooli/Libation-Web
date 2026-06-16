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
- [x] Note: account removal not implemented — LibationCli has no remove-account command

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

## Phase 6 — CLI Self-Update ✅

### Entrypoint restart loop ✅
- [x] `docker-entrypoint.sh` replaced `exec gosu uvicorn` with a `while true` restart loop
- [x] On each loop iteration: installs `/config/updates/pending` `.deb` as root via `dpkg -i` before starting uvicorn
- [x] Previous `.deb` saved to `/config/updates/rollback` before each update (enables one-step rollback)
- [x] Restart path: Python writes `/tmp/libation-restart` sentinel → SIGTERMs uvicorn → loop installs update → restarts
- [x] Crash path (no sentinel): 5-second delay before restart to avoid tight loop
- [x] `SIGTERM` to container exits cleanly (no zombie processes)

### Update service ✅
- [x] `GET /api/updates/status` — installed version (from `libationcli --version`), latest GitHub release, up-to-date flag, rollback availability
- [x] `POST /api/updates/check` — force-refresh GitHub API cache
- [x] `POST /api/updates/install` — admin only; downloads `linux-chardonnay-{arch}.deb` from GitHub to `/config/updates/`, stages pending file, triggers restart
- [x] `POST /api/updates/rollback` — admin only; re-stages previous `.deb` and triggers restart
- [x] GitHub Releases API (`rmcrackan/Libation`) cached in-process for 1 hour
- [x] Architecture auto-detected (`amd64` / `arm64`) via `platform.machine()`
- [x] `httpx` used for async GitHub API calls and streamed `.deb` download

### Settings — About section ✅
- [x] Visible to all users: installed CLI version, latest available version, release date, up-to-date status badge
- [x] Admin-only controls: **Check for updates**, **Update to vX.X.X**, **Roll back**, **Release notes** link
- [x] While restarting: branded reconnecting banner; frontend polls `/api/health` and auto-refreshes when server returns

---

## Phase 7 — Future (not built)
- [ ] Cap usage banner: "N / cap downloads used · Resets in Xh Ym" (use `resets_at` from 429 response)
