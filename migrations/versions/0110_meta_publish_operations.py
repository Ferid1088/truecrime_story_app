"""The live Instagram Reels operations of the Meta publishing module (2026-11-15): one row per container made for a clip file, with a guard that a file is published once per account."""

import sqlalchemy as sa
from alembic import op

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None

_ONCE = "status IN ('published', 'publish_uncertain')"


def upgrade() -> None:
    op.create_table(
        "meta_publish_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("clip_key", sa.String(10), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False, server_default="instagram_reel"),
        sa.Column("account_id", sa.String(40), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("video_path", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="created"),
        sa.Column("container_id", sa.String(80), nullable=True),
        sa.Column("media_id", sa.String(80), nullable=True),
        sa.Column("permalink", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_meta_publish_operations_case_id", "meta_publish_operations", ["case_id"])
    op.create_index("ix_meta_publish_operations_content_hash", "meta_publish_operations", ["content_hash"])
    op.create_index("ix_meta_publish_operations_status", "meta_publish_operations", ["status"])
    op.create_index("uq_meta_publish_once", "meta_publish_operations", ["platform", "account_id", "content_hash"], unique=True,
                    postgresql_where=sa.text(_ONCE), sqlite_where=sa.text(_ONCE))


def downgrade() -> None:
    op.drop_index("uq_meta_publish_once", table_name="meta_publish_operations")
    op.drop_index("ix_meta_publish_operations_status", table_name="meta_publish_operations")
    op.drop_index("ix_meta_publish_operations_content_hash", table_name="meta_publish_operations")
    op.drop_index("ix_meta_publish_operations_case_id", table_name="meta_publish_operations")
    op.drop_table("meta_publish_operations")
