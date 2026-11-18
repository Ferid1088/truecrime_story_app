"""Publishing (2026-11-17): the unique constraint `uq_publication_item` (one publication per case, language, platform, kind and asset) on a database migrated from scratch on SQLite.

Migration 0111 asked for it inside the same batch that renames `clip_key` to `asset_key` and adds `kind`. SQLite rebuilds the table in a batch and silently leaves out a constraint that names a
column that did not exist before the batch, so a fresh SQLite database (the test database) never had it. PostgreSQL runs the statements one after the other and has always had it, so
there this migration does nothing. Here it is added in a batch of its own."""

import sqlalchemy as sa
from alembic import op

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None

NAME = "uq_publication_item"
COLUMNS = ["case_id", "language", "platform", "kind", "asset_key"]


def _has_constraint() -> bool:
    return NAME in {c["name"] for c in sa.inspect(op.get_bind()).get_unique_constraints("publications")}


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite" or _has_constraint():
        return
    with op.batch_alter_table("publications") as batch:
        batch.create_unique_constraint(NAME, COLUMNS)


def downgrade() -> None:
    """Nothing to undo: on PostgreSQL the constraint belongs to 0111, and on SQLite 0111's own downgrade drops it only when it exists."""
