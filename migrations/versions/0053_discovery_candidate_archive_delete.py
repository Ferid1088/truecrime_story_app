"""D4 (archive and delete): `discovery_candidates` gets `archived_at` (set when
a candidate moves to the new "archived" state — permanent, fingerprint-matched
exclusion from every later Search) and `deleted_at` (set when it moves to the
new "deleted" state — a soft delete, restorable until the configured window
in config/parameters/policies.json's discovery.soft_delete_restore_days).

Additive: two nullable columns; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discovery_candidates", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "discovery_candidates", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("discovery_candidates", "deleted_at")
    op.drop_column("discovery_candidates", "archived_at")
