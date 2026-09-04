# Libation Web UI — Docker

A Dockerized web UI wrapper for [Libation](https://github.com/rmcrackan/Libation) by [@rmcrackan](https://github.com/rmcrackan).

> **Attribution:** This project depends entirely on [LibationCli](https://github.com/rmcrackan/Libation), the headless CLI companion to Libation. All audiobook management, Audible authentication, library scanning, and DRM decryption is performed by LibationCli. This repository adds only a web interface on top of it.

---

## Features

- **Liberate view** — default landing page; shows your full Audible library with download status overlays (downloaded ✓, not downloaded ✕, in-progress spinner)
- **Filter & search** — filter by status (All / Downloaded / Not Downloaded / In Progress / Purchased / Audible Plus), search by title, filter by owner
- **Owner tabs** — link each web UI user to an Audible account; books are filterable by owner
- **Download management** — one-click download per book, Download All (uncapped), Download Next N (capped users); real-time progress bars (0–100%) via LibationBridge; 2-second polling while downloads are active
- **Mark as downloaded** — manually set a book's status so LibationCLI treats it as already liberated
- **Multi-select** — select individual books or Select All (across all pages), then bulk Mark Downloaded / Mark Not Downloaded
- **Per-page selector** — choose 24 / 48 / 96 / 200 books per page
- **Accounts page** — add or remove Audible accounts via `login-external` OAuth flow (3-step: form → copy URL → paste response); inline owner name input for accounts you added; amber banner prompts you to set your owner name if unset
- **Downloads page** — active queue with progress bars, failed/completed history, library scan trigger
- **Multi-user support** — admin can create/disable/delete users; per-user permission flags and 12-hour rolling download caps
- **User management** — set owner name and link each user to an Audible account
- **Chaptarr import** — push downloaded audiobooks into a self-hosted [Chaptarr](https://github.com/Chaptarr/chaptarr) library, matched by Audible ASIN so they import even when Chaptarr isn't monitoring for them; automatic after every download, or on demand from the Liberate page
- **Skip what Chaptarr already has** — optionally check Chaptarr's library before pulling a book from Audible, so nothing is downloaded twice
- **Settings** — Libation config passthrough, session management (list/revoke), 2FA setup, API docs
- **Auth** — JWT access tokens (15-min) + 60-day httpOnly refresh cookies, optional TOTP 2FA
- **Dark mode** — persisted to localStorage, toggled from the sidebar
- **Unraid-ready** — PUID/PGID support, Community Applications template included

---

## Images

The image is published to two registries — use whichever you prefer:

| Registry | Image |
|----------|-------|
| Docker Hub | `jtechguru1993/libation-web:latest` |
| GitHub Container Registry | `ghcr.io/kahooli/libation-web:latest` |

Both are `linux/amd64` + `linux/arm64` multi-arch manifests. The GHCR image is
built and pushed automatically by
[`.github/workflows/docker-ghcr.yml`](.github/workflows/docker-ghcr.yml) on every
push to `main`, in addition to these tags:

| Tag | Produced by |
|-----|-------------|
| `latest` | every push to `main` |
| `main` | every push to `main` |
| `sha-<full-commit-sha>` | every push, for pinning an exact build |
| `1.2.3`, `1.2` | pushing a `v1.2.3` git tag |

> **The GHCR package is private.** Authenticate before pulling:
>
> ```bash
> echo "$GITHUB_PAT" | docker login ghcr.io -u KaHooli --password-stdin
> ```
>
> The PAT needs the `read:packages` scope. On Unraid, add the same registry
> credentials under **Docker → Add Container → Registry Authentication**.

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
| `./config`     | `/config`     | Libation config + `LibationContext.db`  |
| `./audiobooks` | `/audiobooks` | Downloaded audiobook files           |

---

## Database

SQLite by default — the file lives at `/data/app.db` on the `data` volume and
needs no configuration.

PostgreSQL is optional. Point `DATABASE_URL` at a server and the app uses it
instead:

```
DATABASE_URL=postgresql://libation:libation@postgres:5432/libation
```

`postgres://` and `postgresql+psycopg2://` are accepted too and normalised to
the bundled psycopg 3 driver. `docker-compose.yml` ships a commented-out
`postgres` service you can uncomment.

The schema is created and migrated automatically at startup, on either backend.
An existing SQLite database from an older version is adopted in place — its
tables are not rebuilt and no data is moved.

> Switching `DATABASE_URL` between backends points the app at a *different,
> empty* database; it does not copy your data across. Migrating an existing
> install means moving the rows yourself.

---

## Logging

All LibationCLI command output (scans, downloads, account logins) is written to a rotating log file on the `/config` volume, so it survives container restarts and is readable without SSH access — useful for debugging on Unraid.

| What | Where |
|------|-------|
| Log file (in container) | `/config/logs/libation-web.log` |
| Log file (Unraid host)  | `/mnt/user/appdata/libation/config/logs/libation-web.log` |
| In-app viewer | **Settings → Logs** (admin only) — filter by level, adjust line count, download raw file |

Rotates at 5MB, keeps 3 backups (`libation-web.log`, `.1`, `.2`, `.3`). OAuth tokens and login response URLs are never logged.

---

## Sending books to Chaptarr

[Chaptarr](https://github.com/Chaptarr/chaptarr) is a Readarr fork for audiobook and eBook
libraries. Libation Web UI can hand it each audiobook as soon as Libation finishes writing it.

**Setup**

1. Mount the same audiobooks volume into both containers. If the two mount it at different
   paths, fill in the path mapping in step 4.
2. In Chaptarr, add that path as a root folder (**Settings → Media Management → Root Folders**).
3. In Libation Web UI, go to **Settings → Chaptarr import**, enter Chaptarr's URL and its API key
   (Chaptarr → **Settings → General → API Key**), then hit **Test connection**.
4. Optionally set a path mapping — e.g. `/audiobooks` → `/books` — when the volume is mounted at
   different paths in the two containers. Leave both blank when the paths match.
5. Turn on **Import automatically after download**, or leave it off and push books by hand.

**How matching works**

Every Libation book carries its Audible ASIN. Chaptarr's canonical prefix for Amazon/Audible ids
is `az:`, so the ASIN is looked up directly — `GET /api/v1/book/lookup?term=az:{ASIN}` — and the
work, author and edition ids that come back are sent with the file to Chaptarr's `ManualImport`
command. Because the metadata is supplied deliberately rather than guessed, Chaptarr creates the
author and book from provider metadata if they aren't in the library yet; nothing has to be
monitored in advance.

If Chaptarr's metadata server doesn't know the ASIN, the containing folder is handed to
`DownloadedBooksScan` instead so Chaptarr can match it from tags and filenames.

Note that a **complete** result means Chaptarr ran the import, not that it necessarily accepted
the file — Chaptarr's `ManualImport` command reports success even when it rejects a file. If a book
doesn't turn up in Chaptarr's library, check Chaptarr's own **History** view for the reason.

**Pushing on demand**

On the Liberate page, switch on **Multi Select**, pick the books, and choose **Send to Chaptarr**.
Results appear under **Settings → Chaptarr import → Recent activity**.

**Not downloading what Chaptarr already has**

Turn on **Skip books Chaptarr already has** in the same settings card and every download —
a single book, an auto-download after a scan, or **Download All** — first asks Chaptarr whether
it already holds the book, and skips it if so. Two things can count as "already has it":

| Setting | Skips when |
|---------|-----------|
| **It has the file** (default) | Chaptarr holds an audiobook file for the book |
| **It's in the library** | Chaptarr knows the book at all, file or not |

Matching is by Audible ASIN against every id Chaptarr records for a book — the work id, the
edition ids, and each edition's ASIN — so no title guessing is involved. Only Chaptarr's
**audiobook** library counts: owning the eBook never stops the audiobook downloading.

If Chaptarr is unreachable the download goes ahead anyway — a metadata server being down is
not a reason to lose an audiobook. Every skip is listed under **Settings → Chaptarr import →
Recent activity**, and the download button on a skipped book offers **Download anyway**.

To see what Chaptarr has without changing anything, use **Multi Select → Check Chaptarr** on the
Liberate page; it reports the answer and badges the affected books whether or not skipping is on.

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
| `ADMIN_USERNAME`              | `admin`      | Initial admin username — only used on **first run** (before `app.db` exists); ignored after that |
| `ADMIN_PASSWORD`              | `admin`      | Initial admin password — only used on **first run**; to change after first run use Settings → Change Password |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15`         | Access token lifetime                              |
| `REFRESH_TOKEN_EXPIRE_DAYS`   | `60`         | Refresh token / session lifetime                   |
| `PUID`                        | `1000`       | User ID for file ownership (Unraid: 99)            |
| `PGID`                        | `1000`       | Group ID for file ownership (Unraid: 100)          |
| `DATABASE_URL`                | `sqlite:////data/app.db` | Where the app database lives. Leave unset for SQLite on the `/data` volume. To use PostgreSQL instead, set e.g. `postgresql://user:pass@host:5432/libation` — `postgres://` and `postgresql+psycopg2://` are accepted and normalised. The schema is created and migrated automatically on startup |

Generate a strong `SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Installing on Unraid

> **Note:** The image is published to Docker Hub as `jtechguru1993/libation-web:latest`
> and to GHCR as `ghcr.io/kahooli/libation-web:latest` (private — see
> [Images](#images) for the `docker login` step).

### Step 1 — Add the container

1. In the Unraid web UI go to the **Docker** tab
2. Click **Add Container**

Fill in these basic fields:

| Field | Value |
|-------|-------|
| Name | `LibationWeb` |
| Repository | `jtechguru1993/libation-web:latest` |
| Network Type | `Bridge` |
| Web UI | `http://[IP]:[PORT:8000]/` |

---

### Step 2 — Port mapping

Click **Add another Path, Port, Variable, Label or Device** → select **Port**

| Field | Value |
|-------|-------|
| Container Port | `8000` |
| Host Port | `8000` |
| Protocol | `TCP` |

---

### Step 3 — Volume paths

Add three paths via **Add another Path, Port, Variable, Label or Device** → **Path**:

**1. App database** (users, sessions, download history)

| Field | Value |
|-------|-------|
| Container Path | `/data` |
| Host Path | `/mnt/user/appdata/libation/data` |
| Access Mode | Read/Write |

**2. Libation config** (Audible account tokens, book database, **and app logs** — no separate path needed for logging, it lives at `/config/logs/libation-web.log` inside this same volume)

| Field | Value |
|-------|-------|
| Container Path | `/config` |
| Host Path | `/mnt/user/appdata/libation/config` |
| Access Mode | Read/Write |

**3. Audiobooks** (downloaded files)

| Field | Value |
|-------|-------|
| Container Path | `/audiobooks` |
| Host Path | `/mnt/user/audiobooks` |
| Access Mode | Read/Write |

> Change `/mnt/user/audiobooks` to match your actual media share path on Unraid.

---

### Step 4 — Environment variables

Add each via **Add another Path, Port, Variable, Label or Device** → **Variable**:

| Variable | Value | Notes |
|----------|-------|-------|
| `PUID` | `99` | Unraid's standard `nobody` user — leave as `99` |
| `PGID` | `100` | Unraid's standard `users` group — leave as `100` |
| `SECRET_KEY` | *(make one up — see below)* | **Required** |
| `ADMIN_USERNAME` | `admin` | Your admin login username |
| `ADMIN_PASSWORD` | *(strong password)* | **Do not leave as default** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Leave as default |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `60` | Leave as default |

**Setting your SECRET_KEY:**
Make up any random string of 32 or more characters and type it in. Letters, numbers, and symbols like `!@#%^&*-_+=` are all fine. Just avoid `"` (double quote), `$` (dollar sign), `\` (backslash), and spaces as these can cause issues in environment variables.

Example of a valid key: `k7m2p9q4n8r3t6w1x5y0z2a4b7c9d1e3`

> The SECRET_KEY signs your login tokens. It never needs to be remembered or typed again — just make it random and don't share it.

---

### Step 5 — Apply and open

Click **Apply**. Unraid will pull the image and start the container. Once it shows as running, open:

```
http://[your-unraid-ip]:8000
```

Log in with your `ADMIN_USERNAME` and `ADMIN_PASSWORD`.

---

### Step 6 — First-time setup

1. Go to **Accounts** → **Add Account** and connect your Audible account
2. After login completes a banner will appear — click through to **Downloads** → **Scan Library**
3. Once the scan finishes your books appear on the **Liberate** page ready to download

If something goes wrong, logs are at `/mnt/user/appdata/libation/config/logs/libation-web.log` on the host, or in-app under **Settings → Logs** — no SSH needed. See [Logging](#logging) above.

---

## Credits

- **[Libation](https://github.com/rmcrackan/Libation)** by [@rmcrackan](https://github.com/rmcrackan) — the audiobook manager this wraps
- This Docker web UI is an independent community project, not affiliated with or endorsed by the original Libation project
