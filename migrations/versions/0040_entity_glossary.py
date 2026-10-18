"""The spellings of a case's named entities per language, approved by the owner.

Additive: one new table; nothing existing changes."""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_glossary",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("entity", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("spellings", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("origin", sa.String(length=20), nullable=False, server_default="evidence"),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "entity", "language", name="uq_entity_glossary"),
    )
    op.create_index("ix_entity_glossary_case_id", "entity_glossary", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_entity_glossary_case_id", table_name="entity_glossary")
    op.drop_table("entity_glossary")
