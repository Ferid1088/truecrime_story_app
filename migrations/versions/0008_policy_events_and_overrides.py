"""Policy decisions and human override history.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("production_id", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("policy", sa.String(length=80), nullable=True),
        sa.Column("rule_id", sa.String(length=100), nullable=True),
        sa.Column("decision", sa.String(length=40), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("context_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_policy_events_created_at", "policy_events", ["created_at"])
    op.create_index("ix_policy_events_case_id", "policy_events", ["case_id"])
    op.create_index("ix_policy_events_production_id", "policy_events", ["production_id"])
    op.create_index("ix_policy_events_language", "policy_events", ["language"])
    op.create_table(
        "override_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=True),
        sa.Column("scope_type", sa.String(length=80), nullable=True),
        sa.Column("scope_id", sa.String(length=100), nullable=True),
        sa.Column("field", sa.String(length=200), nullable=True),
        sa.Column("old_value_json", sa.Text(), nullable=False),
        sa.Column("new_value_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverted_by", sa.String(length=200), nullable=True),
    )
    op.create_index("ix_override_log_created_at", "override_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_override_log_created_at", table_name="override_log")
    op.drop_table("override_log")
    op.drop_index("ix_policy_events_language", table_name="policy_events")
    op.drop_index("ix_policy_events_production_id", table_name="policy_events")
    op.drop_index("ix_policy_events_case_id", table_name="policy_events")
    op.drop_index("ix_policy_events_created_at", table_name="policy_events")
    op.drop_table("policy_events")
