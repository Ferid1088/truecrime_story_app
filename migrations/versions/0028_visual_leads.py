"""Part 3(d): visual leads — the real footage or photos a video shows, at
which timestamp, and the original owner if named (a lead for Part 4's media
research; nothing is downloaded)."""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visual_leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column(
            "video_source_id", sa.Integer(), sa.ForeignKey("video_sources.id"), nullable=False
        ),
        sa.Column("lead_type", sa.String(length=40), nullable=False),
        sa.Column("description_en", sa.Text(), nullable=False),
        sa.Column("original_owner", sa.String(length=300), nullable=True),
        sa.Column("segment_ids_json", sa.Text(), nullable=False),
        sa.Column("timestamp_start", sa.Float(), nullable=True),
        sa.Column("timestamp_end", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_visual_leads_case_id", "visual_leads", ["case_id"])
    op.create_index("ix_visual_leads_video_source_id", "visual_leads", ["video_source_id"])


def downgrade() -> None:
    op.drop_index("ix_visual_leads_video_source_id", table_name="visual_leads")
    op.drop_index("ix_visual_leads_case_id", table_name="visual_leads")
    op.drop_table("visual_leads")
