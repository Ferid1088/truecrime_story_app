"""Part 7b: the host's look library (studio, camera, framing, outfit, pose,
image, approval, HeyGen look id)."""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_looks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("studio_profile_id", sa.String(length=100), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("camera", sa.String(length=20), nullable=False),
        sa.Column("framing", sa.String(length=20), nullable=False),
        sa.Column("outfit_id", sa.String(length=100), nullable=False),
        sa.Column("pose", sa.String(length=60), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("image_sha256", sa.String(length=64), nullable=True),
        sa.Column("image_model", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("heygen_look_id", sa.String(length=200), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_host_looks_studio_profile_id", "host_looks", ["studio_profile_id"])
    op.create_index("ix_host_looks_status", "host_looks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_host_looks_status", table_name="host_looks")
    op.drop_index("ix_host_looks_studio_profile_id", table_name="host_looks")
    op.drop_table("host_looks")
