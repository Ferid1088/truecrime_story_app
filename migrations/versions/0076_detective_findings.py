"""Section J: the Film Experience Reviewer's findings, routed to one of four repair owners and
tracked to closure - checklist rows J1/H3/H4. A repair's own recheck creates a new finding row
(status='incomplete' or the repair closes it) linked via repairs_finding_id, rather than mutating
the original - a regression never silently erases the finding that preceded it."""

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "detective_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.Integer(), sa.ForeignKey("reveal_graphs.id"), nullable=False,
                  index=True),
        sa.Column("finding_key", sa.String(length=80), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("repair_owner", sa.String(length=30), nullable=False, index=True),
        sa.Column("observed_problem", sa.Text(), nullable=False),
        sa.Column("scene_key", sa.String(length=80), nullable=True),
        sa.Column("action_key", sa.String(length=80), nullable=True),
        sa.Column("anchor_key", sa.String(length=80), nullable=True),
        sa.Column("recommended_repair", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open", index=True),
        sa.Column("repairs_finding_id", sa.Integer(), sa.ForeignKey("detective_findings.id"),
                  nullable=True),
        sa.Column("repair_result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_id", "finding_key", name="uq_detective_finding_key"),
        sa.CheckConstraint(
            "repair_owner IN ('detective_director', 'visual_design_director', "
            "'scheduler_renderer', 'narration_workflow')",
            name="ck_detective_finding_repair_owner",
        ),
        sa.CheckConstraint("severity IN ('blocking', 'major', 'minor')",
                           name="ck_detective_finding_severity"),
        sa.CheckConstraint(
            "status IN ('open', 'repair_in_progress', 'closed', 'incomplete')",
            name="ck_detective_finding_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("detective_findings")
