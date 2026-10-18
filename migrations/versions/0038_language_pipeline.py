"""Language pipeline: prompt-cache and reasoning usage in the ledger, stage metadata on story versions."""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("cached_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("reasoning_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("reasoning_effort", sa.String(length=20), nullable=True))
    op.add_column("provider_calls", sa.Column("tokens_cached", sa.Integer(), nullable=True))
    op.add_column("provider_calls", sa.Column("tokens_reasoning", sa.Integer(), nullable=True))
    op.add_column("provider_calls", sa.Column("reasoning_effort", sa.String(length=20), nullable=True))
    op.add_column("story_versions", sa.Column("draft_version_id", sa.Integer(), nullable=True))
    op.add_column("story_versions", sa.Column("reasoning_effort", sa.String(length=20), nullable=True))
    op.add_column("story_versions", sa.Column("minutes", sa.Float(), nullable=True))
    op.add_column("story_versions", sa.Column("cost_eur", sa.Float(), nullable=True))
    op.create_index("ix_story_versions_draft_version_id", "story_versions", ["draft_version_id"])


def downgrade() -> None:
    op.drop_index("ix_story_versions_draft_version_id", table_name="story_versions")
    for column in ("cost_eur", "minutes", "reasoning_effort", "draft_version_id"):
        op.drop_column("story_versions", column)
    for column in ("reasoning_effort", "tokens_reasoning", "tokens_cached"):
        op.drop_column("provider_calls", column)
    for column in ("reasoning_effort", "reasoning_tokens", "cached_tokens"):
        op.drop_column("agent_runs", column)
