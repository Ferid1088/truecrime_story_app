"""Part 6g: the persona file name and sha256 stored with every host plan, host
segment set and host scene. Older rows keep NULL: shown as "unknown persona"."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_TABLES = ("host_plans", "host_segments", "host_scenes")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("persona_file", sa.String(length=300), nullable=True))
            batch_op.add_column(sa.Column("persona_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("persona_sha256")
            batch_op.drop_column("persona_file")
