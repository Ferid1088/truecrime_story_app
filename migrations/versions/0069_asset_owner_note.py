"""The owner's note on a picture or clip (who or what it shows), which the planner uses to place it. Additive."""

import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("visual_assets", sa.Column("owner_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("visual_assets", "owner_note")
