# Libation Web UI — Docker

A Dockerized web UI wrapper for [Libation](https://github.com/rmcrackan/Libation) by [@rmcrackan](https://github.com/rmcrackan).

> **Attribution:** This project depends entirely on [LibationCli](https://github.com/rmcrackan/Libation), the headless CLI companion to Libation. All audiobook management, Audible authentication, library scanning, and DRM decryption is performed by LibationCli. This repository adds only a web interface on top of it.

---

## What this adds

- REST API (FastAPI/Python) wrapping LibationCli
- React web frontend — library grid/list, search, sort, book detail
- User authentication with 2FA (TOTP) and 60-day persistent sessions
- Audible account management via `login-external` OAuth flow
- Library scan and per-book download queue with progress tracking
- Single Docker container — runs on Unraid, Synology, or any Docker host

## Quick start

```bash
# Clone and configure
cp .env.example .env
# Edit .env — set a strong SECRET_KEY and your admin credentials

# Start
docker compose up -d
```

Open `http://localhost:8000` and log in with your configured credentials.

## Volume layout

| Host path      | Container path | Purpose                              |
|----------------|---------------|--------------------------------------|
| `./data`       | `/data`       | App database (users, sessions)       |
| `./config`     | `/config`     | Libation config + `LibationData.db`  |
| `./audiobooks` | `/audiobooks` | Downloaded audiobook files           |

## Adding an Audible account

1. Go to **Accounts** → **Add Account**
2. Enter your Audible email and marketplace
3. Copy the login URL → open it in your browser and sign in
4. Paste the response URL back → account is connected
5. Go to **Downloads** → **Scan Library** to import your books

## Environment variables

| Variable                     | Default      | Description                          |
|------------------------------|-------------|--------------------------------------|
| `SECRET_KEY`                 | (random)    | JWT signing key — **change this**    |
| `ADMIN_USERNAME`             | `admin`     | Initial admin username               |
| `ADMIN_PASSWORD`             | `admin`     | Initial admin password — change on first login |
| `ACCESS_TOKEN_EXPIRE_MINUTES`| `15`        | Access token lifetime                |
| `REFRESH_TOKEN_EXPIRE_DAYS`  | `60`        | Refresh token / session lifetime     |

## Credits

- **[Libation](https://github.com/rmcrackan/Libation)** by [@rmcrackan](https://github.com/rmcrackan) — the audiobook manager this wraps
- This Docker web UI is an independent community project, not affiliated with or endorsed by the original Libation project
