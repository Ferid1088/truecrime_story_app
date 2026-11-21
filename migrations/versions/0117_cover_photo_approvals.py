"""Cover photos (2026-11-19): the owner's "I'm finished" with the photographs for the covers of a case, one row per case, with the photographs that were approved at that moment.
Nothing existing is changed."""

import sqlalchemy as sa
from alembic import op

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cover_photo_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False, unique=True),
        sa.Column("approved_by", sa.String(120), nullable=False),
        sa.Column("photo_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_cover_photo_approvals_case_id", "cover_photo_approvals", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_cover_photo_approvals_case_id", table_name="cover_photo_approvals")
    op.drop_table("cover_photo_approvals")
