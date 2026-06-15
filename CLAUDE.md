# Libation Web UI — CLAUDE.md

## Project overview
A Dockerized web application that wraps the LibationCli audiobook manager with a professional, mobile-responsive web UI. Built in phases.

## Architecture

### Single-container deployment
- **Backend**: Python 3.12 + FastAPI, served on port 8000
- **Frontend**: React 18 + Vite + Tailwind CSS, built to `/app/static` and served as static files by FastAPI
- **LibationCli**: Installed from the official `.deb` (`/usr/bin/libationcli`)

### Volume layout
| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `./data` | `/data` | App SQLite DB (`app.db`), session store |
| `./config` | `/config` | Libation config, `appsettings.json`, `LibationData.db` |
| `./audiobooks` | `/audiobooks` | Downloaded audiobooks |

### Key paths
- Backend entry: `backend/app/main.py`
- Auth API: `backend/app/api/auth.py`
- Auth service: `backend/app/services/auth.py`
- Frontend entry: `frontend/src/main.tsx`
- Auth context: `frontend/src/context/AuthContext.tsx`

## Auth system
- **Access token**: 15-min JWT in response body, stored in memory (React context)
- **Refresh token**: 60-day JWT in httpOnly cookie (`/api/auth` path), hashed in `sessions` table
- **2FA**: TOTP via `pyotp`, optional per user, toggled in Settings
- **Session persistence**: On page load, silently calls `/api/auth/refresh` using the cookie
- **Auto-refresh**: Timer in `AuthContext` refreshes access token 2 min before expiry

## Database (SQLite at `/data/app.db`)
- `users`: id, username, hashed_password (bcrypt), totp_secret, totp_enabled, is_active, created_at
- `sessions`: id, user_id, refresh_token_hash (sha256), expires_at, created_at, last_used_at

## Default credentials
Set via env vars `ADMIN_USERNAME` / `ADMIN_PASSWORD` (defaults: `admin` / `admin`).
Admin user is seeded on first startup if no users exist.

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
- Reads Libation's `LibationData.db` at `{LIBATION_CONFIG}/LibationData.db`
- Uses schema discovery (`PRAGMA table_info`) so it handles column name variations across Libation versions
- Returns `empty_reason: "no_accounts"` when no DB exists (user hasn't connected Audible yet)
- Authors/narrators via `BookContributors` + `Contributors`/`Persons` junction (contributor type 0=author, 1=narrator)
- Series via `BookSeries` + `Series` junction
- Cover paths stored in `PictureLarge` column; served via `GET /api/library/covers/{book_id}` (no auth required — images are not sensitive)

## CLI service (`backend/app/services/cli.py`)
- All commands get `--libationFiles /config` so Libation reads/writes the correct config dir
- `login-external` subprocess is kept alive in `_PENDING_LOGINS` dict (keyed by UUID) between the two login steps; auto-expires after 10 min
- Download progress parsed from `\d+%` patterns in `liberate` stdout
- `list-accounts --bare` returns tab-separated: account_id, name, locale, scan_library, authenticated

## Downloads & Scan (`backend/app/api/downloads.py`)
- `POST /api/downloads/scan` creates a `Scan` row, fires `asyncio.create_task` to run `libationcli scan`
- `POST /api/downloads` creates a `Download` row with `user_id`, fires task to run `libationcli liberate --id {asin}`
- Background tasks update DB rows as progress changes; frontend polls `/api/downloads` every 2s
- Duplicate active downloads blocked with 409

## Phase history
- **Phase 1** (complete): Project foundation, Docker setup, full auth system (login, 2FA, 60-day sessions, change password), React UI shell with sidebar navigation.
- **Phase 2** (complete): Library view — reads Libation SQLite DB, grid/list book view with cover art, search, sort, pagination, book detail slide-over, empty states.
- **Phase 3** (complete): Accounts & Downloads — add Audible accounts via `login-external` OAuth flow, library scan, per-book downloads via `liberate`, downloads page with progress polling, download button on book cards.

## Conventions
- API routes: `/api/<resource>/<action>`
- All API responses use snake_case JSON
- Frontend uses `@/` alias for `frontend/src/`
- No ads, no telemetry, no external dependencies at runtime
