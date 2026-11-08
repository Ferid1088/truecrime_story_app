"""The production choices of a case (languages), saved on the case page before the Autopilot is pressed."""

import sqlalchemy as sa
from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_production",
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), primary_key=True),
        sa.Column("languages_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("case_production")
