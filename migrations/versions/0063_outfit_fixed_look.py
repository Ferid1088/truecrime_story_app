"""O2 "one fixed picture": the host outfit of a case and language may pin ONE approved look
that every host appearance of that video uses (no rotation). None keeps the stored-order
rotation of the outfit's fitting approved looks.

Additive: one nullable column; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("host_outfits", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fixed_look_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_host_outfits_fixed_look", "host_looks",
                                    ["fixed_look_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("host_outfits", schema=None) as batch_op:
        batch_op.drop_constraint("fk_host_outfits_fixed_look", type_="foreignkey")
        batch_op.drop_column("fixed_look_id")
