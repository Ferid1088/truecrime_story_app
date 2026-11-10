"""Removes the Persian/Arabic pronunciation system (2026-11-10): the reading approvals, doubts and resolutions, the pronunciation lexicon and memory
(entries, versions, interventions, evidence, audit log) and the phonetic benchmark (samples, labels). The downgrade recreates the tables empty."""

import sqlalchemy as sa
from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None

TABLES = (
    "phonetic_labels", "phonetic_samples", "pronunciation_audit_log", "pronunciation_evidence", "pronunciation_interventions",
    "pronunciation_entry_versions", "pronunciation_entries", "reading_doubts", "reading_approvals", "reading_resolutions", "pronunciation_lexicon",
)


def stamps() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade() -> None:
    for table in TABLES:
        op.drop_table(table)


def downgrade() -> None:
    op.create_table(
        "pronunciation_lexicon",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("surface", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="alias"),
        sa.Column("reading", sa.String(240), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="reviewer"),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("language", "surface", name="uq_pronunciation_lexicon"),
    )
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
        sa.Column("status", sa.String(24), nullable=False, server_default="candidate"),
        sa.Column("source", sa.String(12), nullable=False, server_default="agent"),
        sa.Column("origin", sa.String(40), nullable=False, server_default=""),
        sa.Column("origin_ref", sa.String(80), nullable=False, server_default=""),
        sa.Column("origin_data", sa.Text(), nullable=False, server_default="{}"),
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
    op.create_index("ix_pronunciation_entries_origin", "pronunciation_entries", ["origin", "origin_ref"])
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
        sa.Column("purpose", sa.String(20), nullable=False, server_default="validation"),
        sa.Column("intended_key", sa.String(80), nullable=True),
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
    op.create_table(
        "reading_resolutions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("surface", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reading", sa.String(240), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("candidates", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("language", "case_id", "surface", name="uq_reading_resolutions"),
    )
