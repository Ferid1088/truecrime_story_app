"""Two kinds of video: research only or a film candidate, with the rights evidence.

Every existing video becomes research_only (its transcript was read; it was never shown).
Applied only after a backup and the owner's approval."""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_sources", sa.Column("use", sa.String(length=20), nullable=False,
                                             server_default="research_only"))
    op.add_column("video_sources", sa.Column("use_reason", sa.Text(), nullable=True))
    op.add_column("video_sources", sa.Column("rights_status", sa.String(length=40), nullable=False,
                                             server_default="unknown"))
    op.add_column("video_sources", sa.Column("license_evidence_url", sa.Text(), nullable=True))
    op.add_column("video_sources", sa.Column("license_checked_at", sa.DateTime(timezone=True),
                                             nullable=True))
    op.add_column("video_sources", sa.Column("attribution_text", sa.Text(), nullable=True))
    op.create_index("ix_video_sources_use", "video_sources", ["use"])


def downgrade() -> None:
    op.drop_index("ix_video_sources_use", table_name="video_sources")
    for column in ("attribution_text", "license_checked_at", "license_evidence_url",
                   "rights_status", "use_reason", "use"):
        op.drop_column("video_sources", column)
