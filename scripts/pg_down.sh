#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MARKER="$ROOT/data/dev/postgresql.started"

usage() { echo "Usage: scripts/pg_down.sh"; }
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then usage; exit 0; fi
[[ -z "${1:-}" ]] || { usage >&2; exit 2; }

if [[ ! -f "$MARKER" ]]; then
  echo "PostgreSQL 16 was not started by scripts/pg_up.sh; leaving it running."
  exit 0
fi
brew services stop postgresql@16 >/dev/null
rm -f "$MARKER"
echo "Stopped PostgreSQL 16 started by scripts/pg_up.sh."
