"""Who chose an episode's title: the owner, or the app (the best title-and-cover pair): the app never replaces an owner's choice (2026-11-14)."""

import sqlalchemy as sa
from alembic import op

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("episode_identities", sa.Column("title_source", sa.String(10), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("episode_identities", "title_source")
