#!/usr/bin/env bash
# The four jobs of .github/workflows/ci.yml, run locally in a clean copy of the tree: no .env, no data/, no local
# database, Python 3.13 and the newest allowed packages, a throw-away Postgres 16 on its own port. "Green" here means green in CI.
#
#   scripts/ci_local.sh [all|backend|backend-postgres|frontend|audit]...
#
# The copy holds the tracked files and the untracked files git does not ignore (what a commit would hold), so the
# proof covers uncommitted fixes too. Needs: git, uv, node/npm, ffmpeg, Homebrew postgresql@16 (or PG_BIN=<bin dir>).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${CI_LOCAL_DIR:-$(mktemp -d /tmp/ci_local.XXXXXX)}"
PG_BIN="${PG_BIN:-$(brew --prefix postgresql@16 2>/dev/null)/bin}"
JOBS=("$@")
[ ${#JOBS[@]} -eq 0 ] && JOBS=(all)
[ "${JOBS[0]}" = all ] && JOBS=(backend backend-postgres frontend audit)

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

echo "== clean copy in $WORK"
rm -rf "$WORK/repo" && mkdir -p "$WORK/repo"
(cd "$ROOT" && git ls-files -z --cached --others --exclude-standard | tar --null -T - -cf - 2>/dev/null) | tar -xf - -C "$WORK/repo"
cd "$WORK/repo" || exit 2
# the copy is a git repo with one commit (the tree under test): the secret scan, the hygiene tests and the dev scripts read git
git init -q && git add -A && git -c user.name=ci -c user.email=ci@local commit -qm "tree under test"

PY="$WORK/venv/bin/python"
python_env() {
  [ -x "$PY" ] && return 0
  uv venv --python 3.13 "$WORK/venv" -q || return 1
  uv pip install --python "$PY" -q -r requirements-dev.txt || return 1
  uv pip install --python "$PY" -q "Pillow>=10.3" "opencv-python-headless>=4.9" "faster-whisper>=1.1,<2" "whisper-normalizer>=0.1.12" || return 1
  uv pip install --python "$PY" -q torch --index-url https://download.pytorch.org/whl/cpu || return 1
  uv pip install --python "$PY" -q "transformers>=5.5,<6" "setuptools>=83" || return 1
}

backend() {
  python_env || return 1
  export TRUECRIME_DATABASE_URL=sqlite:///ci-fast.db TRUECRIME_TEST_ADMIN_URL=""
  "$PY" scripts/secret_scan.py || return 1
  "$PY" scripts/check_model_migrations.py || return 1
  "$PY" scripts/quality_ratchet.py ruff || return 1
  "$PY" scripts/quality_ratchet.py mypy || return 1
  "$PY" -m vulture app scripts --min-confidence 80 || true      # report only, like CI
  "$PY" -m pytest -q -p no:cacheprovider
}

backend_postgres() {
  python_env || return 1
  local port data
  port="$(free_port)"; data="$WORK/pgdata"
  rm -rf "$data"; echo postgres > "$WORK/pgpw"
  "$PG_BIN/initdb" -D "$data" -U postgres --pwfile="$WORK/pgpw" --auth=md5 -E UTF8 >/dev/null || return 1
  "$PG_BIN/pg_ctl" -D "$data" -o "-p $port -k $WORK -c listen_addresses=127.0.0.1" -l "$WORK/pg.log" -w start >/dev/null || return 1
  PGPASSWORD=postgres "$PG_BIN/psql" -h 127.0.0.1 -p "$port" -U postgres -qc "CREATE DATABASE truecrime_test" || { "$PG_BIN/pg_ctl" -D "$data" stop -m fast >/dev/null; return 1; }
  export TRUECRIME_DATABASE_URL="postgresql+psycopg://truecrime:postgres@127.0.0.1:$port/truecrime_test"
  export TC_TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:$port/truecrime_test"
  export TRUECRIME_TEST_ADMIN_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:$port/postgres"
  "$PY" -m pytest -q -p no:cacheprovider
  local rc=$?
  "$PG_BIN/pg_ctl" -D "$data" stop -m fast >/dev/null
  return $rc
}

frontend() {
  (
    cd frontend || exit 1
    # the build points at a port nobody listens on: the e2e tests must not lean on a backend running on this machine
    export NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:9"
    npm ci --no-audit --no-fund && npx next typegen && npx tsc --noEmit && npx eslint . && npx next build \
      && npx playwright install chromium \
      && CI=1 PLAYWRIGHT_PORT="$(free_port)" npx playwright test e2e/app.spec.ts --reporter=list
  )
}

audit() {
  uv venv --python 3.13 "$WORK/audit-venv" -q || return 1
  local apy="$WORK/audit-venv/bin/python"
  uv pip install --python "$apy" -q torch --index-url https://download.pytorch.org/whl/cpu || return 1
  uv pip install --python "$apy" -q -r requirements-documentary.txt pip-audit || return 1
  uv pip freeze --python "$apy" | grep -vi '^torch==' > "$WORK/frozen.txt"
  "$WORK/audit-venv/bin/pip-audit" -r "$WORK/frozen.txt" --no-deps --disable-pip || return 1
  (cd frontend && npm ci --omit=dev --no-audit --no-fund >/dev/null && npm audit --omit=dev --audit-level=low)
}

status=0
RESULT=""
for job in "${JOBS[@]}"; do
  echo; echo "================ $job"
  case "$job" in
    backend) backend ;;
    backend-postgres) backend_postgres ;;
    frontend) frontend ;;
    audit) audit ;;
    *) echo "unknown job $job"; false ;;
  esac
  rc=$?
  [ $rc -eq 0 ] && echo ">>> $job: PASS" || { echo ">>> $job: FAIL ($rc)"; status=1; }
  RESULT+="$job=$rc "
done
echo; echo "== result: $RESULT"
exit $status
