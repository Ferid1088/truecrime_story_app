#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/dev"
mkdir -p "$STATE"

status() {
  for name in searxng api worker frontend; do
    pidfile="$STATE/$name.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then
      echo "$name: running (pid $(<"$pidfile"))"
    else
      echo "$name: stopped"
    fi
  done
  if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "postgresql@16: ready"
  else
    echo "postgresql@16: not ready"
  fi
}

if [[ "${1:-}" == status ]]; then status; exit 0; fi
[[ -z "${1:-}" ]] || { echo "usage: scripts/dev_up.sh [status]" >&2; exit 2; }
command -v brew >/dev/null 2>&1 || { echo "Homebrew is required." >&2; exit 1; }
command -v pg_isready >/dev/null 2>&1 || { echo "pg_isready is required; install postgresql@16." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }
[[ -x "$ROOT/.venv/bin/python" ]] || { echo "Create .venv and install requirements first." >&2; exit 1; }
[[ -x "$ROOT/frontend/node_modules/.bin/next" ]] || { echo "Run npm ci in frontend first." >&2; exit 1; }

brew services start postgresql@16 >/dev/null
for _ in {1..30}; do pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && break; sleep 1; done
pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 || { echo "PostgreSQL did not become ready." >&2; exit 1; }

"$ROOT/scripts/searxng_up.sh"
for _ in {1..30}; do curl -fsS 'http://127.0.0.1:8085/search?q=health&format=json' >/dev/null 2>&1 && break; sleep 1; done
curl -fsS 'http://127.0.0.1:8085/search?q=health&format=json' >/dev/null 2>&1 || { echo "SearXNG did not become ready." >&2; exit 1; }

start_process() {
  name="$1"; shift
  pidfile="$STATE/$name.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then return; fi
  nohup "$@" >"$STATE/$name.log" 2>&1 &
  echo $! > "$pidfile"
}
start_process api env TRUECRIME_JOB_RUNNER=external "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
start_process worker env TRUECRIME_JOB_RUNNER=external "$ROOT/.venv/bin/python" -m app.worker
start_process frontend npm --prefix "$ROOT/frontend" run dev -- --hostname 127.0.0.1 --port 3000
echo "Local services started. Run scripts/dev_up.sh status for readiness."
