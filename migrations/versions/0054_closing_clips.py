"""The stored closing clips of the host (call to action + identity line, spoken by the host):
one row per rendered variant (language, outfit, position, voice, sentences, seconds, cost). The
file itself and its use ledger live in shared_reel_assets (asset_type 'closing_clip'), joined by
asset_key.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "closing_clips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_key", sa.String(64), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("look_id", sa.Integer(), nullable=True),
        sa.Column("heygen_look_id", sa.String(80), nullable=False),
        sa.Column("outfit_label", sa.String(200), nullable=False),
        sa.Column("position", sa.String(80), nullable=False),
        sa.Column("voice_id", sa.String(80), nullable=False),
        sa.Column("sentences_json", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("cost_eur", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_closing_clips_asset_key", "closing_clips", ["asset_key"], unique=True)
    op.create_index("ix_closing_clips_language", "closing_clips", ["language"])


def downgrade() -> None:
    op.drop_table("closing_clips")
