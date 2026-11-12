"""A rejected picture entry the owner cleared from the picture review keeps its verdict (so the planner never judges the
picture again for that requirement) but is no longer listed (2026-11-12)."""

import sqlalchemy as sa
from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mvp_candidates", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("mvp_candidates", "dismissed_at")
