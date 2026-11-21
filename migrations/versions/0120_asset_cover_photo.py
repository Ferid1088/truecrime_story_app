"""The owner's choice of a photograph of a person for the covers in the picture review (`cover_photo`): the covers use the chosen photos first."""

import sqlalchemy as sa
from alembic import op

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("visual_assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cover_photo", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("visual_assets", schema=None) as batch_op:
        batch_op.drop_column("cover_photo")
