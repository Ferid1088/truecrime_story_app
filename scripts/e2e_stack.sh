#!/usr/bin/env bash
set -euo pipefail

# Runs browser E2E against an app-owned disposable database and private ports.
# This script intentionally does not call dev_up/dev_down and never manages
# SearXNG.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env.local ]] && set -a && source .env.local && set +a

DB_NAME="truecrime_e2e"
API_PORT="${E2E_API_PORT:-8011}"
WEB_PORT="${E2E_WEB_PORT:-3011}"
DB_URL="${TRUECRIME_DATABASE_URL:?TRUECRIME_DATABASE_URL is required}"
export E2E_DB_NAME="$DB_NAME"
export E2E_DB_URL="$DB_URL"

"$ROOT/.venv/bin/python" - <<'PY'
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

source = make_url(os.environ["E2E_DB_URL"])
admin = os.environ.get("TRUECRIME_TEST_ADMIN_URL")
if not admin:
    user = os.environ.get("PGUSER") or os.environ.get("USER") or "postgres"
    admin = f"postgresql+psycopg:///{'postgres'}?user={user}"
admin_url = make_url(admin).set(database="postgres")
db_name = os.environ["E2E_DB_NAME"]
engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    conn.execute(text(f'CREATE DATABASE "{db_name}" OWNER "{source.username}"'))
engine.dispose()
print(f"created disposable database {db_name}")
PY

cleanup() {
  status=$?
  [[ "${CLEANED_UP:-0}" == 1 ]] && return
  CLEANED_UP=1
  set +e
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null
  wait "${FRONTEND_PID:-}" 2>/dev/null || true
  wait "${API_PID:-}" 2>/dev/null || true
  E2E_DB_URL="$DB_URL" E2E_DB_NAME="$DB_NAME" "$ROOT/.venv/bin/python" - <<'PY'
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
source = make_url(os.environ["E2E_DB_URL"])
admin = os.environ.get("TRUECRIME_TEST_ADMIN_URL")
if not admin:
    user = os.environ.get("PGUSER") or os.environ.get("USER") or "postgres"
    admin = f"postgresql+psycopg:///postgres?user={user}"
engine = create_engine(make_url(admin).set(database="postgres"), isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    conn.execute(text(f'DROP DATABASE IF EXISTS "{os.environ["E2E_DB_NAME"]}" WITH (FORCE)'))
engine.dispose()
print(f"dropped disposable database {os.environ['E2E_DB_NAME']}")
PY
  exit "$status"
}
trap cleanup EXIT INT TERM

E2E_DATABASE_URL="$("$ROOT/.venv/bin/python" - <<'PY'
import os
from sqlalchemy.engine import make_url
print(make_url(os.environ["E2E_DB_URL"]).set(database=os.environ["E2E_DB_NAME"]).render_as_string(hide_password=False))
PY
)"
export TRUECRIME_DATABASE_URL="$E2E_DATABASE_URL"
export DATABASE_URL="$E2E_DATABASE_URL"
export TRUECRIME_DISABLE_SCHEDULER=1
export TRUECRIME_SEARXNG_URL="http://127.0.0.1:9"
export TC_TEST_NO_NETWORK=1
export NEXT_DIST_DIR=".next-e2e"

"$ROOT/.venv/bin/python" -m alembic upgrade head
"$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" >"$ROOT/data/e2e-api.log" 2>&1 &
API_PID=$!
n=0
until curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null; do
  n=$((n + 1)); [[ "$n" -lt 60 ]] || { cat "$ROOT/data/e2e-api.log"; exit 1; }
  sleep 1
done

(cd "$ROOT/frontend" && npm run dev -- --hostname 127.0.0.1 --port "$WEB_PORT") >"$ROOT/data/e2e-frontend.log" 2>&1 &
FRONTEND_PID=$!
n=0
until curl -fsS "http://127.0.0.1:${WEB_PORT}" >/dev/null; do
  n=$((n + 1)); [[ "$n" -lt 60 ]] || { cat "$ROOT/data/e2e-frontend.log"; exit 1; }
  sleep 1
done

echo "E2E isolated stack: api=http://127.0.0.1:${API_PORT} frontend=http://127.0.0.1:${WEB_PORT} db=${DB_NAME}"
(cd "$ROOT/frontend" && PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" npx playwright test --reporter=line)
