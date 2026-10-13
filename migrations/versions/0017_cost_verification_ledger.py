"""Record estimated provider usage and documentary-run attribution."""

import sqlalchemy as sa
from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_calls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("estimated", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
        batch_op.add_column(sa.Column("documentary_job_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_provider_calls_documentary_job_id", "documentary_jobs",
            ["documentary_job_id"], ["id"],
        )
        batch_op.create_index("ix_provider_calls_documentary_job_id",
                             ["documentary_job_id"])


def downgrade() -> None:
    raise RuntimeError("0017 is not reversible in place; restore the backup instead.")
