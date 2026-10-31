"""How each language's narration writes the names of the case's entities (full, first and last name as the text spells them)."""

import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_name_forms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("entity_key", sa.String(120), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("forms_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "language", "entity_key", name="uq_entity_name_forms"),
    )
    op.create_index("ix_entity_name_forms_case_id", "entity_name_forms", ["case_id"])


def downgrade() -> None:
    op.drop_table("entity_name_forms")
