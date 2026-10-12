"""Reconcile legacy databases without rewriting existing rows.

Revision ID: 0010
Revises: 0009

This revision is deliberately additive and idempotent. A clean database at
0009 already has every object below, so its upgrade is a no-op. SQLite cannot
add foreign keys to an existing table without a table rebuild; that remains an
explicitly documented allowlist for the legacy SQLite copy.
"""
from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _columns(name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(name)}


def _indexes(name: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(name)}


def _foreign_keys(name: str) -> set[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    return {
        (fk.get("name") or "", tuple(fk["constrained_columns"]), tuple(fk["referred_columns"]))
        for fk in _inspector().get_foreign_keys(name)
    }


def _create_missing_tables() -> None:
    if not _has_table("provider_calls"):
        op.create_table(
            "provider_calls",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("host", sa.String(length=120), nullable=False),
            sa.Column("method", sa.String(length=10), nullable=False),
            sa.Column("path", sa.String(length=300), nullable=False),
            sa.Column("status", sa.Integer(), nullable=True),
            sa.Column("ok", sa.Boolean(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("bytes_in", sa.Integer(), nullable=True),
            sa.Column("tokens_in", sa.Integer(), nullable=True),
            sa.Column("tokens_out", sa.Integer(), nullable=True),
            sa.Column("cost_usd", sa.Float(), nullable=True),
            sa.Column("error", sa.String(length=200), nullable=True),
            sa.Column("request_id", sa.String(length=64), nullable=True),
        )
    if not _has_table("app_settings"):
        op.create_table(
            "app_settings",
            sa.Column("key", sa.String(length=80), primary_key=True),
            sa.Column("value_json", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not _has_table("backup_runs"):
        op.create_table(
            "backup_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("trigger", sa.String(length=12), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False),
            sa.Column("dest_dir", sa.Text(), nullable=False),
            sa.Column("file_path", sa.Text(), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
        )
    if not _has_table("policy_events"):
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
    if not _has_table("override_log"):
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
    if not _has_table("artifacts"):
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
    if not _has_table("artifact_deps"):
        op.create_table(
            "artifact_deps",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("artifact_id", sa.String(length=180), nullable=False),
            sa.Column("depends_on_id", sa.String(length=180), nullable=False),
            sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
            sa.ForeignKeyConstraint(["depends_on_id"], ["artifacts.id"]),
            sa.UniqueConstraint("artifact_id", "depends_on_id", name="uq_artifact_deps_edge"),
        )
    if not _has_table("edit_log"):
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


def _add_columns() -> None:
    additions = {
        "documentary_jobs": (
            ("worker_id", sa.String(length=80), True, None),
            ("heartbeat_at", sa.DateTime(timezone=True), True, None),
            ("auto_resumes", sa.Integer(), False, "0"),
        ),
        "agent_runs": (
            ("documentary_job_id", sa.Integer(), True, None),
            ("language", sa.String(length=20), True, None),
            ("pipeline_stage", sa.String(length=80), True, None),
        ),
    }
    for table, columns in additions.items():
        existing = _columns(table)
        for name, column_type, nullable, server_default in columns:
            if name in existing:
                continue
            op.add_column(table, sa.Column(name, column_type, nullable=nullable,
                                           server_default=server_default))


def _create_missing_indexes() -> None:
    indexes = {
        "provider_calls": {
            "ix_provider_calls_created_at": ["created_at"],
            "ix_provider_calls_host": ["host"],
        },
        "backup_runs": {
            "ix_backup_runs_started_at": ["started_at"],
            "ix_backup_runs_status": ["status"],
        },
        "policy_events": {
            "ix_policy_events_created_at": ["created_at"],
            "ix_policy_events_case_id": ["case_id"],
            "ix_policy_events_production_id": ["production_id"],
            "ix_policy_events_language": ["language"],
        },
        "override_log": {"ix_override_log_created_at": ["created_at"]},
        "artifacts": {
            "ix_artifacts_case_id": ["case_id"],
            "ix_artifacts_production_id": ["production_id"],
            "ix_artifacts_language": ["language"],
            "ix_artifacts_type": ["type"],
            "ix_artifacts_status": ["status"],
            "ix_artifacts_case_type": ["case_id", "type"],
            "ix_artifacts_scope": ["case_id", "language", "status"],
            "ix_artifacts_stage_job_id": ["stage_job_id"],
        },
        "artifact_deps": {
            "ix_artifact_deps_artifact_id": ["artifact_id"],
            "ix_artifact_deps_depends_on_id": ["depends_on_id"],
        },
        "edit_log": {
            "ix_edit_log_created_at": ["created_at"],
            "ix_edit_log_scope_type": ["scope_type"],
            "ix_edit_log_scope_id": ["scope_id"],
            "ix_edit_log_status": ["status"],
            "ix_edit_log_parent_edit_id": ["parent_edit_id"],
        },
        "agent_runs": {
            "ix_agent_runs_documentary_job_id": ["documentary_job_id"],
            "ix_agent_runs_language": ["language"],
            "ix_agent_runs_pipeline_stage": ["pipeline_stage"],
        },
        "cases": {"ix_cases_resolution_status": ["resolution_status"]},
        "discovery_candidates": {
            "ix_discovery_candidates_case_id": ["case_id"],
            "ix_discovery_candidates_state": ["state"],
        },
        "epistemic_claims": {
            "ix_epistemic_claims_assertion_role": ["assertion_role"],
            "ix_epistemic_claims_parent_claim_key": ["parent_claim_key"],
        },
        "story_versions": {
            "ix_story_versions_case_id": ["case_id"],
            "ix_story_versions_status": ["status"],
        },
        "visual_assets": {"ix_visual_assets_entity_key": ["entity_key"]},
    }
    for table, table_indexes in indexes.items():
        existing = _indexes(table)
        for name, columns in table_indexes.items():
            if name not in existing:
                op.create_index(name, table, columns)


def _add_postgres_foreign_keys() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    foreign_keys = {
        "agent_runs": ("fk_agent_runs_documentary_job_id", ["documentary_job_id"],
                        "documentary_jobs", ["id"]),
        "story_versions": ("fk_story_versions_master_version_id", ["master_version_id"],
                            "story_versions", ["id"]),
    }
    for table, (name, columns, referred_table, referred_columns) in foreign_keys.items():
        existing = _foreign_keys(table)
        key = (name, tuple(columns), tuple(referred_columns))
        if key not in existing and not any(
                tuple(fk_columns) == tuple(columns) and tuple(fk_table_columns) == tuple(referred_columns)
                for _fk_name, fk_columns, fk_table_columns in existing):
            op.create_foreign_key(name, table, referred_table, columns, referred_columns)


def upgrade() -> None:
    _create_missing_tables()
    _add_columns()
    _create_missing_indexes()
    _add_postgres_foreign_keys()


def downgrade() -> None:
    # Deliberately a no-op: this reconciliation must never remove legacy data.
    pass
