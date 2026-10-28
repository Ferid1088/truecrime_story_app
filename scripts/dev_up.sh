#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/data/dev"
mkdir -p "$STATE"
export TRUECRIME_PROCESS_COMMIT="$(git -C "$ROOT" rev-parse HEAD)"

usage() {
  cat <<'EOF'
Usage: scripts/dev_up.sh [--first-run-safe] [--require-search] [status|--status]

Start the native local services, or show their status. Brave Search is
configured through BRAVE_SEARCH_API_KEY; --require-search makes a missing key
fatal.
--first-run-safe sets TRUECRIME_JOB_MAX_AUTO_RESUMES=0 and
TRUECRIME_DISABLE_SCHEDULER=1 for the API and worker.
EOF
}

SAFE_MODE=0
REQUIRE_SEARCH=0
ACTION=start
for arg in "$@"; do
  case "$arg" in
    --help|-h) usage; exit 0 ;;
    --first-run-safe) SAFE_MODE=1 ;;
    --require-search) REQUIRE_SEARCH=1 ;;
    status|--status) ACTION=status ;;
    *) usage >&2; exit 2 ;;
  esac
done

# Read only the small set of local process settings needed by this script. In
# particular, never source a shell rc file or execute .env contents.
read_local_value() {
  local file="$1" key="$2" value
  [[ -f "$file" ]] || return 0
  value="$(grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true)"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'*\' && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

load_if_unset() {
  local key="$1" file value
  [[ ${!key+x} ]] && return 0
  for file in "$ROOT/.env" "$ROOT/.env.local"; do
    value="$(read_local_value "$file" "$key")"
    if [[ -n "$value" ]]; then export "$key=$value"; return 0; fi
  done
}

for key in TRUECRIME_DATABASE_URL DATABASE_URL BRAVE_SEARCH_API_KEY \
  TRUECRIME_API_PORT TRUECRIME_FRONTEND_PORT FFMPEG_BIN FFPROBE_BIN; do
  load_if_unset "$key"
done

# This launcher is for the live local stack, never the disposable E2E stack.
# Clear inherited test switches before any API/worker process is created.  In
# particular, an E2E shell can otherwise leak recorded-e2e into the live DB.
unset TC_TEST_NO_NETWORK TRUECRIME_TEST_NO_REAL_PROVIDERS TRUECRIME_PROVIDER_MODE \
  E2E_DB_NAME E2E_DB_URL E2E_DATABASE_URL NEXT_DIST_DIR
echo "effective provider mode: live"

# ffmpeg-full is keg-only. Pass its absolute tools to API/worker processes;
# deliberately do not alter PATH or link the keg.
if [[ -z "${FFMPEG_BIN:-}" && -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg ]]; then
  export FFMPEG_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
fi
if [[ -z "${FFPROBE_BIN:-}" && -x /opt/homebrew/opt/ffmpeg-full/bin/ffprobe ]]; then
  export FFPROBE_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffprobe
fi

DATABASE_URL_VALUE="${TRUECRIME_DATABASE_URL:-${DATABASE_URL:-postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime}}"
if [[ "$DATABASE_URL_VALUE" == postgresql* || "$DATABASE_URL_VALUE" == postgres* ]]; then
  DB_KIND=postgres
else
  DB_KIND=sqlite
fi
API_PORT="${TRUECRIME_API_PORT:-8001}"
FRONTEND_PORT_REQUESTED="${TRUECRIME_FRONTEND_PORT:-3001}"

_pidfile_for() { echo "$STATE/$1.pid"; }

_pid_alive() {
  [[ -f "$1" ]] && kill -0 "$(<"$1")" 2>/dev/null
}

_port_free() {
  if command -v lsof >/dev/null 2>&1; then
    ! lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | grep -q .
  else
    ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

_wait_for_port() {
  local pid="$1" port="$2"
  for _ in {1..40}; do
    kill -0 "$pid" 2>/dev/null || return 1
    if ! _port_free "$port"; then return 0; fi
    sleep 0.25
  done
  return 1
}

_frontend_process_cwd() {
  local pid="$1"
  command -v lsof >/dev/null 2>&1 || return 0
  # a process that exited since `ps` listed it is not an error
  lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true
}

_stop_stale_frontend_servers() {
  local recorded_pid="" line pid command cwd
  if [[ -f "$STATE/frontend.pid" ]]; then
    recorded_pid="$(<"$STATE/frontend.pid")"
  fi
  command -v ps >/dev/null 2>&1 || return 0
  command -v lsof >/dev/null 2>&1 || return 0

  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    pid="${line%% *}"
    command="${line#* }"
    [[ -n "$pid" && "$pid" != "$recorded_pid" ]] || continue
    case "$command" in
      *next-server*|*"next dev"*) ;;
      *) continue ;;
    esac
    cwd="$(_frontend_process_cwd "$pid")"
    [[ "$cwd" == "$ROOT/frontend" ]] || continue
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    echo "stopped stale frontend pid $pid"
  done < <(ps -axo pid=,command=)
}

_last_log_line() {
  local name="$1" log="$STATE/$1.log"
  [[ -f "$log" ]] || return 0
  awk 'NF { line=$0 } END { if (line != "") print line }' "$log"
}

_record_start_failure() {
  local name="$1" last_line
  last_line="$(_last_log_line "$name")"
  [[ -n "$last_line" ]] || last_line="no log output"
  printf '%s\n' "$last_line" > "$STATE/$name.error"
}

_port_holder_report() {
  local port="$1" pid command cwd
  command -v lsof >/dev/null 2>&1 || return 0
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    command="$(ps -p "$pid" -o command= 2>/dev/null | sed 's/^ *//')"
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
    echo "frontend port $port holder: pid $pid command=${command:-unknown} cwd=${cwd:-unknown}"
  done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u)
}

_component_status() {
  local name="$1" port="$2" pidfile
  pidfile="$(_pidfile_for "$name")"
  if _pid_alive "$pidfile"; then
    if [[ "$name" == worker ]]; then
      echo "$name: running (pid $(<"$pidfile"), port n/a)"
    else
      echo "$name: running (pid $(<"$pidfile"), port $port)"
    fi
  else
    if [[ "$name" == worker ]]; then
      echo "$name: stopped (pid none, port n/a)"
    elif [[ -f "$STATE/$name.error" ]]; then
      echo "$name: stopped (pid none, port $port; last error: $(<"$STATE/$name.error"))"
    else
      echo "$name: stopped (pid none, port $port)"
    fi
  fi
}

status() {
  if [[ "$DB_KIND" == sqlite ]]; then
    echo "postgresql: not used (sqlite)"
  elif pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "postgresql: ready (external, port 5432)"
  else
    echo "postgresql: stopped (pid external, port 5432)"
  fi
  if [[ -n "${BRAVE_SEARCH_API_KEY:-}" ]]; then
    echo "brave: configured (api key present)"
  else
    echo "brave: unconfigured (BRAVE_SEARCH_API_KEY missing)"
  fi
  _component_status api "$API_PORT"
  _component_status worker n/a
  frontend_shown="$([[ -f "$STATE/frontend.port" ]] && cat "$STATE/frontend.port" || echo "$FRONTEND_PORT_REQUESTED")"
  _component_status frontend "$frontend_shown"
}

if [[ "$SAFE_MODE" == 1 ]]; then
  echo "first-run-safe: TRUECRIME_JOB_MAX_AUTO_RESUMES=0 TRUECRIME_DISABLE_SCHEDULER=1"
fi
if [[ "$ACTION" == status ]]; then status; exit 0; fi

# a missing search key stops --require-search before anything else is looked at
if [[ -z "${BRAVE_SEARCH_API_KEY:-}" ]]; then
  if [[ "$REQUIRE_SEARCH" == 1 ]]; then
    echo "Brave Search is unconfigured and --require-search was requested." >&2
    exit 1
  fi
  echo "WARNING: Brave Search is unconfigured; API, worker and frontend will start without it."
else
  echo "Brave Search configured; effective provider mode: live."
fi

command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }
[[ -x "$ROOT/.venv/bin/python" ]] || { echo "Create .venv and install requirements first." >&2; exit 1; }
[[ -x "$ROOT/frontend/node_modules/.bin/next" ]] || { echo "Run npm ci in frontend first." >&2; exit 1; }

if [[ "$DB_KIND" == postgres ]]; then
  command -v brew >/dev/null 2>&1 || { echo "Homebrew is required for the configured PostgreSQL database; no SQLite fallback is used." >&2; exit 1; }
  command -v pg_isready >/dev/null 2>&1 || { echo "pg_isready is required; install postgresql@16; no SQLite fallback is used." >&2; exit 1; }
fi

_stop_stale_frontend_servers
FRONTEND_PORT="$FRONTEND_PORT_REQUESTED"
while ! _port_free "$FRONTEND_PORT"; do
  _port_holder_report "$FRONTEND_PORT"
  FRONTEND_PORT=$((FRONTEND_PORT + 1))
  if [[ "$FRONTEND_PORT" -ge $((FRONTEND_PORT_REQUESTED + 20)) ]]; then
    echo "No free frontend port found near $FRONTEND_PORT_REQUESTED." >&2
    exit 1
  fi
done
if [[ "$FRONTEND_PORT" != "$FRONTEND_PORT_REQUESTED" ]]; then
  echo "Frontend port $FRONTEND_PORT_REQUESTED is taken; using $FRONTEND_PORT instead."
fi
echo "$FRONTEND_PORT" > "$STATE/frontend.port"

SAFE_ENV=()
if [[ "$SAFE_MODE" == 1 ]]; then
  SAFE_ENV=(TRUECRIME_JOB_MAX_AUTO_RESUMES=0 TRUECRIME_DISABLE_SCHEDULER=1)
fi

if [[ "$DB_KIND" == postgres ]]; then
  "$ROOT/scripts/pg_up.sh"
  load_if_unset TRUECRIME_DATABASE_URL
  DATABASE_URL_VALUE="${TRUECRIME_DATABASE_URL:-$DATABASE_URL_VALUE}"
  TRUECRIME_DATABASE_URL="$DATABASE_URL_VALUE" "$ROOT/.venv/bin/python" -m alembic upgrade head
fi

start_process() {
  local name="$1" readiness="$2" target="$3"; shift 3
  local pidfile="$STATE/$name.pid" new_pid
  if [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then return; fi
  nohup "$ROOT/.venv/bin/python" "$ROOT/scripts/detached_exec.py" "$@" \
    </dev/null >"$STATE/$name.log" 2>&1 &
  new_pid=$!
  if [[ "$readiness" == port ]]; then
    if ! _wait_for_port "$new_pid" "$target"; then
      kill "$new_pid" 2>/dev/null || true
      wait "$new_pid" 2>/dev/null || true
      [[ -f "$pidfile" ]] && ! _pid_alive "$pidfile" && rm -f "$pidfile"
      _record_start_failure "$name"
      echo "$name did not become ready; its pidfile was not recorded." >&2
      return 1
    fi
  else
    sleep 1
    if ! kill -0 "$new_pid" 2>/dev/null; then
      wait "$new_pid" 2>/dev/null || true
      [[ -f "$pidfile" ]] && ! _pid_alive "$pidfile" && rm -f "$pidfile"
      _record_start_failure "$name"
      echo "$name exited before readiness; its pidfile was not recorded." >&2
      return 1
    fi
  fi
  echo "$new_pid" > "$pidfile"
  rm -f "$STATE/$name.error"
}

start_failed=0
start_process api port "$API_PORT" env TRUECRIME_JOB_RUNNER=external TRUECRIME_API_PORT="$API_PORT" TRUECRIME_FRONTEND_PORT="$FRONTEND_PORT" ${SAFE_ENV[@]+"${SAFE_ENV[@]}"} \
  "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" || start_failed=1
start_process worker process - env TRUECRIME_JOB_RUNNER=external ${SAFE_ENV[@]+"${SAFE_ENV[@]}"} "$ROOT/.venv/bin/python" -m app.worker || start_failed=1
start_process frontend port "$FRONTEND_PORT" env NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:$API_PORT" \
  npm --prefix "$ROOT/frontend" run dev -- --hostname 127.0.0.1 --port "$FRONTEND_PORT" || start_failed=1

verify_failed=()
for name in api worker frontend; do
  if ! _pid_alive "$STATE/$name.pid"; then
    verify_failed+=("$name")
  fi
done
if ((${#verify_failed[@]} > 0)) || [[ "$start_failed" == 1 ]]; then
  ((${#verify_failed[@]} > 0)) || verify_failed+=("startup")
  echo "Services failed after readiness verification: ${verify_failed[*]}" >&2
  for name in api worker frontend; do
    if [[ -f "$STATE/$name.error" ]]; then
      echo "$name last error: $(<"$STATE/$name.error")" >&2
    fi
  done
  exit 1
fi

echo "Local services started (api: $API_PORT, frontend: $FRONTEND_PORT). Run scripts/dev_up.sh --status for readiness."
