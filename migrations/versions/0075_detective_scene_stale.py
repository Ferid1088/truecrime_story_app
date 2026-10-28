"""Section I: an earlier correction marks dependent scenes stale, instead of silently leaving a
film built on retracted claims or rebuilding unrelated work - checklist row I2."""

import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("detective_scenes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("stale_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("detective_scenes", schema=None) as batch_op:
        batch_op.drop_column("stale_reason")
        batch_op.drop_column("stale")
