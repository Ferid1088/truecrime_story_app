"""Persist API background job state."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("input_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
    )
    op.create_index("ix_background_jobs_case_id", "background_jobs", ["case_id"])
    op.create_index("ix_background_jobs_kind", "background_jobs", ["kind"])
    op.create_index("ix_background_jobs_status", "background_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_background_jobs_status", table_name="background_jobs")
    op.drop_index("ix_background_jobs_kind", table_name="background_jobs")
    op.drop_index("ix_background_jobs_case_id", table_name="background_jobs")
    op.drop_table("background_jobs")
