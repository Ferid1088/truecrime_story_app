"""The Main Visual Planner's own records: requirements tied to the reveal graph, every external
search round, the per-requirement candidate verdicts (five separate decisions) and the persisted
owner-review state of a rendered film. Additive."""

import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mvp_requirements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("visual_plan_id", sa.Integer(), sa.ForeignKey("visual_plans.id"), nullable=False,
                  index=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=True),
        sa.Column("requirement_key", sa.String(length=80), nullable=False),
        sa.Column("beat_id", sa.String(length=20), nullable=False, index=True),
        sa.Column("node_key", sa.String(length=120), nullable=True),
        sa.Column("function", sa.String(length=30), nullable=False),
        sa.Column("entity_key", sa.String(length=120), nullable=True, index=True),
        sa.Column("role_needed", sa.String(length=20), nullable=False, server_default="context"),
        sa.Column("priority", sa.String(length=10), nullable=False, server_default="medium"),
        sa.Column("spec_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open", index=True),
        sa.Column("rounds_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shortage_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("visual_plan_id", "requirement_key", name="uq_mvp_requirement_key"),
    )
    op.create_table(
        "mvp_search_rounds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("mvp_requirements.id"), nullable=False,
                  index=True),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("new_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_eur", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "mvp_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("mvp_requirements.id"), nullable=False,
                  index=True),
        sa.Column("visual_asset_id", sa.Integer(), sa.ForeignKey("visual_assets.id"), nullable=False,
                  index=True),
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("provenance_status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("identity_status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("rights_status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("ethics_status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("scene_status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("final_status", sa.String(length=20), nullable=False, server_default="pending",
                  index=True),
        sa.Column("reasons_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("verifiers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("requirement_id", "visual_asset_id", name="uq_mvp_candidate"),
    )
    op.create_table(
        "mvp_film_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("production_script_id", sa.Integer(), sa.ForeignKey("production_scripts.id"),
                  nullable=True),
        sa.Column("render_path", sa.Text(), nullable=True),
        sa.Column("render_sha256", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False, server_default="IMPLEMENTING",
                  index=True),
        sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("owner_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for table in ("mvp_film_reviews", "mvp_candidates", "mvp_search_rounds", "mvp_requirements"):
        op.drop_table(table)
