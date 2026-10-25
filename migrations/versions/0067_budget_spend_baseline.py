"""Owner budget reset: ``cases.budget_spend_baseline_eur`` holds the ledger spend that was
behind the last reset to 0, so the provider_calls history stays intact while the cap is
measured against later spend. Additive."""

import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("budget_spend_baseline_eur", sa.Float(), nullable=False,
                                     server_default="0"))


def downgrade() -> None:
    op.drop_column("cases", "budget_spend_baseline_eur")
