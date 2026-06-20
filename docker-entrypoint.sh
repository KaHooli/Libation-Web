#!/bin/bash
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
RESTART_SENTINEL="/tmp/libation-restart"
UPDATES_DIR="/config/updates"

# ── Install any pending CLI update (runs as root before privilege drop) ───────
install_pending_update() {
    [ -f "$UPDATES_DIR/pending" ] || return 0
    PENDING_DEB=$(cat "$UPDATES_DIR/pending")
    DEB_PATH="$UPDATES_DIR/$PENDING_DEB"
    if [ -f "$DEB_PATH" ]; then
        echo "[Libation] Installing pending update: $PENDING_DEB"
        if dpkg -i "$DEB_PATH"; then
            echo "[Libation] Update installed: $PENDING_DEB"
            rm -f "$UPDATES_DIR/pending"
            echo "$PENDING_DEB" > "$UPDATES_DIR/current"
        else
            echo "[Libation] Update failed — removing pending flag, keeping old binary"
            rm -f "$UPDATES_DIR/pending"
        fi
    else
        echo "[Libation] Pending deb not found: $DEB_PATH — skipping"
        rm -f "$UPDATES_DIR/pending"
    fi
}

# ── Wait for LibationBridge /health to respond ────────────────────────────────
wait_for_bridge() {
    echo "[Libation] Waiting for LibationBridge to be ready..."
    for i in $(seq 1 30); do
        if wget -qO- http://localhost:8001/health > /dev/null 2>&1; then
            echo "[Libation] LibationBridge is ready."
            return 0
        fi
        sleep 1
    done
    echo "[Libation] ERROR: LibationBridge did not become ready within 30s"
    return 1
}

# ── One-time setup ────────────────────────────────────────────────────────────
if [ "$PUID" != "0" ]; then
    if getent group libation > /dev/null 2>&1; then
        groupmod -g "$PGID" libation 2>/dev/null || true
    else
        groupadd -o -g "$PGID" libation
    fi

    if getent passwd libation > /dev/null 2>&1; then
        usermod -u "$PUID" -g "$PGID" libation 2>/dev/null || true
    else
        useradd -o -u "$PUID" -g "$PGID" -s /bin/bash -M libation
    fi

    mkdir -p /home/libation/.config
    ln -sfn /config /home/libation/.config/Libation
    chown libation:libation /home/libation

    if [ ! -f /config/Settings.json ]; then
        echo '[Libation] Creating default Settings.json for LibationCli'
        echo '{"Books": "/audiobooks"}' > /config/Settings.json
    fi

    chown -R libation:libation /data /config /audiobooks /app 2>/dev/null || true
    echo "[Libation] Running as UID=$PUID GID=$PGID"
    USE_GOSU=true
else
    if [ ! -f /config/Settings.json ]; then
        echo '[Libation] Creating default Settings.json for LibationCli'
        echo '{"Books": "/audiobooks"}' > /config/Settings.json
    fi
    echo "[Libation] Running as root"
    USE_GOSU=false
fi

# ── Restart loop ──────────────────────────────────────────────────────────────
SHOULD_EXIT=false
UVICORN_PID=""
BRIDGE_PID=""

cleanup() {
    SHOULD_EXIT=true
    [ -n "$BRIDGE_PID" ]  && kill "$BRIDGE_PID"  2>/dev/null || true
    [ -n "$UVICORN_PID" ] && kill "$UVICORN_PID" 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

while true; do
    install_pending_update

    # ── Bootstrap LibationBridge config dir ──────────────────────────────────
    # Libation reads {CWD}/Libation/appsettings.json to discover its files dir.
    # Bridge CWD is /config (set via Directory.SetCurrentDirectory in Program.cs),
    # so we pre-seed /config/Libation/appsettings.json to point it at /config directly
    # (same path that libationcli uses via --libationFiles /config).
    mkdir -p /config/Libation
    echo '{"LibationFiles":"/config"}' > /config/Libation/appsettings.json

    # ── Start LibationBridge ──────────────────────────────────────────────────
    if [ "$USE_GOSU" = true ]; then
        HOME=/home/libation gosu libation /usr/local/bin/libation-bridge &
    else
        HOME=/home/libation /usr/local/bin/libation-bridge &
    fi
    BRIDGE_PID=$!

    if ! wait_for_bridge; then
        echo "[Libation] Bridge failed to start — killing and exiting"
        kill "$BRIDGE_PID" 2>/dev/null || true
        BRIDGE_PID=""
        exit 1
    fi

    # ── Start uvicorn ─────────────────────────────────────────────────────────
    if [ "$USE_GOSU" = true ]; then
        gosu libation uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 &
    else
        uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 &
    fi
    UVICORN_PID=$!

    # Wait for uvicorn to exit
    wait "$UVICORN_PID" || true
    EXIT_CODE=$?
    UVICORN_PID=""

    # Kill bridge — it will be restarted on the next loop iteration
    kill "$BRIDGE_PID" 2>/dev/null || true
    BRIDGE_PID=""

    if [ "$SHOULD_EXIT" = true ]; then
        echo "[Libation] Shutting down."
        exit 0
    fi

    if [ -f "$RESTART_SENTINEL" ]; then
        rm -f "$RESTART_SENTINEL"
        echo "[Libation] Restarting for update..."
        sleep 1
    else
        echo "[Libation] uvicorn exited (code $EXIT_CODE) — restarting in 5s..."
        sleep 5
    fi
done
