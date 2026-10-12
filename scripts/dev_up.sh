#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/dev"
mkdir -p "$STATE"

usage() {
  cat <<'EOF'
Usage: scripts/dev_up.sh [status]

Start the native local services, or show their status. Use --help for this text.
EOF
}

case "${1:-}" in
  --help|-h) usage; exit 0 ;;
  status) ;;
  "") ;;
  *) usage >&2; exit 2 ;;
esac

# .env fills in TRUECRIME_API_PORT / TRUECRIME_FRONTEND_PORT if set there; an already
# exported value in the caller's shell is never overwritten.
if [[ -f "$ROOT/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^(TRUECRIME_API_PORT|TRUECRIME_FRONTEND_PORT)$ ]] || continue
    [[ -n "${!key:-}" ]] && continue
    export "$key=$value"
  done < <(grep -E '^(TRUECRIME_API_PORT|TRUECRIME_FRONTEND_PORT)=' "$ROOT/.env" || true)
fi
API_PORT="${TRUECRIME_API_PORT:-8001}"
FRONTEND_PORT_REQUESTED="${TRUECRIME_FRONTEND_PORT:-3001}"

_pidfile_for() { [[ "$1" == searxng ]] && echo "$ROOT/data/searxng/searxng.pid" || echo "$STATE/$1.pid"; }

status() {
  for name in searxng api worker frontend; do
    pidfile="$(_pidfile_for "$name")"
    if [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then
      echo "$name: running (pid $(<"$pidfile"))"
    else
      echo "$name: stopped"
    fi
  done
  api_shown="$API_PORT"
  frontend_shown="$([[ -f "$STATE/frontend.port" ]] && cat "$STATE/frontend.port" || echo "$FRONTEND_PORT_REQUESTED (not started)")"
  echo "api port: $api_shown, frontend port: $frontend_shown"
  if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "postgresql@16: ready"
  else
    echo "postgresql@16: not ready"
  fi
}

if [[ "${1:-}" == status ]]; then status; exit 0; fi
command -v brew >/dev/null 2>&1 || { echo "Homebrew is required." >&2; exit 1; }
command -v pg_isready >/dev/null 2>&1 || { echo "pg_isready is required; install postgresql@16." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }
[[ -x "$ROOT/.venv/bin/python" ]] || { echo "Create .venv and install requirements first." >&2; exit 1; }
[[ -x "$ROOT/frontend/node_modules/.bin/next" ]] || { echo "Run npm ci in frontend first." >&2; exit 1; }

_port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

_wait_for_port() {
  local pid="$1" port="$2"
  for _ in {1..40}; do
    kill -0 "$pid" 2>/dev/null || return 1
    if ! _port_free "$port"; then return 0; fi
    sleep 0.25
  done
  return 1
}

_wait_for_process() {
  local pid="$1"
  for _ in {1..40}; do
    kill -0 "$pid" 2>/dev/null || return 1
    sleep 0.25
  done
  kill -0 "$pid" 2>/dev/null
}

# The API port is fixed (the worker, the frontend and CORS all need to agree on it without
# re-discovery); the frontend port is a starting point only, moved forward if it's taken.
FRONTEND_PORT="$FRONTEND_PORT_REQUESTED"
while ! _port_free "$FRONTEND_PORT"; do
  FRONTEND_PORT=$((FRONTEND_PORT + 1))
  if [[ "$FRONTEND_PORT" -ge $((FRONTEND_PORT_REQUESTED + 20)) ]]; then
    echo "No free frontend port found near $FRONTEND_PORT_REQUESTED." >&2
    exit 1
  fi
done
if [[ "$FRONTEND_PORT" != "$FRONTEND_PORT_REQUESTED" ]]; then
  echo "Frontend port $FRONTEND_PORT_REQUESTED is taken; using $FRONTEND_PORT instead."
fi
echo "$FRONTEND_PORT" > "$STATE/frontend.port"

brew services start postgresql@16 >/dev/null
for _ in {1..30}; do pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && break; sleep 1; done
pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 || { echo "PostgreSQL did not become ready." >&2; exit 1; }

"$ROOT/scripts/searxng_up.sh"
for _ in {1..30}; do curl -fsS 'http://127.0.0.1:8085/search?q=health&format=json' >/dev/null 2>&1 && break; sleep 1; done
curl -fsS 'http://127.0.0.1:8085/search?q=health&format=json' >/dev/null 2>&1 || { echo "SearXNG did not become ready." >&2; exit 1; }

start_process() {
  local name="$1" readiness="$2" target="$3"; shift 3
  local pidfile="$STATE/$name.pid" new_pid
  if [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then return; fi
  nohup "$@" >"$STATE/$name.log" 2>&1 &
  new_pid=$!
  if [[ "$readiness" == port ]]; then
    _wait_for_port "$new_pid" "$target" || {
      kill "$new_pid" 2>/dev/null || true
      wait "$new_pid" 2>/dev/null || true
      [[ -f "$pidfile" ]] && ! kill -0 "$(<"$pidfile")" 2>/dev/null && rm -f "$pidfile"
      echo "$name did not become ready; its pidfile was not recorded." >&2
      return 1
    }
  else
    _wait_for_process "$new_pid" || {
      kill "$new_pid" 2>/dev/null || true
      wait "$new_pid" 2>/dev/null || true
      [[ -f "$pidfile" ]] && ! kill -0 "$(<"$pidfile")" 2>/dev/null && rm -f "$pidfile"
      echo "$name exited before readiness; its pidfile was not recorded." >&2
      return 1
    }
  fi
  echo "$new_pid" > "$pidfile"
}
start_process api port "$API_PORT" env TRUECRIME_JOB_RUNNER=external TRUECRIME_API_PORT="$API_PORT" TRUECRIME_FRONTEND_PORT="$FRONTEND_PORT" \
  "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT"
start_process worker process - env TRUECRIME_JOB_RUNNER=external "$ROOT/.venv/bin/python" -m app.worker
start_process frontend port "$FRONTEND_PORT" env NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:$API_PORT" \
  npm --prefix "$ROOT/frontend" run dev -- --hostname 127.0.0.1 --port "$FRONTEND_PORT"
echo "Local services started (api: $API_PORT, frontend: $FRONTEND_PORT). Run scripts/dev_up.sh status for readiness."
