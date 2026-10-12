#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec env PYTHONPATH="$ROOT" .venv/bin/python scripts/db_reconcile.py "$@"
