"""The pronunciation memory: words with their context, the pronunciation expected and the one a person approved, the interventions that made the voice say
it (with how often they worked), the evidence observed in generated audio, every earlier state of an entry, and an append-only audit log."""

import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def stamps() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade() -> None:
    op.create_table(
        "pronunciation_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("surface", sa.String(160), nullable=False),
        sa.Column("surface_key", sa.String(160), nullable=False),
        sa.Column("prev_word", sa.String(160), nullable=False, server_default=""),
        sa.Column("next_word", sa.String(160), nullable=False, server_default=""),
        sa.Column("sense", sa.String(120), nullable=False, server_default=""),
        sa.Column("voice_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("scope", sa.String(10), nullable=False, server_default="context"),
        sa.Column("sentence", sa.Text(), nullable=False, server_default=""),
        sa.Column("grammar", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("expected_irab", sa.Text(), nullable=False, server_default=""),
        sa.Column("observed_wrong", sa.Text(), nullable=False, server_default=""),
        sa.Column("approved_pronunciation", sa.Text(), nullable=True),
        sa.Column("approved_ipa", sa.String(240), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("source", sa.String(12), nullable=False, server_default="agent"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reviewer", sa.String(120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *stamps(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("language", "surface", "prev_word", "next_word", "sense", "voice_id", "model_id", name="uq_pronunciation_entries"),
    )
    op.create_index("ix_pronunciation_entries_key", "pronunciation_entries", ["language", "surface_key"])
    op.create_table(
        "pronunciation_entry_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("pronunciation_entries.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        *stamps(),
        sa.UniqueConstraint("entry_id", "version", name="uq_pronunciation_entry_versions"),
    )
    op.create_index("ix_pronunciation_entry_versions_entry_id", "pronunciation_entry_versions", ["entry_id"])
    op.create_table(
        "pronunciation_interventions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("pronunciation_entries.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("exact_form", sa.Text(), nullable=False),
        sa.Column("replacement", sa.Text(), nullable=False),
        sa.Column("dictionary_id", sa.String(40), nullable=True),
        sa.Column("dictionary_version_id", sa.String(40), nullable=True),
        sa.Column("voice_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="candidate"),
        sa.Column("successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_audio_ref", sa.String(400), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        *stamps(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("entry_id", "kind", "exact_form", "replacement", "voice_id", "model_id", name="uq_pronunciation_interventions"),
    )
    op.create_index("ix_pronunciation_interventions_entry_id", "pronunciation_interventions", ["entry_id"])
    op.create_table(
        "pronunciation_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("pronunciation_entries.id"), nullable=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("block_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("word_n", sa.Integer(), nullable=True),
        sa.Column("surface", sa.String(160), nullable=False),
        sa.Column("expected", sa.Text(), nullable=False, server_default=""),
        sa.Column("estimated_observed", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("verdict", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("audio_ref", sa.String(400), nullable=False, server_default=""),
        sa.Column("start_s", sa.Float(), nullable=True),
        sa.Column("end_s", sa.Float(), nullable=True),
        sa.Column("voice_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        *stamps(),
    )
    op.create_index("ix_pronunciation_evidence_entry_id", "pronunciation_evidence", ["entry_id"])
    op.create_table(
        "pronunciation_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("pronunciation_entries.id"), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("before", sa.Text(), nullable=False, server_default=""),
        sa.Column("after", sa.Text(), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        *stamps(),
    )
    op.create_index("ix_pronunciation_audit_log_entry_id", "pronunciation_audit_log", ["entry_id"])


def downgrade() -> None:
    for table in ("pronunciation_audit_log", "pronunciation_evidence", "pronunciation_interventions", "pronunciation_entry_versions", "pronunciation_entries"):
        op.drop_table(table)
