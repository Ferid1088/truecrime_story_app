"""Part 4: a recorded human approval for editorial_review_required pictures.
Until now the "preview" render profile allowed such pictures outright; they
are now usable only with an approval (who, when) stored on the asset."""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("visual_assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("editorial_approved_by", sa.String(length=120), nullable=True))
        batch_op.add_column(
            sa.Column("editorial_approved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("visual_assets", schema=None) as batch_op:
        batch_op.drop_column("editorial_approved_at")
        batch_op.drop_column("editorial_approved_by")
