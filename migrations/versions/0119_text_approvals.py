"""Texts (2026-11-19): the owner's release of the titles and cover texts of a language (Persian) for a case, with a hash of the texts he saw; nothing existing is changed."""

import sqlalchemy as sa
from alembic import op

revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "text_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=False),
        sa.Column("texts_hash", sa.String(64), nullable=False),
        sa.Column("texts_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("case_id", "language", name="uq_text_approval_case_language"),
    )
    op.create_index("ix_text_approvals_case_id", "text_approvals", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_text_approvals_case_id", table_name="text_approvals")
    op.drop_table("text_approvals")
