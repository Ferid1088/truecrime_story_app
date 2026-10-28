"""Section C of the detective toolkit: scene decisions and their ordered tool actions, scoped to
a reveal_graphs row and optionally attributed to the agent_runs row that produced them."""

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "detective_scenes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False,
                  index=True),
        sa.Column("scene_key", sa.String(length=80), nullable=False),
        sa.Column("scene_order", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("covered_unit_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("aim", sa.Text(), nullable=False),
        sa.Column("active_question_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("active_entity_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("state_before_order_seq", sa.Integer(), nullable=True),
        sa.Column("state_after_order_seq", sa.Integer(), nullable=True),
        sa.Column("change_in_understanding", sa.Text(), nullable=False),
        sa.Column("alternatives_considered_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("why_selected", sa.Text(), nullable=False),
        sa.Column("expected_audience_gain", sa.Text(), nullable=False),
        sa.Column("audio_clarity_findings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_id", "scene_key", name="uq_detective_scene_key"),
    )

    op.create_table(
        "detective_tool_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scene_id", sa.Integer(), sa.ForeignKey("detective_scenes.id"), nullable=False,
                  index=True),
        sa.Column("action_key", sa.String(length=80), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("tool_id", sa.String(length=60), nullable=False, index=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_bindings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("continuity_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("board_operations_json", sa.Text(), nullable=True),
        sa.Column("reading_requirements_json", sa.Text(), nullable=True),
        sa.Column("relationship_to_neighbors", sa.Text(), nullable=True),
        sa.Column("fallback_json", sa.Text(), nullable=True),
        sa.Column("reuse_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scene_id", "action_key", name="uq_detective_action_key"),
    )


def downgrade() -> None:
    op.drop_table("detective_tool_actions")
    op.drop_table("detective_scenes")
