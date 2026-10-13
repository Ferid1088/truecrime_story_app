"""Production Shorts candidates, language renders and script metadata.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("film_job_id", sa.Integer(), nullable=False),
        sa.Column("film_version_id", sa.Integer(), nullable=True),
        sa.Column("stable_key", sa.String(120), nullable=False),
        sa.Column("concept_type", sa.String(60), nullable=False, server_default="MYSTERY_HOOK"),
        sa.Column("source_beat_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_chapter_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_start_seconds", sa.Float(), nullable=True),
        sa.Column("source_end_seconds", sa.Float(), nullable=True),
        sa.Column("hook", sa.Text(), nullable=False, server_default=""),
        sa.Column("cta", sa.Text(), nullable=False, server_default=""),
        sa.Column("gate_results_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("claims_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("selection_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(30), nullable=False, server_default="generated"),
        sa.Column("approval_state", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["film_job_id"], ["documentary_jobs.id"]),
        sa.ForeignKeyConstraint(["film_version_id"], ["videos.id"]),
        sa.UniqueConstraint("film_job_id", "stable_key", name="uq_short_candidate_film_key"),
    )
    op.create_index("ix_short_candidates_case_id", "short_candidates", ["case_id"])
    op.create_index("ix_short_candidates_film_job_id", "short_candidates", ["film_job_id"])
    op.create_index("ix_short_candidates_film_version_id", "short_candidates", ["film_version_id"])
    op.create_index("ix_short_candidates_stable_key", "short_candidates", ["stable_key"])
    op.create_index("ix_short_candidates_status", "short_candidates", ["status"])
    op.create_index("ix_short_candidates_approval_state", "short_candidates", ["approval_state"])
    op.create_index("ix_short_candidates_content_hash", "short_candidates", ["content_hash"])
    op.create_index("ix_short_candidates_case_status", "short_candidates", ["case_id", "status"])

    op.create_table(
        "rendered_shorts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("film_job_id", sa.Integer(), nullable=False),
        sa.Column("film_version_id", sa.Integer(), nullable=True),
        sa.Column("script_id", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("approval_state", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("caption_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("cta", sa.Text(), nullable=False, server_default=""),
        sa.Column("captions_path", sa.Text(), nullable=True),
        sa.Column("video_path", sa.Text(), nullable=True),
        sa.Column("cover_path", sa.Text(), nullable=True),
        sa.Column("manifest_path", sa.Text(), nullable=True),
        sa.Column("files_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("gate_results_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["short_candidates.id"]),
        sa.ForeignKeyConstraint(["film_job_id"], ["documentary_jobs.id"]),
        sa.ForeignKeyConstraint(["film_version_id"], ["videos.id"]),
        sa.ForeignKeyConstraint(["script_id"], ["short_form_scripts.id"]),
        sa.UniqueConstraint("candidate_id", "language", name="uq_rendered_short_candidate_language"),
    )
    for name, cols in {
        "ix_rendered_shorts_case_id": ["case_id"],
        "ix_rendered_shorts_candidate_id": ["candidate_id"],
        "ix_rendered_shorts_film_job_id": ["film_job_id"],
        "ix_rendered_shorts_film_version_id": ["film_version_id"],
        "ix_rendered_shorts_script_id": ["script_id"],
        "ix_rendered_shorts_language": ["language"],
        "ix_rendered_shorts_status": ["status"],
        "ix_rendered_shorts_approval_state": ["approval_state"],
        "ix_rendered_shorts_content_hash": ["content_hash"],
        "ix_rendered_shorts_case_language": ["case_id", "language"],
    }.items():
        op.create_index(name, "rendered_shorts", cols)
    with op.batch_alter_table("short_form_scripts") as batch:
        batch.add_column(sa.Column("candidate_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("caption_text", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("captions_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("audit_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("approval_state", sa.String(30), nullable=False, server_default="pending"))
        batch.create_foreign_key("fk_short_form_scripts_candidate_id", "short_candidates", ["candidate_id"], ["id"])
        batch.create_index("ix_short_form_scripts_candidate_id", ["candidate_id"])
        batch.create_index("ix_short_form_scripts_content_hash", ["content_hash"])
        batch.create_index("ix_short_form_scripts_approval_state", ["approval_state"])


def downgrade() -> None:
    # The production upgrade is reversible for disposable/test databases. A
    # live rollback should still be preceded by the normal backup procedure.
    with op.batch_alter_table("short_form_scripts") as batch:
        batch.drop_index("ix_short_form_scripts_approval_state")
        batch.drop_index("ix_short_form_scripts_content_hash")
        batch.drop_index("ix_short_form_scripts_candidate_id")
        batch.drop_constraint("fk_short_form_scripts_candidate_id", type_="foreignkey")
        for column in ("approval_state", "audit_json", "content_hash", "captions_json",
                       "caption_text", "candidate_id"):
            batch.drop_column(column)
    op.drop_table("rendered_shorts")
    op.drop_index("ix_short_candidates_case_status", table_name="short_candidates")
    op.drop_index("ix_short_candidates_content_hash", table_name="short_candidates")
    op.drop_index("ix_short_candidates_approval_state", table_name="short_candidates")
    op.drop_index("ix_short_candidates_status", table_name="short_candidates")
    op.drop_index("ix_short_candidates_stable_key", table_name="short_candidates")
    op.drop_index("ix_short_candidates_film_version_id", table_name="short_candidates")
    op.drop_index("ix_short_candidates_film_job_id", table_name="short_candidates")
    op.drop_index("ix_short_candidates_case_id", table_name="short_candidates")
    op.drop_table("short_candidates")
