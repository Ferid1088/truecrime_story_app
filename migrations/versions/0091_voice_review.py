"""The hand review of a voiced narration: the review of a narration, its blocks (yellow once corrected), the words corrected (red for good) and the
takes of each block (one of them active)."""

import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_reviews",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("story_version_id", sa.Integer, sa.ForeignKey("story_versions.id"), nullable=False, index=True),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("assembled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assembled_sha256", sa.String(64), nullable=True),
        sa.Column("assembled_takes_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("story_version_id", name="uq_voice_review_version"),
    )
    op.create_table(
        "voice_blocks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("review_id", sa.Integer, sa.ForeignKey("voice_reviews.id"), nullable=False, index=True),
        sa.Column("block_id", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("canonical_text", sa.Text, nullable=False),
        sa.Column("voice_text", sa.Text, nullable=False),
        sa.Column("draft_text", sa.Text, nullable=True),
        sa.Column("edited", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("review_id", "block_id", name="uq_voice_block"),
    )
    op.create_table(
        "voice_block_words",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("block_pk", sa.Integer, sa.ForeignKey("voice_blocks.id"), nullable=False, index=True),
        sa.Column("word_n", sa.Integer, nullable=False),
        sa.Column("was", sa.String(200), nullable=False),
        sa.Column("surface", sa.String(200), nullable=False),
        sa.Column("finglish", sa.String(200), nullable=False, server_default=""),
        sa.Column("pending", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("block_pk", "word_n", name="uq_voice_block_word"),
    )
    op.create_table(
        "voice_block_takes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("block_pk", sa.Integer, sa.ForeignKey("voice_blocks.id"), nullable=False, index=True),
        sa.Column("take_no", sa.Integer, nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("text_sent", sa.Text, nullable=False),
        sa.Column("request_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("aliases_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("characters", sa.Integer, nullable=True),
        sa.Column("cost_eur", sa.Float, nullable=False, server_default="0"),
        sa.Column("duration_s", sa.Float, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("block_pk", "take_no", name="uq_voice_block_take"),
    )


def downgrade() -> None:
    op.drop_table("voice_block_takes")
    op.drop_table("voice_block_words")
    op.drop_table("voice_blocks")
    op.drop_table("voice_reviews")
