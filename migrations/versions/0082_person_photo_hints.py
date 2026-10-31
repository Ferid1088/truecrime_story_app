"""The person-photo inspector's table: for a person named for the first time in the text, the approved photos that give nothing away there."""

import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "person_photo_hints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("visual_plan_id", sa.Integer(), sa.ForeignKey("visual_plans.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("entity_key", sa.String(120), nullable=False),
        sa.Column("person_name", sa.String(200), nullable=False),
        sa.Column("mention_start", sa.Float(), nullable=False),
        sa.Column("mention_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("visual_asset_id", sa.Integer(), sa.ForeignKey("visual_assets.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_person_photo_hints_case_id", "person_photo_hints", ["case_id"])
    op.create_index("ix_person_photo_hints_visual_plan_id", "person_photo_hints", ["visual_plan_id"])
    op.create_index("ix_person_photo_hints_visual_asset_id", "person_photo_hints", ["visual_asset_id"])
    op.create_index("ix_person_photo_hints_film", "person_photo_hints", ["case_id", "language"])


def downgrade() -> None:
    op.drop_table("person_photo_hints")
