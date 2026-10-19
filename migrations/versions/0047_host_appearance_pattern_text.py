"""The host's interaction pattern is the stage director's own sentence, longer than 80 characters.

Widening only: String(80) becomes Text, every stored value is kept as it is."""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("host_appearances") as batch:
        batch.alter_column("interaction_pattern", existing_type=sa.String(length=80),
                           type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("host_appearances") as batch:
        batch.alter_column("interaction_pattern", existing_type=sa.Text(),
                           type_=sa.String(length=80), existing_nullable=True)
