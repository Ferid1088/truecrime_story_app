"""The words of a language and how the voice must say them, kept across films (proven by the listening review, or given by the owner)."""

import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pronunciation_lexicon",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("surface", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="alias"),
        sa.Column("reading", sa.String(240), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="reviewer"),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("language", "surface", name="uq_pronunciation_lexicon"),
    )


def downgrade() -> None:
    op.drop_table("pronunciation_lexicon")
