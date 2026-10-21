"""Videos: the allowed source class a film candidate belongs to and the evidence that put it there.

Additive: two nullable columns; no existing row changes (a video found before has no class and
stays research only until it is classified again)."""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_sources", sa.Column("source_class", sa.String(30), nullable=True))
    op.add_column("video_sources", sa.Column("class_evidence", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_sources", "class_evidence")
    op.drop_column("video_sources", "source_class")
