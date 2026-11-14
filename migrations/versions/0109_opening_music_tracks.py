"""The stressful piano tracks of the film openings: one per case, new ones made until the library holds the configured number, then one is taken at random (2026-11-14)."""

import sqlalchemy as sa
from alembic import op

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opening_music_tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_code", sa.String(60), nullable=False),
        sa.Column("variation_key", sa.String(200), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("seconds", sa.Float(), nullable=False, server_default="30"),
        sa.Column("provider", sa.String(30), nullable=False, server_default="elevenlabs_sound"),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("characters_paid", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("variation_key", name="uq_opening_music_variation"),
    )
    op.create_index("ix_opening_music_tracks_track_code", "opening_music_tracks", ["track_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_opening_music_tracks_track_code", table_name="opening_music_tracks")
    op.drop_table("opening_music_tracks")
