"""Architecture correction (2026-10-28): the Main Visual Planner's recorded accept/combine/defer/
reject decision on each Detective Director tool action - checklist row D5."""

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("detective_tool_actions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("planner_decision", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("planner_decision_reason", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_detective_action_planner_decision",
            "planner_decision IS NULL OR planner_decision IN "
            "('accepted', 'combined', 'deferred', 'rejected')",
        )


def downgrade() -> None:
    with op.batch_alter_table("detective_tool_actions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_detective_action_planner_decision", type_="check")
        batch_op.drop_column("planner_decision_reason")
        batch_op.drop_column("planner_decision")
