"""Case archive: the owner's own archive mark on a case and the publish mark on a rendered short.

A case whose shorts are all published is archived without a mark of its own.

Additive: two nullable columns; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("rendered_shorts", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("rendered_shorts", "published_at")
    op.drop_column("cases", "archived_at")
