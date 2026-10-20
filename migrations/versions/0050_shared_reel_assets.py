"""The shared, content-addressed store for reusable host-led-reel assets (outro clips, end
cards, cover templates, music library entries), created once and reused by every reel.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shared_reel_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_key", sa.String(64), nullable=False),
        sa.Column("asset_type", sa.String(40), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parameters_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_shared_reel_assets_asset_key", "shared_reel_assets", ["asset_key"], unique=True)
    for col in ("asset_type", "language"):
        op.create_index(f"ix_shared_reel_assets_{col}", "shared_reel_assets", [col])


def downgrade() -> None:
    op.drop_table("shared_reel_assets")
