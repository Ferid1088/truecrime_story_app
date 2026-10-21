"""Pictures: the time the files of a rejected picture were removed after the retention period (its
metadata, hashes and the reason it was rejected stay).

Additive: one nullable column; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("visual_assets", sa.Column("file_removed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("visual_assets", "file_removed_at")
