"""Add per-language hash-bound approval bundles."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_bundles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
        sa.Column("account_connected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("bundle_hash", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_status", sa.String(20), nullable=False, server_default="not_started"),
        sa.Column("upload_id", sa.String(120), nullable=True),
        sa.Column("upload_error", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_now", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.UniqueConstraint("case_id", "language", name="uq_approval_bundle_case_language"),
    )
    op.create_index("ix_approval_bundles_case_id", "approval_bundles", ["case_id"])
    op.create_index("ix_approval_bundles_language", "approval_bundles", ["language"])
    op.create_index("ix_approval_bundles_status", "approval_bundles", ["status"])
    op.create_table(
        "approval_bundle_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bundle_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(20), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("approved_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bundle_id"], ["approval_bundles.id"]),
        sa.UniqueConstraint("bundle_id", "item_type", "item_id", name="uq_approval_bundle_item"),
    )
    op.create_index("ix_approval_bundle_items_bundle_id", "approval_bundle_items", ["bundle_id"])
    op.create_index("ix_approval_bundle_items_status", "approval_bundle_items", ["status"])
    op.create_index("ix_approval_bundle_items_bundle_status", "approval_bundle_items", ["bundle_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_approval_bundle_items_bundle_status", table_name="approval_bundle_items")
    op.drop_index("ix_approval_bundle_items_status", table_name="approval_bundle_items")
    op.drop_index("ix_approval_bundle_items_bundle_id", table_name="approval_bundle_items")
    op.drop_table("approval_bundle_items")
    op.drop_index("ix_approval_bundles_status", table_name="approval_bundles")
    op.drop_index("ix_approval_bundles_language", table_name="approval_bundles")
    op.drop_index("ix_approval_bundles_case_id", table_name="approval_bundles")
    op.drop_table("approval_bundles")
