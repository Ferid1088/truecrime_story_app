"""The detective board's cards, for every language of a blueprint, kept so a later render always pastes every earlier card first.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "board_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("blueprint_id", sa.Integer(), sa.ForeignKey("editorial_blueprints.id"),
                  nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="approved"),
        sa.Column("languages_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("plan_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("case_id", "blueprint_id", "status"):
        op.create_index(f"ix_board_plans_{col}", "board_plans", [col])


def downgrade() -> None:
    op.drop_table("board_plans")
