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
    op.drop_index("ix_provider_calls_stage", table_name="provider_calls")
    op.drop_index("ix_provider_calls_case_id", table_name="provider_calls")
    op.drop_column("provider_calls", "cost_eur")
    op.drop_column("provider_calls", "units")
    op.drop_column("provider_calls", "model")
    op.drop_column("provider_calls", "stage")
    op.drop_column("provider_calls", "case_id")
    op.drop_column("cases", "budget_stop_reason")
    op.drop_column("cases", "total_cost_cap_eur")
