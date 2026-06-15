#!/bin/bash
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ "$PUID" != "0" ]; then
  # Ensure group exists with the right GID
  if getent group libation > /dev/null 2>&1; then
    groupmod -g "$PGID" libation 2>/dev/null || true
  else
    groupadd -g "$PGID" libation
  fi

  # Ensure user exists with the right UID
  if getent passwd libation > /dev/null 2>&1; then
    usermod -u "$PUID" -g "$PGID" libation 2>/dev/null || true
  else
    useradd -u "$PUID" -g "$PGID" -s /bin/bash -M libation
  fi

  # Fix ownership of runtime directories
  chown -R libation:libation /data /config /audiobooks /app 2>/dev/null || true

  # Bootstrap LibationCli settings on first run
  if [ ! -f /config/Settings.json ]; then
    echo '[Libation] Creating default Settings.json for LibationCli'
    echo '{"Books": "/audiobooks"}' > /config/Settings.json
    chown libation:libation /config/Settings.json
  fi

  echo "[Libation] Running as UID=$PUID GID=$PGID"
  exec gosu libation uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
else
  # Bootstrap LibationCli settings on first run
  if [ ! -f /config/Settings.json ]; then
    echo '[Libation] Creating default Settings.json for LibationCli'
    echo '{"Books": "/audiobooks"}' > /config/Settings.json
  fi

  echo "[Libation] Running as root"
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
fi
