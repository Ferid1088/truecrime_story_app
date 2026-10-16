"""Part 2(e) item 1: record why fact extraction skipped a source (no
case-specific content, a near-duplicate/syndicated copy, or ranked below
the per-case extraction limit), instead of silently dropping it."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sources", schema=None) as batch_op:
        batch_op.add_column(sa.Column("extraction_skip_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sources", schema=None) as batch_op:
        batch_op.drop_column("extraction_skip_reason")
