"""A case's EUR budget cap now defaults to null (tracks the live configured
default), instead of a hardcoded 20.0 baked in at creation time — raising
config/parameters/policies.json's budget.total_cap_eur later had no effect
on any case created before the change."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.alter_column(
            "total_cost_cap_eur",
            existing_type=sa.Float(),
            nullable=True,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.alter_column(
            "total_cost_cap_eur",
            existing_type=sa.Float(),
            nullable=False,
            server_default="20",
        )
