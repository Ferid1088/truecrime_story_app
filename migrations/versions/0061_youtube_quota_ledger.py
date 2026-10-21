"""YouTube Data API quota ledger (P15d): every call counted (time, endpoint, units, stage,
case), so the discovery stage can show remaining units against its reserved share and the
Pacific-day reset time, and never spend the share reserved for uploads.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_quota_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("endpoint", sa.String(40), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("query_key", sa.String(300), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_youtube_quota_ledger_case_id", "youtube_quota_ledger", ["case_id"])
    op.create_index("ix_youtube_quota_ledger_created_at", "youtube_quota_ledger", ["created_at"])
    op.create_index("ix_youtube_quota_ledger_query_key", "youtube_quota_ledger", ["query_key"])


def downgrade() -> None:
    op.drop_index("ix_youtube_quota_ledger_query_key", table_name="youtube_quota_ledger")
    op.drop_index("ix_youtube_quota_ledger_created_at", table_name="youtube_quota_ledger")
    op.drop_index("ix_youtube_quota_ledger_case_id", table_name="youtube_quota_ledger")
    op.drop_table("youtube_quota_ledger")
