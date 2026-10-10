#!/usr/bin/env bash
# The migration gate: every step must pass all of this before it lands on main.
#
#   scripts/gate.sh                  # everything
#   scripts/gate.sh --backend        # Python tests + secret scan only
#   scripts/gate.sh --frontend       # typecheck, lint, build, end-to-end only
#   scripts/gate.sh --no-e2e         # skip the Playwright tests
#
# Optional environment:
#   PYTHON=python3.13                interpreter for the backend checks
#   TC_BASELINE=baseline.json TC_DB=truecrime.db
#                                    also check data parity against a saved baseline
#   PW_CONFIG=playwright.config.ts   Playwright config to use
#
# A failed check does not stop the run: the summary lists every failure, and the
# exit code is non-zero if any check failed. Fix, then run the WHOLE gate again.
set -u
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
PW_CONFIG="${PW_CONFIG:-playwright.config.ts}"
run_backend=1; run_frontend=1; run_e2e=1
for arg in "$@"; do
  case "$arg" in
    --backend)  run_frontend=0 ;;
    --frontend) run_backend=0 ;;
    --no-e2e)   run_e2e=0 ;;
    -h|--help)  sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

results=()
failed=0
check() {  # check "label" command...
  local label="$1"; shift
  echo; echo "=== $label"
  if "$@"; then results+=("PASS  $label"); else results+=("FAIL  $label"); failed=1; fi
}
in_frontend() { ( cd frontend && "$@" ); }

if [ "$run_backend" = 1 ]; then
  check "Python test suite" "$PYTHON" -m pytest -q -p no:cacheprovider
  check "Secret scan (tracked files)" "$PYTHON" scripts/secret_scan.py
  if [ -n "${TC_BASELINE:-}" ] && [ -n "${TC_DB:-}" ]; then
    check "Data parity vs baseline" "$PYTHON" scripts/db_tools.py compare "$TC_BASELINE" "$TC_DB"
  fi
fi

if [ "$run_frontend" = 1 ]; then
  check "Frontend: route types"   in_frontend npx next typegen
  check "Frontend: typecheck"     in_frontend npx tsc --noEmit
  check "Frontend: eslint"        in_frontend npx eslint .
  check "Frontend: production build" in_frontend npx next build
  if [ "$run_e2e" = 1 ]; then
    check "Frontend: Playwright e2e" in_frontend npx playwright test -c "$PW_CONFIG" e2e/app.spec.ts --reporter=list
  fi
fi

echo; echo "================ GATE SUMMARY ================"
printf '%s\n' "${results[@]}"
if [ "$failed" = 0 ]; then echo "GATE: GREEN"; else echo "GATE: RED - fix and rerun the whole gate"; fi
exit "$failed"
