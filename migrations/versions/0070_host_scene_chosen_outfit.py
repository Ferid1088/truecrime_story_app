"""The outfit the owner chose for one host scene alone (instead of the language's one outfit). Additive."""

import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("host_scenes", sa.Column("chosen_outfit_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("host_scenes", "chosen_outfit_id")
