"""Entries imported from the earlier pronunciation systems (reading approvals, the pronunciation lexicon) are kept as legacy_unverified with their origin and
their original data; the status column gets room for that word."""

import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pronunciation_entries") as batch:
        batch.alter_column("status", existing_type=sa.String(16), type_=sa.String(24), existing_nullable=False, existing_server_default="candidate")
    op.add_column("pronunciation_entries", sa.Column("origin", sa.String(40), nullable=False, server_default=""))
    op.add_column("pronunciation_entries", sa.Column("origin_ref", sa.String(80), nullable=False, server_default=""))
    op.add_column("pronunciation_entries", sa.Column("origin_data", sa.Text(), nullable=False, server_default="{}"))
    op.create_index("ix_pronunciation_entries_origin", "pronunciation_entries", ["origin", "origin_ref"])


def downgrade() -> None:
    op.execute("DELETE FROM pronunciation_audit_log WHERE entry_id IN (SELECT id FROM pronunciation_entries WHERE status = 'legacy_unverified')")
    op.execute("DELETE FROM pronunciation_entry_versions WHERE entry_id IN (SELECT id FROM pronunciation_entries WHERE status = 'legacy_unverified')")
    op.execute("DELETE FROM pronunciation_entries WHERE status = 'legacy_unverified'")
    op.drop_index("ix_pronunciation_entries_origin", table_name="pronunciation_entries")
    op.drop_column("pronunciation_entries", "origin_data")
    op.drop_column("pronunciation_entries", "origin_ref")
    op.drop_column("pronunciation_entries", "origin")
    with op.batch_alter_table("pronunciation_entries") as batch:
        batch.alter_column("status", existing_type=sa.String(24), type_=sa.String(16), existing_nullable=False, existing_server_default="candidate")
