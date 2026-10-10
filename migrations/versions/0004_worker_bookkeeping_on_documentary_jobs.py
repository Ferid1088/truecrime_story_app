"""Worker bookkeeping on documentary jobs: who holds a job, when it last reported, automatic retries.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documentary_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("worker_id", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("auto_resumes", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    raise RuntimeError("0004 is not reversible in place; restore the backup instead.")
