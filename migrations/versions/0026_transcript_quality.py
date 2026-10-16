"""Part 3(e) item 3: transcript quality gating. Auto-caption transcripts
get a per-segment garbled flag and a video-level quality score/verdict,
computed deterministically (no model call) at storage time. A quote-type
TranscriptClaim gets a deterministic quote_verified flag — the same
substring-match-against-the-real-text check app.core.quote_check already
does for Fact.quote_verified, applied here to the transcript segment it
cites, and required to be on a manual caption (never an auto-caption,
whatever language) to count as verified at all."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_garbled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("video_sources", schema=None) as batch_op:
        batch_op.add_column(sa.Column("transcript_quality_score", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("transcript_quality_verdict", sa.String(length=20), nullable=True)
        )
    with op.batch_alter_table("transcript_claims", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quote_verified", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transcript_claims", schema=None) as batch_op:
        batch_op.drop_column("quote_verified")
    with op.batch_alter_table("video_sources", schema=None) as batch_op:
        batch_op.drop_column("transcript_quality_verdict")
        batch_op.drop_column("transcript_quality_score")
    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.drop_column("is_garbled")
