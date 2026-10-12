#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB_NAME="${1:-truecrime_migration_test}"
DB_HOST="${PGHOST:-127.0.0.1}"
DB_PORT="${PGPORT:-5432}"
DB_USER="${PGUSER:-$USER}"
URL="postgresql+psycopg://${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

command -v createdb >/dev/null 2>&1 || { echo "createdb is required." >&2; exit 1; }
command -v dropdb >/dev/null 2>&1 || { echo "dropdb is required." >&2; exit 1; }
pg_isready -h "$DB_HOST" -p "$DB_PORT" >/dev/null 2>&1 || {
  echo "PostgreSQL is not ready at ${DB_HOST}:${DB_PORT}. Start postgresql@16 first." >&2
  exit 1
}

dropdb --if-exists -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" >/dev/null
createdb -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME"
cleanup() { dropdb -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

cd "$ROOT"
echo "upgrade through 0009: $DB_NAME"
TRUECRIME_DATABASE_URL="$URL" .venv/bin/python -m alembic upgrade 0009
echo "upgrade 0010 on a clean 0009 schema (reconciliation no-op): $DB_NAME"
TRUECRIME_DATABASE_URL="$URL" .venv/bin/python -m alembic upgrade head
echo "upgrade head second pass: $DB_NAME"
TRUECRIME_DATABASE_URL="$URL" .venv/bin/python -m alembic upgrade head
echo "downgrade -1: $DB_NAME"
TRUECRIME_DATABASE_URL="$URL" .venv/bin/python -m alembic downgrade -1
echo "upgrade head after downgrade: $DB_NAME"
TRUECRIME_DATABASE_URL="$URL" .venv/bin/python -m alembic upgrade head
echo "Migration cycle passed: $DB_NAME"
