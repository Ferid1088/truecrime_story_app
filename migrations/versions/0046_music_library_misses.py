"""Moments the audio director found no fitting library track for, kept for the owner's decision.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "music_library_misses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("film_key", sa.String(60), nullable=False),
        sa.Column("after_beat", sa.String(20), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("mood", sa.String(30), nullable=False),
        sa.Column("situation", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("music_tracks.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("film_key", "after_beat", "kind", name="uq_music_miss_film_beat"),
    )
    for col in ("case_id", "film_key", "kind", "mood"):
        op.create_index(f"ix_music_library_misses_{col}", "music_library_misses", [col])


def downgrade() -> None:
    op.drop_table("music_library_misses")
