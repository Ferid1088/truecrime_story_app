"""The outfit library: ``data/outfits`` is the single source of truth, mirrored into
``outfit_items`` (one row per photo, hash-tracked, marked missing rather than deleted)
and ``outfit_scans`` (every rescan with its counts and per-photo problems). Additive."""

import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outfit_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("studio", sa.String(length=20), nullable=False),
        sa.Column("outfit_slug", sa.String(length=120), nullable=False),
        sa.Column("situation", sa.String(length=160), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        # available | missing_on_disk | invalid
        sa.Column("status", sa.String(length=20), nullable=False, server_default="available",
                  index=True),
        # pose classification from the situation name: stance + parsed detail
        sa.Column("stance", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("pose_detail_json", sa.Text(), nullable=False, server_default="{}"),
        # per-photo problems from the import check (not blocking)
        sa.Column("issues_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("studio", "outfit_slug", "situation",
                            name="uq_outfit_items_key"),
    )
    op.create_index("ix_outfit_items_studio_outfit", "outfit_items",
                    ["studio", "outfit_slug"])
    op.create_table(
        "outfit_scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("problems_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_table("outfit_scans")
    op.drop_index("ix_outfit_items_studio_outfit", "outfit_items")
    op.drop_table("outfit_items")
