"""The crime scene(s) of a case, named once from the evidence for every language's film."""

import sqlalchemy as sa
from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_crime_scenes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False, index=True),
        sa.Column("place", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mention_share", sa.Float(), nullable=True),
        sa.Column("source", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "place", name="uq_case_crime_scene"),
    )


def downgrade() -> None:
    op.drop_table("case_crime_scenes")
