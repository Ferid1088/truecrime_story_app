#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/searxng"
SETTINGS="$STATE/settings.yml"
TEMPLATE="$ROOT/config/searxng/settings.yml.template"
PIDFILE="$STATE/searxng.pid"
LOGFILE="$STATE/searxng.log"
LOCAL_ENV="$ROOT/.env.local"
RUNNER="${SEARXNG_RUNNER:-}"
SEARCH_URL="${TRUECRIME_SEARXNG_URL:-http://127.0.0.1:8085}"

read_local_value() {
  local file="$1" key="$2" value
  [[ -f "$file" ]] || return 0
  value="$(grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true)"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'*\' && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

if [[ -z "${SEARXNG_DIR:-}" ]]; then
  SEARXNG_DIR="$(read_local_value "$LOCAL_ENV" SEARXNG_DIR)"
fi

if [[ "${1:-}" == "--set-dir" ]]; then
  [[ -n "${2:-}" && -d "$2" ]] || { echo "SearXNG directory does not exist: ${2:-}" >&2; exit 2; }
  dir="$(cd "$2" && pwd)"
  existing="$(grep -v '^SEARXNG_DIR=' "$LOCAL_ENV" 2>/dev/null || true)"
  mkdir -p "$(dirname "$LOCAL_ENV")"
  if [[ -n "$existing" ]]; then
    printf '%s\nSEARXNG_DIR=%s\n' "$existing" "$dir" > "$LOCAL_ENV"
  else
    printf 'SEARXNG_DIR=%s\n' "$dir" > "$LOCAL_ENV"
  fi
  chmod 600 "$LOCAL_ENV"
  echo "Saved SearXNG directory in .env.local (value not echoed)."
  exit 0
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Usage: scripts/searxng_up.sh

Start native SearXNG on the configured local URL. The first run creates
data/searxng/settings.yml with a local secret. Use --set-dir PATH to save the
native installation directory in the ignored .env.local file.
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
[[ -n "$RUNNER" ]] || fail "native SearXNG is not installed; set SEARXNG_DIR or run scripts/searxng_up.sh --set-dir PATH."
[[ -x "$RUNNER" ]] || fail "SearXNG runner is not executable: $RUNNER"

if [[ -f "$PIDFILE" ]] && kill -0 "$(<"$PIDFILE")" 2>/dev/null; then
  echo "SearXNG already running (pid $(<"$PIDFILE"))."
  exit 0
fi

TRUECRIME_SEARXNG_URL="$SEARCH_URL" \
SEARXNG_SETTINGS_PATH="$SETTINGS" \
nohup "$ROOT/.venv/bin/python" "$ROOT/scripts/detached_exec.py" "$RUNNER" \
  </dev/null >"$LOGFILE" 2>&1 &
new_pid=$!
ready=0
for _ in {1..40}; do
  if ! kill -0 "$new_pid" 2>/dev/null; then break; fi
  if curl -fsS --max-time 5 "$SEARCH_URL/search?q=test&format=json" >/dev/null 2>&1; then
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
