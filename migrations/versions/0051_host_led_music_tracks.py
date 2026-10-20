"""The real generated-music provenance log for host-led reels: one row per generated track
(channel, mood, prompt, provider/model, generation id, plan name, duration, bpm, file hash,
licence note, terms source and date) plus the owner's manual Content ID checklist outcome, so a
track that gets a platform claim is excluded from selection.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_led_music_tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("mood", sa.String(40), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model_id", sa.String(40), nullable=False),
        sa.Column("provider_generation_id", sa.String(100), nullable=True),
        sa.Column("plan_name", sa.String(80), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("bpm_estimate", sa.Float(), nullable=True),
        sa.Column("bpm_method", sa.String(40), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("licence_note", sa.Text(), nullable=False),
        sa.Column("terms_url", sa.Text(), nullable=False),
        sa.Column("terms_recorded_date", sa.String(20), nullable=False),
        sa.Column("asset_store_key", sa.String(64), nullable=True),
        sa.Column("content_id_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("content_id_checked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("content_id_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_id_result", sa.Text(), nullable=True),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("excluded_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "content_id_status in ('pending','clear','claimed')",
            name="ck_host_led_music_tracks_content_id_status",
        ),
    )
    op.create_index("ix_host_led_music_tracks_channel", "host_led_music_tracks", ["channel"])
    op.create_index("ix_host_led_music_tracks_mood", "host_led_music_tracks", ["mood"])
    op.create_index(
        "ix_host_led_music_tracks_file_sha256", "host_led_music_tracks", ["file_sha256"], unique=True
    )

    op.create_table(
        "host_led_music_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("host_led_music_tracks.id"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("reel_key", sa.String(80), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_host_led_music_usage_track_id", "host_led_music_usage", ["track_id"])
    op.create_index("ix_host_led_music_usage_case_id", "host_led_music_usage", ["case_id"])


def downgrade() -> None:
    op.drop_table("host_led_music_usage")
    op.drop_table("host_led_music_tracks")
