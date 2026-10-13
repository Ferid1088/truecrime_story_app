"""Add case-wide EUR budgets and enriched provider ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("total_cost_cap_eur", sa.Float(), nullable=False, server_default="20"))
    op.add_column("cases", sa.Column("budget_stop_reason", sa.Text(), nullable=True))
    op.add_column("provider_calls", sa.Column("case_id", sa.Integer(), nullable=True))
    op.add_column("provider_calls", sa.Column("stage", sa.String(80), nullable=True))
    op.add_column("provider_calls", sa.Column("model", sa.String(200), nullable=True))
    op.add_column("provider_calls", sa.Column("units", sa.Float(), nullable=True))
    op.add_column("provider_calls", sa.Column("cost_eur", sa.Float(), nullable=True))
    op.create_index("ix_provider_calls_case_id", "provider_calls", ["case_id"])
    op.create_index("ix_provider_calls_stage", "provider_calls", ["stage"])


def downgrade() -> None:
    raise RuntimeError("0016 is not reversible in place; restore the backup instead.")
