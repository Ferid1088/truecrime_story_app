"""Part 3(e) item 4: a discovered video that is not transcribed (below the
minimum relevance, over a per-language or per-case cap, research stopped
early) is logged with its reason instead of silently disappearing."""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_sources", schema=None) as batch_op:
        batch_op.add_column(sa.Column("skip_reason", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("video_sources", schema=None) as batch_op:
        batch_op.drop_column("skip_reason")
