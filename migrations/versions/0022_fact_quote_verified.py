"""Part 2(e): a deterministic (no model call) check of whether a fact's
supporting_text actually occurs in the source(s) it cites."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("facts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quote_verified", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("facts", schema=None) as batch_op:
        batch_op.drop_column("quote_verified")
