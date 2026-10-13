"""Durable, lease-based Shorts production queue.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_production_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unique_key", sa.String(180), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("film_job_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("rendered_short_id", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String(120), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("output_hash", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["film_job_id"], ["documentary_jobs.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["short_candidates.id"]),
        sa.ForeignKeyConstraint(["rendered_short_id"], ["rendered_shorts.id"]),
        sa.UniqueConstraint("unique_key", name="uq_short_production_job_key"),
    )
    for name, cols in {
        "ix_short_production_jobs_case_id": ["case_id"],
        "ix_short_production_jobs_film_job_id": ["film_job_id"],
        "ix_short_production_jobs_candidate_id": ["candidate_id"],
        "ix_short_production_jobs_rendered_short_id": ["rendered_short_id"],
        "ix_short_production_jobs_language": ["language"],
        "ix_short_production_jobs_status": ["status"],
        "ix_short_production_jobs_worker_id": ["worker_id"],
        "ix_short_production_jobs_queue": ["status", "lease_until"],
        "ix_short_production_jobs_film_language": ["film_job_id", "language"],
    }.items():
        op.create_index(name, "short_production_jobs", cols)


def downgrade() -> None:
    for name in (
        "ix_short_production_jobs_film_language",
        "ix_short_production_jobs_queue",
        "ix_short_production_jobs_worker_id",
        "ix_short_production_jobs_status",
        "ix_short_production_jobs_language",
        "ix_short_production_jobs_rendered_short_id",
        "ix_short_production_jobs_candidate_id",
        "ix_short_production_jobs_film_job_id",
        "ix_short_production_jobs_case_id",
    ):
        op.drop_index(name, table_name="short_production_jobs")
    op.drop_table("short_production_jobs")
