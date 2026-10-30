"""The owner's selection in the picture review: a checked picture or clip must be used 100 % in the film (`must_use`), and a
photo can be given a draw priority 1 to 10 for the pencil drawing (`draw_priority`)."""

import sqlalchemy as sa
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("visual_assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("must_use", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("draw_priority", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("visual_assets", schema=None) as batch_op:
        batch_op.drop_column("draw_priority")
        batch_op.drop_column("must_use")
