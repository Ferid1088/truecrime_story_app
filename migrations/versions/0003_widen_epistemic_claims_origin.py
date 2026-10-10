"""Widen epistemic_claims.origin from 20 to 40 characters.

The pipeline writes "master_story_extraction" (23 characters). SQLite never enforced the
declared length, so it went unnoticed; Postgres does, and refused the copy of the real data.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("epistemic_claims", schema=None) as batch_op:
        batch_op.alter_column("origin", existing_type=sa.VARCHAR(length=20), type_=sa.String(length=40),
                              existing_nullable=False)


def downgrade() -> None:
    raise RuntimeError("0003 is not reversible in place (narrowing could cut data); restore the backup instead.")
