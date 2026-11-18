"""Covers (2026-11-17): the three coordinated shapes of a Short's cover (reel 9:16, feed 4:5, grid 1:1), one row per shape and version, each reviewed and approved on its own. A new table only;
nothing existing is changed and no existing cover is touched."""

import sqlalchemy as sa
from alembic import op

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cover_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("cover_key", sa.String(10), nullable=False),
        sa.Column("shape", sa.String(8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(12), nullable=False, server_default="proposed"),
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("cover_proposals.id"), nullable=True),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("highlight", sa.String(100), nullable=False, server_default=""),
        sa.Column("subject_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("photo_asset_id", sa.Integer(), sa.ForeignKey("visual_assets.id"), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("file_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("elements_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("problems_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "language", "cover_key", "shape", "version", name="uq_cover_variant_version"),
    )
    op.create_index("ix_cover_variants_case_id", "cover_variants", ["case_id"])
    op.create_index("ix_cover_variants_language", "cover_variants", ["language"])
    op.create_index("ix_cover_variants_status", "cover_variants", ["status"])


def downgrade() -> None:
    op.drop_index("ix_cover_variants_status", table_name="cover_variants")
    op.drop_index("ix_cover_variants_language", table_name="cover_variants")
    op.drop_index("ix_cover_variants_case_id", table_name="cover_variants")
    op.drop_table("cover_variants")
