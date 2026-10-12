#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/dev"

read_local_value() {
  local file="$1" key="$2" value
  [[ -f "$file" ]] || return 0
  value="$(grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true)"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'*\' && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

DATABASE_URL_VALUE="${TRUECRIME_DATABASE_URL:-${DATABASE_URL:-}}"
if [[ -z "$DATABASE_URL_VALUE" ]]; then
  DATABASE_URL_VALUE="$(read_local_value "$ROOT/.env" TRUECRIME_DATABASE_URL)"
fi
if [[ -z "$DATABASE_URL_VALUE" ]]; then
  DATABASE_URL_VALUE="$(read_local_value "$ROOT/.env" DATABASE_URL)"
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Usage: scripts/dev_down.sh

Stop only the PIDs recorded by scripts/dev_up.sh. Use --help for this text.
EOF
  exit 0
fi
[[ -z "${1:-}" ]] || { echo "Usage: scripts/dev_down.sh" >&2; exit 2; }

_pidfile_for() { [[ "$1" == searxng ]] && echo "$ROOT/data/searxng/searxng.pid" || echo "$STATE/$1.pid"; }

for name in frontend worker api searxng; do
  pidfile="$(_pidfile_for "$name")"
  if [[ -f "$pidfile" ]]; then
    pid="$(<"$pidfile")"
    kill "$pid" 2>/dev/null || true
    rm -f "$pidfile"
  fi
done
rm -f "$STATE/frontend.port"
echo "Local services stopped."
