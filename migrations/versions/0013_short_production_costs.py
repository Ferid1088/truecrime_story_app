"""Record Shorts provider spend and the per-case cap."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "short_production_jobs",
        sa.Column("provider_cost_usd", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "short_production_jobs",
        sa.Column("provider_call_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "short_production_jobs",
        sa.Column("cost_cap_usd", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("short_production_jobs", "cost_cap_usd")
    op.drop_column("short_production_jobs", "provider_call_count")
    op.drop_column("short_production_jobs", "provider_cost_usd")
