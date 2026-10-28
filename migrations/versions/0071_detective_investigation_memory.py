"""The detective toolkit's text-based investigation memory: narration units, release anchors,
attribute-level claims, questions, typed relations, and the single ordered knowledge-event log.
All scoped to a reveal_graphs row. Additive; also adds reveal_nodes.aliases_json."""

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reveal_nodes",
        sa.Column("aliases_json", sa.Text(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "narration_units",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False,
                  index=True),
        sa.Column("unit_key", sa.String(length=80), nullable=False),
        sa.Column("beat_id", sa.String(length=20), nullable=False, index=True),
        sa.Column("story_order", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("graph_id", "unit_key", name="uq_narration_unit_key"),
    )

    op.create_table(
        "release_anchors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False,
                  index=True),
        sa.Column("anchor_key", sa.String(length=80), nullable=False),
        sa.Column("narration_unit_id", sa.Integer(), sa.ForeignKey("narration_units.id"),
                  nullable=False, index=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("resolved_start_seconds", sa.Float(), nullable=True),
        sa.Column("resolved_end_seconds", sa.Float(), nullable=True),
        sa.UniqueConstraint("graph_id", "anchor_key", name="uq_release_anchor_key"),
    )

    op.create_table(
        "detective_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False,
                  index=True),
        sa.Column("claim_key", sa.String(length=80), nullable=False),
        sa.Column("entity_node_key", sa.String(length=120), nullable=False, index=True),
        sa.Column("attribute", sa.String(length=120), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("basis", sa.String(length=30), nullable=False, index=True),
        sa.Column("attribution", sa.String(length=300), nullable=True),
        sa.Column("supporting_unit_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("release_anchor_id", sa.Integer(), sa.ForeignKey("release_anchors.id"),
                  nullable=True),
        sa.Column("evidence_fact_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_id", "claim_key", name="uq_detective_claim_key"),
        sa.CheckConstraint(
            "basis IN ('established_in_text', 'attributed', 'disputed', 'inference', "
            "'explicit_unknown')",
            name="ck_detective_claim_basis",
        ),
    )

    op.create_table(
        "detective_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False,
                  index=True),
        sa.Column("question_key", sa.String(length=80), nullable=False),
        sa.Column("entity_node_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False, index=True),
        sa.Column("opening_anchor_id", sa.Integer(), sa.ForeignKey("release_anchors.id"),
                  nullable=True),
        sa.Column("supporting_claim_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_id", "question_key", name="uq_detective_question_key"),
        sa.CheckConstraint(
            "kind IN ('not_yet_explained', 'explicit_unresolved', 'interpretive_question')",
            name="ck_detective_question_kind",
        ),
    )

    op.create_table(
        "detective_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False,
                  index=True),
        sa.Column("relation_key", sa.String(length=80), nullable=False),
        sa.Column("from_node_key", sa.String(length=120), nullable=False, index=True),
        sa.Column("to_node_key", sa.String(length=120), nullable=False, index=True),
        sa.Column("relation_type", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("supporting_unit_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("release_anchor_id", sa.Integer(), sa.ForeignKey("release_anchors.id"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_id", "relation_key", name="uq_detective_relation_key"),
    )

    op.create_table(
        "detective_knowledge_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False,
                  index=True),
        sa.Column("order_seq", sa.Integer(), nullable=False, index=True),
        sa.Column("anchor_id", sa.Integer(), sa.ForeignKey("release_anchors.id"), nullable=True),
        sa.Column("subject_type", sa.String(length=20), nullable=False, index=True),
        sa.Column("subject_key", sa.String(length=80), nullable=False, index=True),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("prior_state_json", sa.Text(), nullable=True),
        sa.Column("new_state_json", sa.Text(), nullable=False),
        sa.Column("supporting_claim_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_id", "order_seq", name="uq_detective_knowledge_event_order"),
        sa.CheckConstraint(
            "subject_type IN ('entity', 'claim', 'question', 'relation')",
            name="ck_detective_knowledge_event_subject_type",
        ),
    )


def downgrade() -> None:
    for table in (
        "detective_knowledge_events",
        "detective_relations",
        "detective_questions",
        "detective_claims",
        "release_anchors",
        "narration_units",
    ):
        op.drop_table(table)
    op.drop_column("reveal_nodes", "aliases_json")
