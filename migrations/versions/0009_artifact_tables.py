"""Addressable artifact graph and edit log.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=180), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("production_id", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("input_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="fresh"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("edit_id", sa.String(length=100), nullable=True),
        sa.Column("stage_job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["stage_job_id"], ["documentary_jobs.id"]),
    )
    op.create_index("ix_artifacts_case_id", "artifacts", ["case_id"])
    op.create_index("ix_artifacts_production_id", "artifacts", ["production_id"])
    op.create_index("ix_artifacts_language", "artifacts", ["language"])
    op.create_index("ix_artifacts_type", "artifacts", ["type"])
    op.create_index("ix_artifacts_status", "artifacts", ["status"])
    op.create_index("ix_artifacts_case_type", "artifacts", ["case_id", "type"])
    op.create_index("ix_artifacts_scope", "artifacts", ["case_id", "language", "status"])
    op.create_index("ix_artifacts_stage_job_id", "artifacts", ["stage_job_id"])

    op.create_table(
        "artifact_deps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artifact_id", sa.String(length=180), nullable=False),
        sa.Column("depends_on_id", sa.String(length=180), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["depends_on_id"], ["artifacts.id"]),
        sa.UniqueConstraint("artifact_id", "depends_on_id", name="uq_artifact_deps_edge"),
    )
    op.create_index("ix_artifact_deps_artifact_id", "artifact_deps", ["artifact_id"])
    op.create_index("ix_artifact_deps_depends_on_id", "artifact_deps", ["depends_on_id"])

    op.create_table(
        "edit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=True),
        sa.Column("scope_type", sa.String(length=80), nullable=True),
        sa.Column("scope_id", sa.String(length=180), nullable=True),
        sa.Column("instruction_text", sa.Text(), nullable=True),
        sa.Column("intent_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("scope_preview_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="previewed"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("parent_edit_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_edit_log_created_at", "edit_log", ["created_at"])
    op.create_index("ix_edit_log_scope_type", "edit_log", ["scope_type"])
    op.create_index("ix_edit_log_scope_id", "edit_log", ["scope_id"])
    op.create_index("ix_edit_log_status", "edit_log", ["status"])
    op.create_index("ix_edit_log_parent_edit_id", "edit_log", ["parent_edit_id"])


def downgrade() -> None:
    op.drop_index("ix_edit_log_parent_edit_id", table_name="edit_log")
    op.drop_index("ix_edit_log_status", table_name="edit_log")
    op.drop_index("ix_edit_log_scope_id", table_name="edit_log")
    op.drop_index("ix_edit_log_scope_type", table_name="edit_log")
    op.drop_index("ix_edit_log_created_at", table_name="edit_log")
    op.drop_table("edit_log")
    op.drop_index("ix_artifact_deps_depends_on_id", table_name="artifact_deps")
    op.drop_index("ix_artifact_deps_artifact_id", table_name="artifact_deps")
    op.drop_table("artifact_deps")
    for index in ("ix_artifacts_stage_job_id", "ix_artifacts_scope", "ix_artifacts_case_type",
                  "ix_artifacts_status", "ix_artifacts_type", "ix_artifacts_language",
                  "ix_artifacts_production_id", "ix_artifacts_case_id"):
        op.drop_index(index, table_name="artifacts")
    op.drop_table("artifacts")
