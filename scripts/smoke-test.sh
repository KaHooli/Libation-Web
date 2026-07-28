#!/usr/bin/env bash
#
# End-to-end smoke test for the Audible sign-in URL generation.
#
# Boots the built image, logs in, and asks the API for a login URL for every
# marketplace in the UI dropdown, asserting each one resolves to the correct
# Amazon domain.
#
# This exists because of a real bug: the dropdown sent ISO country codes ("au")
# while LibationCli's Localization.Get() matches on Locale.Name ("australia").
# On a miss it silently returns Locale.Empty instead of throwing, so the empty
# TopDomain interpolated into the template and produced a dead URL —
# "https://www.amazon./ap/signin?...". Nine of eleven marketplaces were broken
# and nothing failed loudly. Only "us" and "uk" worked, because they are the
# only locales whose Name happens to equal their country code.
#
# No credentials are required: login-external prints the sign-in URL before any
# authentication happens.
#
# Usage: scripts/smoke-test.sh <image-ref>

set -euo pipefail

IMAGE="${1:?usage: scripts/smoke-test.sh <image-ref>}"
NAME="libation-smoke-$$"
PORT="${SMOKE_PORT:-18000}"
BASE="http://127.0.0.1:${PORT}/api"

pass=0
fail=0

cleanup() {
  if [ "${KEEP_CONTAINER:-0}" != "1" ]; then
    docker rm -f "$NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }

echo "==> Starting $IMAGE"
docker run -d --name "$NAME" -p "${PORT}:8000" \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=admin \
  -e SECRET_KEY=smoke-test-only-not-a-real-secret \
  "$IMAGE" >/dev/null

echo "==> Waiting for /api/health"
for i in $(seq 1 90); do
  if curl -fsS "${BASE}/health" >/dev/null 2>&1; then
    echo "    up after ${i}s"
    break
  fi
  if [ "$i" -eq 90 ]; then
    echo "container never became healthy; last 100 log lines:" >&2
    docker logs --tail 100 "$NAME" >&2
    exit 1
  fi
  sleep 1
done

echo "==> Authenticating"
TOKEN=$(curl -fsS -X POST "${BASE}/auth/login" \
  -H 'content-type: application/json' \
  -d '{"username":"admin","password":"admin"}' | jq -r '.access_token')

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
  echo "could not obtain an access token" >&2
  exit 1
fi

# locale name -> Amazon/Audible top-level domain, per AudibleApi's locale table.
# The locale name is deliberately NOT the ISO country code; that conflation was
# the original bug.
LOCALES="
us:com
uk:co.uk
australia:com.au
brazil:com.br
canada:ca
france:fr
germany:de
india:in
italy:it
japan:co.jp
spain:es
"

echo "==> Checking login URLs"
for entry in $LOCALES; do
  locale="${entry%%:*}"
  domain="${entry##*:}"

  url=$(curl -fsS -X POST "${BASE}/accounts/login/start" \
    -H "authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d "{\"email\":\"smoke-${locale}@example.invalid\",\"locale\":\"${locale}\"}" \
    | jq -r '.login_url' 2>/dev/null) || url=""

  if [ -z "$url" ] || [ "$url" = "null" ]; then
    bad "$locale — no login URL returned"
    continue
  fi

  # The actual regression: an unresolved locale yields an empty domain.
  case "$url" in
    "https://www.amazon./"*)
      bad "$locale — empty Amazon domain (Locale.Empty): $url"
      continue
      ;;
  esac

  case "$url" in
    "https://www.amazon.${domain}/ap/signin?"*)
      ;;
    *)
      bad "$locale — expected host www.amazon.${domain}, got: ${url%%\?*}"
      continue
      ;;
  esac

  # assoc_handle is the second field that came back blank. Assert it carries a
  # country suffix rather than pinning an exact value.
  handle=$(printf '%s' "$url" \
    | sed -n 's/.*openid\.assoc_handle=\([^&]*\).*/\1/p')
  case "$handle" in
    amzn_audible_android_aui_?*)
      ok "$locale -> www.amazon.${domain} (assoc_handle=${handle})"
      ;;
    *)
      bad "$locale — bad or empty assoc_handle: '${handle}'"
      ;;
  esac
done

echo "==> Checking that bad locales are rejected"
for bad_locale in au de xx ""; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${BASE}/accounts/login/start" \
    -H "authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d "{\"email\":\"smoke@example.invalid\",\"locale\":\"${bad_locale}\"}")

  if [ "$code" = "400" ]; then
    ok "locale '${bad_locale}' rejected with 400"
  else
    bad "locale '${bad_locale}' should be rejected with 400, got ${code}"
  fi
done

echo
echo "==> ${pass} passed, ${fail} failed"

if [ "$fail" -gt 0 ]; then
  echo "--- container logs ---" >&2
  docker logs --tail 120 "$NAME" >&2
  exit 1
fi
