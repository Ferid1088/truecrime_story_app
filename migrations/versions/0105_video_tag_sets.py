"""The YouTube tags of a case in one language: the working list, who wrote it, its approval, a waiting regeneration, the checks and the fingerprint of the facts it was
written from (2026-11-14)."""

import sqlalchemy as sa
from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_tag_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("origin", sa.String(10), nullable=False, server_default="ai"),
        sa.Column("approval", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("generation_status", sa.String(10), nullable=False, server_default="none"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("candidate_json", sa.Text(), nullable=True),
        sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_fingerprint", sa.String(64), nullable=True),
        sa.Column("generator_version", sa.Integer(), nullable=True),
        sa.Column("generation_model", sa.String(200), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "language", name="uq_video_tag_set"),
    )
    op.create_index("ix_video_tag_sets_case_id", "video_tag_sets", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_video_tag_sets_case_id", table_name="video_tag_sets")
    op.drop_table("video_tag_sets")
