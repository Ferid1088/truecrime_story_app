"""Part 5b: every story version records the writer prompt (name and sha256)
that produced its text."""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("story_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("writer_prompt_name", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("writer_prompt_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("story_versions", schema=None) as batch_op:
        batch_op.drop_column("writer_prompt_sha256")
        batch_op.drop_column("writer_prompt_name")
