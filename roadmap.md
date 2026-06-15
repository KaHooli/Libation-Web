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
