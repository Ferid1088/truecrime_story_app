"""Cache and cost telemetry of every model call: tokens written to the prompt cache, the hash of the
reusable start of the prompt as sent, and where a call's cost comes from.

Additive: nullable columns only; nothing existing changes."""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("cache_write_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("prompt_prefix_sha256", sa.String(length=64), nullable=True))
    op.add_column("agent_runs", sa.Column("gateway_request_id", sa.String(length=64), nullable=True))
    op.add_column("provider_calls", sa.Column("cache_write_tokens", sa.Integer(), nullable=True))
    op.add_column("provider_calls", sa.Column("prompt_prefix_sha256", sa.String(length=64), nullable=True))
    op.add_column("provider_calls", sa.Column("cost_source", sa.String(length=24), nullable=True))


def downgrade() -> None:
    for column in ("cost_source", "prompt_prefix_sha256", "cache_write_tokens"):
        op.drop_column("provider_calls", column)
    for column in ("gateway_request_id", "prompt_prefix_sha256", "cache_write_tokens"):
        op.drop_column("agent_runs", column)
