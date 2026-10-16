"""Part 4b: Brave video search results kept as links for the owner (never
downloaded, never put into a film)."""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("creator", sa.String(length=300), nullable=True),
        sa.Column("platform", sa.String(length=200), nullable=True),
        sa.Column("duration", sa.String(length=20), nullable=True),
        sa.Column("found_for", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "url", name="uq_media_links_case_url"),
    )
    op.create_index("ix_media_links_case_id", "media_links", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_media_links_case_id", table_name="media_links")
    op.drop_table("media_links")
