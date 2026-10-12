#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/searxng"
SETTINGS="$STATE/settings.yml"
TEMPLATE="$ROOT/config/searxng/settings.yml.template"
PIDFILE="$STATE/searxng.pid"
LOGFILE="$STATE/searxng.log"
RUNNER="${SEARXNG_RUNNER:-}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Usage: scripts/searxng_up.sh

Start native SearXNG on the configured local URL. The first run creates
data/searxng/settings.yml with a local secret. Use --help for this text.
EOF
  exit 0
fi
[[ -z "${1:-}" ]] || { echo "Usage: scripts/searxng_up.sh" >&2; exit 2; }

fail() { echo "SearXNG: $*" >&2; exit 1; }
mkdir -p "$STATE"
[[ -f "$TEMPLATE" ]] || fail "settings template is missing: $TEMPLATE"

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
[[ -x "$RUNNER" ]] || fail "SearXNG runner is not executable: $RUNNER"

if [[ -f "$PIDFILE" ]] && kill -0 "$(<"$PIDFILE")" 2>/dev/null; then
  echo "SearXNG already running (pid $(<"$PIDFILE"))."
  exit 0
fi

TRUECRIME_SEARXNG_URL="http://127.0.0.1:8085" \
SEARXNG_SETTINGS_PATH="$SETTINGS" \
nohup "$RUNNER" >"$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
echo "SearXNG started (pid $(<"$PIDFILE")); readiness is checked by dev_up.sh."
