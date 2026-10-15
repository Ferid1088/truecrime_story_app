"""Let a human approve a master story "as is" over its own critics."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("story_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("approved_by", sa.String(200), nullable=True))
        batch_op.add_column(
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("story_versions", schema=None) as batch_op:
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by")
