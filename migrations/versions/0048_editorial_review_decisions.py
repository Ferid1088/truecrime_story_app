"""The automatic editorial review of pictures with unclear rights keeps every decision for audit.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "editorial_review_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("visual_assets.id"), nullable=False),
        sa.Column("asset_code", sa.String(40), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("case_id", "asset_id", "asset_code", "verdict", "created_at"):
        op.create_index(f"ix_editorial_review_decisions_{col}", "editorial_review_decisions", [col])


def downgrade() -> None:
    op.drop_table("editorial_review_decisions")
