"""The film and card strategist agents are gone: the toolkit makes its cards from the text directly, so the table that stored
their plans is dropped."""

import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("detective_strategies")


def downgrade() -> None:
    op.create_table(
        "detective_strategies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("strategy_json", sa.Text(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_id", "kind", name="uq_detective_strategy"),
        sa.CheckConstraint("kind IN ('film', 'cards')", name="ck_detective_strategy_kind"),
    )
