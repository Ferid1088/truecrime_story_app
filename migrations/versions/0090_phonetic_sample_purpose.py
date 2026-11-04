"""Phonetic samples: what a clip is for and, for a controlled recording, which reading the voice was told to say. `purpose` keeps the clips that only
illustrate a known problem (the owner's reports from a film) out of the validation of a recognizer; `intended_key` is the reading a controlled
recording was made to say, never shown to the person who labels it (the person's ear is the label: the voice may not have obeyed)."""

import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("phonetic_samples", sa.Column("purpose", sa.String(20), nullable=False, server_default="validation"))
    op.add_column("phonetic_samples", sa.Column("intended_key", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("phonetic_samples", "intended_key")
    op.drop_column("phonetic_samples", "purpose")
