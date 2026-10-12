#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/dev"

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
if command -v brew >/dev/null 2>&1; then brew services stop postgresql@16 >/dev/null 2>&1 || true; fi
echo "Local services stopped."
