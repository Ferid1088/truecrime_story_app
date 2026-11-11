"""The film's opening block (hook, context text, music brief): one row per case, language and version (2026-11-11)."""

import sqlalchemy as sa
from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "film_openings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False, server_default="master"),
        sa.Column("story_version_id", sa.Integer(), sa.ForeignKey("story_versions.id"), nullable=True),
        sa.Column("master_opening_id", sa.Integer(), sa.ForeignKey("film_openings.id"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("opening_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("media_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("generation_model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for col in ("case_id", "language", "story_version_id", "status"):
        op.create_index(f"ix_film_openings_{col}", "film_openings", [col])


def downgrade() -> None:
    op.drop_table("film_openings")
