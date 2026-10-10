#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

THROUGH=""
ALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --through)
      THROUGH="${2:-}"
      shift 2
      ;;
    --all)
      ALL=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

export PYTHONPATH="$ROOT:${PYTHONPATH:-}"

if [[ "$ALL" == "1" ]]; then
  pytest -q
  exit 0
fi

case "$THROUGH" in
  0)
    test -f reports/shortform/phase_0_loop_log.md
    ;;
  1)
    pytest -q tests/test_shortform_phase1.py tests/test_documentary_foundations.py
    ;;
  2)
    pytest -q tests/test_shortform_phase1.py tests/test_shortform_phase2.py tests/test_documentary_foundations.py
    ;;
  3)
    pytest -q tests/test_shortform_phase1.py tests/test_shortform_phase2.py tests/test_shortform_phase3.py tests/test_documentary_foundations.py
    ;;
  3A|3a)
    pytest -q tests/test_shortform_phase1.py tests/test_shortform_phase2.py tests/test_shortform_phase3.py tests/test_shortform_phase3a.py tests/test_documentary_foundations.py
    ;;
  4)
    pytest -q tests/test_shortform_phase1.py tests/test_shortform_phase2.py tests/test_shortform_phase3.py tests/test_shortform_phase3a.py tests/test_shortform_phase4.py tests/test_shortform_operations.py tests/test_documentary_foundations.py
    ;;
  "")
    pytest -q tests/test_shortform_phase1.py
    ;;
  *)
    pytest -q tests/test_shortform_phase1.py
    ;;
esac
