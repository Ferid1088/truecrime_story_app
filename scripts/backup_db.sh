#!/usr/bin/env bash
set -euo pipefail

# Usage: scripts/backup_db.sh
# Writes a PostgreSQL custom-format dump outside the repository. The URL is
# read from the environment first, then from .env/.env.local without sourcing
# either file. Override the destination with TRUECRIME_BACKUP_DIR.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${TRUECRIME_BACKUP_DIR:-$HOME/truecrime_backups}"

read_local_value() {
  local file="$1" key="$2" value
  [[ -f "$file" ]] || return 0
  value="$(grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true)"
  value="${value%$'\r'}"
  if [[ "$value" == \"* && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'* && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

DATABASE_URL_VALUE="${TRUECRIME_DATABASE_URL:-${DATABASE_URL:-}}"
if [[ -z "$DATABASE_URL_VALUE" ]]; then
  for file in "$ROOT/.env" "$ROOT/.env.local"; do
    DATABASE_URL_VALUE="$(read_local_value "$file" TRUECRIME_DATABASE_URL)"
    [[ -n "$DATABASE_URL_VALUE" ]] && break
    DATABASE_URL_VALUE="$(read_local_value "$file" DATABASE_URL)"
    [[ -n "$DATABASE_URL_VALUE" ]] && break
  done
fi
[[ -n "$DATABASE_URL_VALUE" ]] || { echo "No PostgreSQL URL configured" >&2; exit 2; }
case "$DATABASE_URL_VALUE" in
  postgresql*|postgres*) ;;
  *) echo "Refusing non-PostgreSQL URL" >&2; exit 2 ;;
esac
# SQLAlchemy's driver suffix is not part of a libpq URL.
DATABASE_URL_VALUE="$(printf '%s' "$DATABASE_URL_VALUE" | sed \
  -e 's#^postgresql+psycopg://#postgresql://#' \
  -e 's#^postgres+psycopg://#postgres://#')"

mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%d_%H%M%S)"
path="$BACKUP_DIR/truecrime_live_backup_${timestamp}.dump"
pg_dump "$DATABASE_URL_VALUE" --format=custom --no-owner --no-acl --file="$path"
checksum="$(shasum -a 256 "$path" | awk '{print $1}')"
size="$(stat -f '%z' "$path" 2>/dev/null || stat -c '%s' "$path")"
printf 'backup=%s\nsize_bytes=%s\nsha256=%s\n' "$path" "$size" "$checksum"
