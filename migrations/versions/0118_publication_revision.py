"""Publishing (2026-11-19): a Short that is already published can have a NEW publication for a new clip of the same key (a revision), beside the old one. `publications.revision` (1 for every
existing row) joins the unique key `uq_publication_item`, so the old published row is never touched or replaced; the protection against publishing the same file twice (`uq_publication_once`) stays."""

import sqlalchemy as sa
from alembic import op

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None

NAME = "uq_publication_item"
KEY = ["case_id", "language", "platform", "kind", "asset_key"]


def _constraint_names() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_unique_constraints("publications")}


def upgrade() -> None:
    with op.batch_alter_table("publications") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("publications") as batch:
        if NAME in _constraint_names():
            batch.drop_constraint(NAME, type_="unique")
        batch.create_unique_constraint(NAME, [*KEY, "revision"])


def downgrade() -> None:
    with op.batch_alter_table("publications") as batch:
        batch.drop_constraint(NAME, type_="unique")
        batch.create_unique_constraint(NAME, KEY)
    with op.batch_alter_table("publications") as batch:
        batch.drop_column("revision")
