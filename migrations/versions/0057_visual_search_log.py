"""The visual search ledger: every search a provider ran for a case, so the same query is never run
twice on the same provider and the funnel report can count queries per provider.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visual_search_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("query_key", sa.String(300), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False, server_default="exact"),
        sa.Column("found_during", sa.String(30), nullable=True),
        sa.Column("entity_key", sa.String(120), nullable=True),
        sa.Column("candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "provider", "query_key", name="uq_visual_search_log"),
    )
    op.create_index("ix_visual_search_log_case_id", "visual_search_log", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_visual_search_log_case_id", table_name="visual_search_log")
    op.drop_table("visual_search_log")
