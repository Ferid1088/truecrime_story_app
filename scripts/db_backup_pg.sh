#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env.local"
BACKUP_DIR="$ROOT/data/backups"
HOST="${PGHOST:-127.0.0.1}"
PORT="${PGPORT:-5432}"
USER_NAME="${PGUSER_APP:-truecrime}"
DB_NAME="${PGDATABASE_APP:-truecrime}"
ADMIN_USER="${PG_ADMIN_USER:-$USER}"

read_local_value() {
  local key="$1" value
  [[ -f "$ENV_FILE" ]] || return 0
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  value="${value%$'\r'}"
  if [[ "$value" == \"* && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'* && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: scripts/db_backup_pg.sh"
  exit 0
fi
[[ -z "${1:-}" ]] || { echo "Usage: scripts/db_backup_pg.sh" >&2; exit 2; }
command -v pg_dump >/dev/null 2>&1 || { echo "pg_dump is required." >&2; exit 1; }
command -v createdb >/dev/null 2>&1 || { echo "createdb is required." >&2; exit 1; }
command -v dropdb >/dev/null 2>&1 || { echo "dropdb is required." >&2; exit 1; }

password="$(read_local_value TRUECRIME_PG_PASSWORD)"
mkdir -p "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S%z)"
dump="$BACKUP_DIR/truecrime-postgres-${stamp}.dump"
checksum="$dump.sha256"
PGPASSWORD="$password" pg_dump --format=custom --file="$dump" \
  -h "$HOST" -p "$PORT" -U "$USER_NAME" -d "$DB_NAME"
shasum -a 256 "$dump" > "$checksum"

restore_db="truecrime_restore_check_$$"
cleanup() {
  dropdb --if-exists -h "$HOST" -p "$PORT" -U "$ADMIN_USER" "$restore_db" >/dev/null 2>&1 || true
}
trap cleanup EXIT
createdb -h "$HOST" -p "$PORT" -U "$ADMIN_USER" -O "$USER_NAME" "$restore_db"
pg_restore --exit-on-error --no-owner --role="$USER_NAME" \
  -h "$HOST" -p "$PORT" -U "$ADMIN_USER" -d "$restore_db" "$dump"

TRUECRIME_SOURCE_DB="$DB_NAME" TRUECRIME_RESTORE_DB="$restore_db" \
TRUECRIME_PG_HOST="$HOST" TRUECRIME_PG_PORT="$PORT" TRUECRIME_PG_USER="$USER_NAME" \
TRUECRIME_PG_PASSWORD="$password" "$ROOT/.venv/bin/python" - <<'PY'
from __future__ import annotations
import os
from sqlalchemy import create_engine, inspect, text

def counts(db: str) -> dict[str, int]:
    url = "postgresql+psycopg://" + os.environ["TRUECRIME_PG_USER"] + ":" + os.environ["TRUECRIME_PG_PASSWORD"] + "@" + os.environ["TRUECRIME_PG_HOST"] + ":" + os.environ["TRUECRIME_PG_PORT"] + "/" + db
    engine = create_engine(url)
    try:
        names = inspect(engine).get_table_names(schema="public")
        with engine.connect() as conn:
            return {name: int(conn.execute(text(f'SELECT count(*) FROM public."{name}"')).scalar_one()) for name in names}
    finally:
        engine.dispose()

source, restored = counts(os.environ["TRUECRIME_SOURCE_DB"]), counts(os.environ["TRUECRIME_RESTORE_DB"])
if source != restored:
    print("row_count_mismatch")
    for name in sorted(set(source) | set(restored)):
        if source.get(name) != restored.get(name): print(name, source.get(name), restored.get(name))
    raise SystemExit(1)
print(f"restore_row_counts_equal tables={len(source)}")
PY

echo "PostgreSQL backup: $dump"
echo "SHA-256 file: $checksum"
echo "PostgreSQL restore check passed; temporary database: $restore_db (dropped on exit)"
