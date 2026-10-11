"""Attribute documentary model runs to jobs, languages and pipeline stages.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("documentary_job_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("language", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("pipeline_stage", sa.String(length=80), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_runs_documentary_job_id", "documentary_jobs",
            ["documentary_job_id"], ["id"],
        )
        batch_op.create_index("ix_agent_runs_documentary_job_id", ["documentary_job_id"])
        batch_op.create_index("ix_agent_runs_language", ["language"])
        batch_op.create_index("ix_agent_runs_pipeline_stage", ["pipeline_stage"])


def downgrade() -> None:
    raise RuntimeError("0007 is not reversible in place; restore the backup instead.")
