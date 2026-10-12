#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/dev"

for name in frontend worker api searxng; do
  pidfile="$STATE/$name.pid"
  if [[ -f "$pidfile" ]]; then
    pid="$(<"$pidfile")"
    kill "$pid" 2>/dev/null || true
    rm -f "$pidfile"
  fi
done
if command -v brew >/dev/null 2>&1; then brew services stop postgresql@16 >/dev/null 2>&1 || true; fi
echo "Local services stopped."
