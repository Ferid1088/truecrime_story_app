"""The YouTube description of a film per case and language: the narrative the chapter writer wrote, the description the app assembled from it and the owner's own text (2026-11-14)."""

import sqlalchemy as sa
from alembic import op

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_descriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=True),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="no_plan"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=False, server_default=""),
        sa.Column("auto_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("generation_model", sa.String(200), nullable=True),
        sa.Column("cost_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("case_id", "language", name="uq_video_descriptions"),
    )
    op.create_index("ix_video_descriptions_case_id", "video_descriptions", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_video_descriptions_case_id", table_name="video_descriptions")
    op.drop_table("video_descriptions")
