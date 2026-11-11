"""The short clips of a case: one row per case, language, clip and version (2026-11-11)."""

import sqlalchemy as sa
from alembic import op

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_clips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("clip_key", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False, server_default="master"),
        sa.Column("story_version_id", sa.Integer(), sa.ForeignKey("story_versions.id"), nullable=True),
        sa.Column("master_clip_id", sa.Integer(), sa.ForeignKey("short_clips.id"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("script_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("media_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("generation_model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for col in ("case_id", "language", "clip_key", "story_version_id", "status"):
        op.create_index(f"ix_short_clips_{col}", "short_clips", [col])


def downgrade() -> None:
    op.drop_table("short_clips")
