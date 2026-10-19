"""Music tracks carry the situation they fit and tags, so agents and the owner can see where to use them.

Additive: two defaulted columns; existing tracks get their text from the library on the next catalogue read."""

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("music_tracks", sa.Column("situation", sa.Text(), nullable=False, server_default=""))
    op.add_column("music_tracks", sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("music_tracks", "tags_json")
    op.drop_column("music_tracks", "situation")
