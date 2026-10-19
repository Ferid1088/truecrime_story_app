"""The checks of a host scene's voice (Whisper, pronunciation listening, flags).

Additive: one column with a default; nothing existing changes."""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("host_scenes", sa.Column("voice_checks_json", sa.Text(), nullable=False,
                                           server_default="{}"))


def downgrade() -> None:
    op.drop_column("host_scenes", "voice_checks_json")
