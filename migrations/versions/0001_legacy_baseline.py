"""Legacy baseline: the exact structure of the production database on 2026-10-10.

Before Alembic the schema grew through create_all plus hand-written ALTER TABLEs.
This revision freezes that state (migrations/legacy_schema.sql, structure only) so
an existing database can simply be stamped here, and a new database built from
scratch ends up identical to production. Later revisions move both forward.

Revision ID: 0001
Revises:
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_DIR = Path(__file__).resolve().parent.parent


def _statements(name: str) -> list[str]:
    lines = [ln for ln in (_DIR / name).read_text(encoding="utf-8").splitlines() if not ln.startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";\n") if s.strip().rstrip(";")]


def upgrade() -> None:
    # SQLite: the exact production structure. Postgres has no legacy: it starts from the
    # structure the models describe at 0002 (migrations/postgres_schema.sql), so 0002 is a no-op there.
    name = "legacy_schema.sql" if op.get_bind().dialect.name == "sqlite" else "postgres_schema.sql"
    for stmt in _statements(name):
        op.execute(stmt.rstrip(";"))


def downgrade() -> None:
    raise RuntimeError("The legacy baseline cannot be downgraded: it is the starting point.")
