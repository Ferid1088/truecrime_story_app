"""The reading review of a spoken language: readings the owner (or the reader) fixed for a word and kept across films, and the doubts of one film's reading text that wait for the owner's answer."""

import sqlalchemy as sa
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reading_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("surface", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("sense", sa.String(120), nullable=False, server_default=""),
        sa.Column("latin", sa.String(240), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="owner"),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("language", "surface", "kind", "sense", name="uq_reading_approvals"),
    )
    op.create_table(
        "reading_doubts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("story_version_id", sa.Integer(), sa.ForeignKey("story_versions.id"), nullable=False),
        sa.Column("block_id", sa.String(40), nullable=False),
        sa.Column("word_n", sa.Integer(), nullable=False),
        sa.Column("surface", sa.String(160), nullable=False),
        sa.Column("sentence", sa.Text(), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("editor_latin", sa.String(240), nullable=False, server_default=""),
        sa.Column("checker_latin", sa.String(240), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("answer_latin", sa.String(240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("story_version_id", "block_id", "word_n", name="uq_reading_doubts"),
    )
    op.create_index("ix_reading_doubts_version_status", "reading_doubts", ["story_version_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_reading_doubts_version_status", table_name="reading_doubts")
    op.drop_table("reading_doubts")
    op.drop_table("reading_approvals")
