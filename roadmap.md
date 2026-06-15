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

## Phase 4 — Settings & Polish
- [ ] Dashboard stat cards: total books, total downloads, connected accounts, **downloads per user** (sourced from `user_id` on downloads table added in Phase 3 — replaces the removed "Listening hours" card which was meaningless since Libation cannot play content)
- [ ] App settings: download format (mp3/m4b/flac), naming convention
- [ ] Libation settings passthrough (read/write `appsettings.json`)
- [ ] Output directory configuration
- [ ] Multiple user support (admin can add/remove users)
- [ ] Session management (view and revoke active sessions)
- [ ] Dark mode toggle
- [ ] Mobile/foldable phone layout polish
- [ ] Unraid Community Applications template XML
- [ ] Unraid template with PUID/PGID support
- [ ] API documentation page (Swagger/ReDoc link)
- [ ] Health check endpoint improvements
- [ ] Rate limiting on auth endpoints
