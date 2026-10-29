"""The toolkit builds its cards and fixes them in the film timeline, so the Main Visual Planner no longer accepts or
rejects a tool action: the decision columns (0073) are dropped."""

import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("detective_tool_actions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_detective_action_planner_decision", type_="check")
        batch_op.drop_column("planner_decision_reason")
        batch_op.drop_column("planner_decision")


def downgrade() -> None:
    with op.batch_alter_table("detective_tool_actions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("planner_decision", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("planner_decision_reason", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_detective_action_planner_decision",
            "planner_decision IS NULL OR planner_decision IN "
            "('accepted', 'combined', 'deferred', 'rejected')",
        )
