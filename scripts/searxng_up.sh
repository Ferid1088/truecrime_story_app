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
new_pid=$!
ready=0
for _ in {1..40}; do
  if ! kill -0 "$new_pid" 2>/dev/null; then break; fi
  if curl -fsS 'http://127.0.0.1:8085/search?q=health&format=json' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done
if [[ "$ready" != 1 ]]; then
  kill "$new_pid" 2>/dev/null || true
  wait "$new_pid" 2>/dev/null || true
  [[ -f "$PIDFILE" ]] && ! kill -0 "$(<"$PIDFILE")" 2>/dev/null && rm -f "$PIDFILE"
  fail "SearXNG did not become ready; its pidfile was not recorded."
fi
echo "$new_pid" > "$PIDFILE"
echo "SearXNG started (pid $(<"$PIDFILE"))."
