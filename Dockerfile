# ── Stage 1: Build frontend ──────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Build LibationBridge ────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS bridge-builder

ARG LIBATION_VERSION=13.4.9

# Install deps needed to run the Libation .deb post-install scripts
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        ca-certificates \
        libgtk-3-bin \
        libglib2.0-0 \
        libfontconfig1 \
        libx11-6 \
    && rm -rf /var/lib/apt/lists/*

# Install the Libation .deb so MSBuild can resolve <HintPath>/usr/lib/libation/*.dll
RUN ARCH=$(dpkg --print-architecture) && \
    wget -q -O /tmp/libation.deb \
        "https://github.com/rmcrackan/Libation/releases/download/v${LIBATION_VERSION}/Libation.${LIBATION_VERSION}-linux-chardonnay-${ARCH}.deb" && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/libation.deb && \
    rm -f /tmp/libation.deb && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /bridge
COPY libation-bridge/ ./

# Build self-contained single-file binary; arch matched to host at build time
RUN ARCH=$(dpkg --print-architecture) && \
    DOTNET_ARCH=$([ "$ARCH" = "arm64" ] && echo "linux-arm64" || echo "linux-x64") && \
    dotnet publish -c Release \
        -r "$DOTNET_ARCH" \
        --self-contained true \
        -p:PublishSingleFile=true \
        -p:PublishTrimmed=false \
        -o /bridge/out

# ── Stage 3: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim

ARG LIBATION_VERSION=13.4.9

WORKDIR /app

# System dependencies for Libation CLI + PUID/PGID support
# libicu76 is required by .NET 10 for globalization (CultureInfo/RegionInfo used in DownloadOptions)
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        ca-certificates \
        libgtk-3-bin \
        libglib2.0-0 \
        libfontconfig1 \
        libx11-6 \
        gosu \
        ffmpeg \
        libicu76 \
    && rm -rf /var/lib/apt/lists/*

# Install LibationCli from .deb (self-contained, includes .NET runtime)
# Pre-create sysctl.conf so the .deb post-install script doesn't fail inside Docker
RUN ARCH=$(dpkg --print-architecture) && \
    wget -q -O /tmp/libation.deb \
        "https://github.com/rmcrackan/Libation/releases/download/v${LIBATION_VERSION}/Libation.${LIBATION_VERSION}-linux-chardonnay-${ARCH}.deb" && \
    echo 'fs.inotify.max_user_instances=524288' > /etc/sysctl.conf && \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/libation.deb && \
    rm -f /tmp/libation.deb && \
    rm -rf /var/lib/apt/lists/*

# LibationBridge binary — placed in /usr/lib/libation/ so the .NET single-file host
# probes that directory for the sibling Libation DLLs (AppScaffolding.dll, etc.)
COPY --from=bridge-builder /bridge/out/LibationBridge /usr/lib/libation/libation-bridge
RUN chmod +x /usr/lib/libation/libation-bridge && \
    ln -s /usr/lib/libation/libation-bridge /usr/local/bin/libation-bridge

# Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/app ./app

# Built frontend (served as static files by FastAPI)
COPY --from=frontend-builder /frontend/dist ./static

# Entrypoint script for PUID/PGID support
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Runtime directories
RUN mkdir -p /data /config /audiobooks

ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD wget -qO- http://localhost:8000/api/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
