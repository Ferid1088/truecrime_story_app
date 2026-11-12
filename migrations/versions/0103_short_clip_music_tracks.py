"""The quiet music beds of the short clips: one row per generated bed, so each short can get its own and, once the library is full, one is taken at random
instead of paying for a new one (2026-11-12)."""

import sqlalchemy as sa
from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_clip_music_tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_code", sa.String(60), nullable=False),
        sa.Column("variation_key", sa.String(200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("seconds", sa.Float(), nullable=False, server_default="22"),
        sa.Column("provider", sa.String(30), nullable=False, server_default="elevenlabs_sound"),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("characters_paid", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("variation_key", name="uq_short_clip_music_variation"),
    )
    op.create_index("ix_short_clip_music_tracks_track_code", "short_clip_music_tracks", ["track_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_short_clip_music_tracks_track_code", table_name="short_clip_music_tracks")
    op.drop_table("short_clip_music_tracks")
