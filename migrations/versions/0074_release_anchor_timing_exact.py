"""Section F: record whether a release anchor's resolved timing came from real word-level
audio alignment or a sentence/beat-level proportional fallback - checklist row F1."""

import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("release_anchors", schema=None) as batch_op:
        batch_op.add_column(sa.Column("timing_exact", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("release_anchors", schema=None) as batch_op:
        batch_op.drop_column("timing_exact")
