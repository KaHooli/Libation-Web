# Libation Web UI — Docker

A Dockerized web UI wrapper for [Libation](https://github.com/rmcrackan/Libation) by [@rmcrackan](https://github.com/rmcrackan).

> **Attribution:** This project depends entirely on [LibationCli](https://github.com/rmcrackan/Libation), the headless CLI companion to Libation. All audiobook management, Audible authentication, library scanning, and DRM decryption is performed by LibationCli. This repository adds only a web interface on top of it.

---

## Features

- **Liberate view** — default landing page; shows your full Audible library with download status overlays (downloaded ✓, not downloaded ✕, in-progress spinner)
- **Filter & search** — filter by status (All / Downloaded / Not Downloaded / In Progress / Audible Plus), search by title, filter by owner
- **Owner tabs** — link each web UI user to an Audible account; books are filterable by owner
- **Download management** — one-click download per book, Download All (uncapped), Download Next N (capped users); 2-second polling while downloads are active
- **Mark as downloaded** — manually set a book's status so LibationCLI treats it as already liberated
- **Multi-select** — select individual books or Select All (across all pages), then bulk Mark Downloaded / Mark Not Downloaded
- **Per-page selector** — choose 24 / 48 / 96 / 200 books per page
- **Accounts page** — add Audible accounts via `login-external` OAuth flow (3-step: form → copy URL → paste response)
- **Downloads page** — active queue with progress bars, failed/completed history, library scan trigger
- **Multi-user support** — admin can create/disable/delete users; per-user permission flags and 12-hour rolling download caps
- **User management** — set owner name and link each user to an Audible account
- **Settings** — Libation config passthrough, session management (list/revoke), 2FA setup, API docs
- **Auth** — JWT access tokens (15-min) + 60-day httpOnly refresh cookies, optional TOTP 2FA
- **Dark mode** — persisted to localStorage, toggled from the sidebar
- **Unraid-ready** — PUID/PGID support, Community Applications template included

---

## Quick start

```bash
# Clone and configure
cp .env.example .env
# Edit .env — set a strong SECRET_KEY and change the admin credentials

# Start
docker compose up -d
```

Open `http://localhost:8000` — log in with your configured credentials, then go to **Accounts** to connect your Audible account.

---

## Volume layout

| Host path      | Container path | Purpose                              |
|----------------|---------------|--------------------------------------|
| `./data`       | `/data`       | App database (users, sessions)       |
| `./config`     | `/config`     | Libation config + `LibationData.db`  |
| `./audiobooks` | `/audiobooks` | Downloaded audiobook files           |

---

## Adding an Audible account

1. Go to **Accounts** → **Add Account**
2. Enter your Audible email and marketplace
3. Copy the login URL → open it in your browser and sign in
4. Paste the response URL back → account is connected
5. A banner will prompt you to go to **Downloads** → **Scan Library** to import your books

---

## Environment variables

| Variable                      | Default      | Description                                        |
|-------------------------------|--------------|----------------------------------------------------|
| `SECRET_KEY`                  | (required)   | JWT signing key — **generate a strong random value** |
| `ADMIN_USERNAME`              | `admin`      | Initial admin username                             |
| `ADMIN_PASSWORD`              | `admin`      | Initial admin password — **change on first login** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15`         | Access token lifetime                              |
| `REFRESH_TOKEN_EXPIRE_DAYS`   | `60`         | Refresh token / session lifetime                   |
| `PUID`                        | `1000`       | User ID for file ownership (Unraid: 99)            |
| `PGID`                        | `1000`       | Group ID for file ownership (Unraid: 100)          |

Generate a strong `SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Credits

- **[Libation](https://github.com/rmcrackan/Libation)** by [@rmcrackan](https://github.com/rmcrackan) — the audiobook manager this wraps
- This Docker web UI is an independent community project, not affiliated with or endorsed by the original Libation project
