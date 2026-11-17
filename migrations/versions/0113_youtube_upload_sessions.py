"""YouTube publishing (2026-11-16): the persisted resumable upload sessions of the real YouTube uploader. A new table only; nothing existing is changed. At most one session per
publication is active at a time (partial unique index)."""

import sqlalchemy as sa
from alembic import op

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None

ACTIVE = "status IN ('created', 'uploading', 'interrupted', 'uploaded', 'processing')"


def upgrade() -> None:
    op.create_table(
        "youtube_upload_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("publication_id", sa.Integer(), sa.ForeignKey("publications.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="created"),
        sa.Column("session_uri", sa.Text(), nullable=True),
        sa.Column("channel_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("metadata_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("privacy", sa.String(10), nullable=False, server_default="private"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_sent", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("remote_video_id", sa.String(40), nullable=True),
        sa.Column("upload_status", sa.String(20), nullable=True),
        sa.Column("processing_status", sa.String(20), nullable=True),
        sa.Column("processing_percent", sa.Integer(), nullable=True),
        sa.Column("thumbnail_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("captions_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("required_action", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_youtube_upload_sessions_publication_id", "youtube_upload_sessions", ["publication_id"])
    op.create_index("ix_youtube_upload_sessions_status", "youtube_upload_sessions", ["status"])
    op.create_index("uq_youtube_session_active", "youtube_upload_sessions", ["publication_id"], unique=True,
                    postgresql_where=sa.text(ACTIVE), sqlite_where=sa.text(ACTIVE))


def downgrade() -> None:
    op.drop_index("uq_youtube_session_active", table_name="youtube_upload_sessions")
    op.drop_index("ix_youtube_upload_sessions_status", table_name="youtube_upload_sessions")
    op.drop_index("ix_youtube_upload_sessions_publication_id", table_name="youtube_upload_sessions")
    op.drop_table("youtube_upload_sessions")
