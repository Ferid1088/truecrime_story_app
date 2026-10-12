#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/searxng"
SETTINGS="$STATE/settings.yml"
TEMPLATE="$ROOT/config/searxng/settings.yml.template"
PIDFILE="$STATE/searxng.pid"
LOGFILE="$STATE/searxng.log"
RUNNER="${SEARXNG_RUNNER:-}"

fail() { echo "SearXNG: $*" >&2; exit 1; }
mkdir -p "$STATE"

if [[ ! -f "$SETTINGS" ]]; then
  command -v openssl >/dev/null 2>&1 || fail "openssl is required to create the local secret."
  secret="$(openssl rand -hex 32)"
  sed "s/__TRUECRIME_SEARXNG_SECRET__/$secret/" "$TEMPLATE" > "$SETTINGS"
  chmod 600 "$SETTINGS"
fi

if [[ -z "$RUNNER" ]]; then
  if [[ -n "${SEARXNG_DIR:-}" && -x "${SEARXNG_DIR}/.venv/bin/searxng-run" ]]; then
    RUNNER="${SEARXNG_DIR}/.venv/bin/searxng-run"
  elif command -v searxng-run >/dev/null 2>&1; then
    RUNNER="$(command -v searxng-run)"
  fi
fi
[[ -n "$RUNNER" ]] || fail "native SearXNG is not installed; set SEARXNG_DIR or SEARXNG_RUNNER."

if [[ -f "$PIDFILE" ]] && kill -0 "$(<"$PIDFILE")" 2>/dev/null; then
  echo "SearXNG already running (pid $(<"$PIDFILE"))."
  exit 0
fi

TRUECRIME_SEARXNG_URL="http://127.0.0.1:8085" \
SEARXNG_SETTINGS_PATH="$SETTINGS" \
nohup "$RUNNER" >"$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
echo "SearXNG started (pid $(<"$PIDFILE")); readiness is checked by dev_up.sh."
