"""Persist legal-risk warnings on discovery candidates."""

import sqlalchemy as sa
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("discovery_candidates", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("legal_risk", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("legal_risk_reasons_json", sa.Text(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("discovery_candidates", schema=None) as batch_op:
        batch_op.drop_column("legal_risk_reasons_json")
        batch_op.drop_column("legal_risk")
