"""Reading resolutions: how a word the film read in more than one way is read, decided by a stated rule and its evidence (the approved text's letters, a
name's source syllables, notation, a colloquial variant kept), with the case it holds for. Kept apart from the owner's approvals: a resolution is
not a respelling, and the owner's decision outranks it."""

import sqlalchemy as sa
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reading_resolutions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("surface", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reading", sa.String(240), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("candidates", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("language", "case_id", "surface", name="uq_reading_resolutions"),
    )


def downgrade() -> None:
    op.drop_table("reading_resolutions")
