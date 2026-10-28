"""The outfit and photos the owner chose last for each language, pre-filled for the next film. Additive."""

import sqlalchemy as sa
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outfit_defaults",
        sa.Column("language", sa.String(length=20), primary_key=True),
        sa.Column("outfit_ref", sa.String(length=200), nullable=False),
        sa.Column("item_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("outfit_defaults")
