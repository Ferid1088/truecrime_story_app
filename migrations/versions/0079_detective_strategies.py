"""The Detective Toolkit's strategy for a whole film: the film strategist's plan of which card kinds serve which aim and
how they help each other, and the card strategist's plan of where each card kind is used - made from the text before
a scene is directed (one row per graph and kind of strategy)."""

import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("detective_strategies")
