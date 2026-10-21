"""The raw YouTube Data API license value (P15b: "use the license the API reports"), fetched
from a real videos.list call — status.license is "youtube" or "creativeCommon". The existing
classifier (VideoUseClassifier._youtube) already checks candidate["license"] against the
configured film_candidate_license; nothing populated it from a live call until now.

Additive: one nullable column; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_sources", sa.Column("youtube_license", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("video_sources", "youtube_license")
