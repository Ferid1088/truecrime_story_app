#!/usr/bin/env bash
# Run once on a NEW Mac after the whole project folder (including the .env files)
# was copied to ~/truecrime_story_app. Installs the system tools, the exact Python
# packages from requirements.lock.txt, the frontend packages, and restores the
# plain-SQL PostgreSQL dump whose paths were already rewritten for this machine.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB_NAME="${PGDATABASE_APP:-truecrime}"
DB_USER="${PGUSER_APP:-truecrime}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: scripts/mac_setup.sh"
  exit 0
fi
command -v brew >/dev/null 2>&1 || { echo "Install Homebrew first: https://brew.sh" >&2; exit 1; }
[[ -f "$ROOT/.env.local" ]] || { echo ".env.local missing: copy the whole project folder first." >&2; exit 1; }

brew install postgresql@16 ffmpeg-full ffmpeg tesseract poppler redis node uv git gh
brew services start postgresql@16
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
until pg_isready -q -h 127.0.0.1; do sleep 1; done

cd "$ROOT"
uv python install "$(cat .python-version)"
uv venv --python "$(cat .python-version)" --allow-existing --clear .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt
.venv/bin/python -m playwright install chromium
(cd frontend && npm ci)

sql="${1:?Usage: scripts/mac_setup.sh <path-to-truecrime_tc.sql>}"
[[ -f "$sql" ]] || { echo "SQL dump not found: $sql" >&2; exit 1; }
password="$(grep -E '^TRUECRIME_PG_PASSWORD=' .env.local | tail -n 1 | cut -d= -f2- | tr -d "\"'")"
psql -d postgres -v ON_ERROR_STOP=1 -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$DB_USER') THEN CREATE ROLE $DB_USER LOGIN PASSWORD '$password'; END IF; END \$\$;"
createdb -O "$DB_USER" "$DB_NAME"
psql -q -v ON_ERROR_STOP=1 -d "$DB_NAME" -f "$sql"

mkdir -p "$HOME/.truecrime"
echo "Alembic state:"; .venv/bin/alembic current
echo "Done. Start with: scripts/dev_up.sh   (UI http://localhost:3001, API port 8001)"
