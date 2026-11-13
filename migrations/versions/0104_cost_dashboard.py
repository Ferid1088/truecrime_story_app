"""What the cost dashboard needs on every ledger row: the language, the pipeline step, the vendor, the object it was paid for, how sure the price is, and
whether the row only repeats another row of the same call; plus the invoices the owner types in to compare the ledger with the real bills (2026-11-13)."""

import sqlalchemy as sa
from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_calls", sa.Column("language", sa.String(20), nullable=True))
    op.add_column("provider_calls", sa.Column("step", sa.String(40), nullable=True))
    op.add_column("provider_calls", sa.Column("vendor", sa.String(40), nullable=True))
    op.add_column("provider_calls", sa.Column("subject", sa.String(120), nullable=True))
    op.add_column("provider_calls", sa.Column("accuracy", sa.String(16), nullable=True))
    op.add_column("provider_calls", sa.Column("attribution", sa.String(10), nullable=True))
    op.add_column("provider_calls", sa.Column("duplicate_of", sa.Integer(), nullable=True))
    for column in ("language", "step", "vendor", "duplicate_of"):
        op.create_index(f"ix_provider_calls_{column}", "provider_calls", [column])
    op.create_index("ix_provider_calls_case_created", "provider_calls", ["case_id", "created_at"])
    op.create_table(
        "cost_invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vendor", sa.String(40), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("amount_eur", sa.Float(), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("vendor", "period", name="uq_cost_invoice_vendor_period"),
    )


def downgrade() -> None:
    op.drop_table("cost_invoices")
    op.drop_index("ix_provider_calls_case_created", table_name="provider_calls")
    for column in ("duplicate_of", "vendor", "step", "language"):
        op.drop_index(f"ix_provider_calls_{column}", table_name="provider_calls")
    for column in ("duplicate_of", "attribution", "accuracy", "subject", "vendor", "step", "language"):
        op.drop_column("provider_calls", column)
