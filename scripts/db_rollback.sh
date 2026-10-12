#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ "${1:-}" == "--to-sqlite" ]]; then
  backup="${2:-}"
  [[ -n "$backup" && -f "$backup" ]] || { echo "Usage: scripts/db_rollback.sh --to-sqlite <sqlite-backup-file>" >&2; exit 2; }
  shift 2
  target="$ROOT/truecrime.db"
  no_start=0
  no_stop=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --target) target="${2:-}"; [[ -n "$target" ]] || { echo "--target needs a path" >&2; exit 2; }; shift 2 ;;
      --no-start) no_start=1; shift ;;
      --no-stop) no_stop=1; shift ;;
      *) echo "Usage: scripts/db_rollback.sh --to-sqlite <backup> [--target <path>] [--no-stop] [--no-start]" >&2; exit 2 ;;
    esac
  done
  [[ "$no_stop" == 1 ]] || "$ROOT/scripts/dev_down.sh"
  ROOT="$ROOT" BACKUP_FILE="$(cd "$(dirname "$backup")" && pwd)/$(basename "$backup")" TARGET_FILE="$target" \
    "$ROOT/.venv/bin/python" - <<'PY'
from pathlib import Path
import os
from scripts import db_reconcile

backup = Path(os.environ["BACKUP_FILE"])
target = Path(os.environ["TARGET_FILE"])
db_reconcile.restore_database(backup, target)
env = Path(os.environ["ROOT"]) / ".env.local"
if target.resolve() == (Path(os.environ["ROOT"]) / "truecrime.db").resolve():
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [line for line in lines if not line.startswith("TRUECRIME_DATABASE_URL=")]
    lines.append("TRUECRIME_DATABASE_URL=sqlite:///./truecrime.db")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"SQLite rollback restored {target} and selected SQLite in {env}")
else:
    print(f"SQLite rollback restored copy {target}; local database selection was unchanged")
PY
  if [[ "$no_start" == 0 ]]; then
    "$ROOT/scripts/dev_up.sh" --first-run-safe
  fi
  exit 0
fi
exec env PYTHONPATH="$ROOT" .venv/bin/python scripts/db_rollback.py "$@"
