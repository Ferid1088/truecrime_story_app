"""The host's outfit per section (S1 ... S4) and language, chosen before any text or scene exists."""

import sqlalchemy as sa
from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_section_outfits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("section", sa.String(20), nullable=False),
        sa.Column("outfit_id", sa.Integer(), sa.ForeignKey("host_outfits.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "language", "section", name="uq_host_section_outfit"),
    )


def downgrade() -> None:
    op.drop_table("host_section_outfits")
