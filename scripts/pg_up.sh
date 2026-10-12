#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/dev"
ENV_FILE="$ROOT/.env.local"
PG_SERVICE="postgresql@16"
PG_HOST="${PGHOST:-127.0.0.1}"
PG_PORT="${PGPORT:-5432}"
PG_ADMIN_USER="${PGUSER:-$USER}"

usage() {
  cat <<'EOF'
Usage: scripts/pg_up.sh [--status]

Start Homebrew PostgreSQL 16 if needed and provision the TrueCrime role and
databases. Credentials are kept in the ignored .env.local file.
EOF
}

read_local_value() {
  local key="$1" value
  [[ -f "$ENV_FILE" ]] || return 0
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  value="${value%$'\r'}"
  if [[ "$value" == \"* && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'* && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

status() {
  if ! command -v brew >/dev/null 2>&1; then
    echo "postgresql@16: unavailable (Homebrew is not installed)"
    return 1
  fi
  if pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
    echo "postgresql@16: ready (host $PG_HOST, port $PG_PORT)"
    psql -h "$PG_HOST" -p "$PG_PORT" -d postgres -Atc \
      "select 'truecrime role=' || exists(select 1 from pg_roles where rolname='truecrime') || ' database=' || exists(select 1 from pg_database where datname='truecrime') || ' test_database=' || exists(select 1 from pg_database where datname='truecrime_test')" \
      2>/dev/null || true
    return 0
  fi
  echo "postgresql@16: stopped (host $PG_HOST, port $PG_PORT)"
  return 1
}

[[ "${1:-}" != "--help" && "${1:-}" != "-h" ]] || { usage; exit 0; }
[[ "${1:-}" != "--status" ]] || { status; exit $?; }
[[ -z "${1:-}" ]] || { usage >&2; exit 2; }

command -v brew >/dev/null 2>&1 || { echo "Homebrew is required to provision PostgreSQL 16." >&2; exit 10; }
command -v psql >/dev/null 2>&1 || { echo "psql is required; install postgresql@16." >&2; exit 11; }
command -v pg_isready >/dev/null 2>&1 || { echo "pg_isready is required; install postgresql@16." >&2; exit 11; }

started_by_us=0
if ! pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then
  brew services start "$PG_SERVICE" >/dev/null
  started_by_us=1
  for _ in {1..30}; do
    pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1 && break
    sleep 1
  done
fi
pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1 || {
  echo "PostgreSQL 16 did not become ready at $PG_HOST:$PG_PORT." >&2
  exit 12
}

role_exists="$(psql -h "$PG_HOST" -p "$PG_PORT" -d postgres -Atqc "select exists(select 1 from pg_roles where rolname='truecrime')")"
password="$(read_local_value TRUECRIME_PG_PASSWORD)"
if [[ "$role_exists" != t ]]; then
  [[ -n "$password" ]] || password="$(openssl rand -hex 24)"
  psql -h "$PG_HOST" -p "$PG_PORT" -d postgres -v ON_ERROR_STOP=1 \
    -c "create role truecrime login password '$password'" >/dev/null
fi

for db_name in truecrime truecrime_test; do
  if ! psql -h "$PG_HOST" -p "$PG_PORT" -d postgres -Atqc "select 1 from pg_database where datname='$db_name'" | grep -q '^1$'; then
    createdb -h "$PG_HOST" -p "$PG_PORT" -O truecrime "$db_name"
  fi
done

if [[ -n "$password" ]]; then
  if [[ ! -f "$ENV_FILE" ]] || ! grep -q '^TRUECRIME_PG_PASSWORD=' "$ENV_FILE"; then
    umask 077
    touch "$ENV_FILE"
    printf 'TRUECRIME_PG_PASSWORD=%s\n' "$password" >> "$ENV_FILE"
  fi
  if [[ ! -f "$ENV_FILE" ]] || ! grep -q '^TRUECRIME_DATABASE_URL=' "$ENV_FILE"; then
    printf 'TRUECRIME_DATABASE_URL=postgresql+psycopg://truecrime:%s@127.0.0.1:5432/truecrime\n' "$password" >> "$ENV_FILE"
  fi
fi

mkdir -p "$STATE"
if [[ "$started_by_us" == 1 ]]; then
  printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE/postgresql.started"
fi
echo "PostgreSQL 16 is ready; TrueCrime role and databases are provisioned."
