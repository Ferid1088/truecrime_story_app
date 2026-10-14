"""Mark recorded provider calls as test data outside case budgets."""

import sqlalchemy as sa
from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_calls",
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        sa.text(
            "UPDATE provider_calls SET is_test_data = TRUE "
            "WHERE model = 'recorded-e2e'"
        )
    )


def downgrade() -> None:
    op.drop_column("provider_calls", "is_test_data")
