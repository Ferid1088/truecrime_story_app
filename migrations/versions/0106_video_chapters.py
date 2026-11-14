"""The YouTube chapters of a film per case and language: the list resolved on the finished timeline, its status, what it was made from and the estimated extra cost the chapters
added to the chapter writer's existing calls (2026-11-14)."""

import sqlalchemy as sa
from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_chapters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=True),
        sa.Column("production_script_id", sa.Integer(), nullable=True),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="no_render"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("chapters_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("method", sa.String(200), nullable=True),
        sa.Column("generation_model", sa.String(200), nullable=True),
        sa.Column("cost_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("case_id", "language", name="uq_video_chapters"),
    )
    op.create_index("ix_video_chapters_case_id", "video_chapters", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_video_chapters_case_id", table_name="video_chapters")
    op.drop_table("video_chapters")
