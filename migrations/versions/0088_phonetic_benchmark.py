"""The phonetic benchmark: audio samples of words (from a case's narration, recorded or uploaded) with their candidate pronunciations, and every opinion about
what was really said (agent, recognizer, person) kept apart."""

import sqlalchemy as sa
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phonetic_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("story_version_id", sa.Integer(), sa.ForeignKey("story_versions.id"), nullable=True),
        sa.Column("block_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("word_n", sa.Integer(), nullable=True),
        sa.Column("surface", sa.String(160), nullable=False),
        sa.Column("sentence", sa.Text(), nullable=False, server_default=""),
        sa.Column("audio_path", sa.String(400), nullable=False),
        sa.Column("start_s", sa.Float(), nullable=True),
        sa.Column("end_s", sa.Float(), nullable=True),
        sa.Column("voice_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("speaker", sa.String(60), nullable=False, server_default=""),
        sa.Column("speed", sa.String(20), nullable=False, server_default=""),
        sa.Column("category", sa.String(24), nullable=False, server_default="other"),
        sa.Column("candidates", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("expected_key", sa.String(80), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="unlabeled"),
        sa.Column("consensus_key", sa.String(80), nullable=True),
        sa.Column("human_key", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("language", "audio_path", name="uq_phonetic_samples"),
    )
    op.create_index("ix_phonetic_samples_status", "phonetic_samples", ["language", "status"])
    op.create_table(
        "phonetic_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sample_id", sa.Integer(), sa.ForeignKey("phonetic_samples.id"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("actor", sa.String(60), nullable=False),
        sa.Column("choice", sa.String(80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_phonetic_labels_sample_id", "phonetic_labels", ["sample_id"])


def downgrade() -> None:
    op.drop_table("phonetic_labels")
    op.drop_table("phonetic_samples")
