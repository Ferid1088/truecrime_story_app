"""Facebook Page publishing (2026-11-17): the persisted upload sessions of the Facebook publisher (Reels and long films). A new table only; nothing existing is changed. At most one
session per publication is active, and one file goes to one Page at most once (two partial unique indexes)."""

import sqlalchemy as sa
from alembic import op

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None

ACTIVE = "status IN ('created', 'uploading', 'interrupted', 'processing', 'ready')"
ONCE = "status IN ('created', 'uploading', 'interrupted', 'processing', 'ready', 'drafted', 'published', 'uncertain')"


def upgrade() -> None:
    op.create_table(
        "facebook_upload_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("publication_id", sa.Integer(), sa.ForeignKey("publications.id"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False, server_default="short"),
        sa.Column("status", sa.String(12), nullable=False, server_default="created"),
        sa.Column("page_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("metadata_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("remote_video_id", sa.String(60), nullable=True),
        sa.Column("upload_ref", sa.Text(), nullable=True),
        sa.Column("file_handle", sa.Text(), nullable=True),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_sent", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("video_status", sa.String(20), nullable=True),
        sa.Column("publish_status", sa.String(20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("required_action", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_facebook_upload_sessions_publication_id", "facebook_upload_sessions", ["publication_id"])
    op.create_index("ix_facebook_upload_sessions_status", "facebook_upload_sessions", ["status"])
    op.create_index("uq_facebook_session_active", "facebook_upload_sessions", ["publication_id"], unique=True, postgresql_where=sa.text(ACTIVE), sqlite_where=sa.text(ACTIVE))
    op.create_index("uq_facebook_file_once", "facebook_upload_sessions", ["page_id", "content_hash"], unique=True, postgresql_where=sa.text(ONCE), sqlite_where=sa.text(ONCE))


def downgrade() -> None:
    op.drop_index("uq_facebook_file_once", table_name="facebook_upload_sessions")
    op.drop_index("uq_facebook_session_active", table_name="facebook_upload_sessions")
    op.drop_index("ix_facebook_upload_sessions_status", table_name="facebook_upload_sessions")
    op.drop_index("ix_facebook_upload_sessions_publication_id", table_name="facebook_upload_sessions")
    op.drop_table("facebook_upload_sessions")
