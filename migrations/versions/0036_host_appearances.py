"""Part 7c/7d: the stage direction stored on the host scene and the visual
memory of what the host looked like in each appearance."""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("host_scenes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("direction_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("direction_sha256", sa.String(length=64), nullable=True))
    op.create_table(
        "host_appearances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("host_scene_id", sa.Integer(), sa.ForeignKey("host_scenes.id"), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("channel", sa.String(length=100), nullable=False),
        sa.Column("position", sa.String(length=20), nullable=True),
        sa.Column("studio_profile_id", sa.String(length=100), nullable=False),
        sa.Column("shots_json", sa.Text(), nullable=False),
        sa.Column("outfit_id", sa.String(length=100), nullable=False),
        sa.Column("look_id", sa.String(length=200), nullable=True),
        sa.Column("posture", sa.String(length=60), nullable=False),
        sa.Column("gestures_json", sa.Text(), nullable=False),
        sa.Column("lighting", sa.String(length=60), nullable=False),
        sa.Column("emotion", sa.String(length=40), nullable=False),
        sa.Column("interaction_pattern", sa.String(length=80), nullable=True),
        sa.Column("persona_sha256", sa.String(length=64), nullable=True),
        sa.Column("direction_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_host_appearances_case_id", "host_appearances", ["case_id"])
    op.create_index("ix_host_appearances_channel", "host_appearances", ["channel"])
    op.create_index("ix_host_appearances_created_at", "host_appearances", ["created_at"])
    op.create_index("ix_host_appearances_host_scene_id", "host_appearances", ["host_scene_id"],
                    unique=True)


def downgrade() -> None:
    op.drop_table("host_appearances")
    with op.batch_alter_table("host_scenes", schema=None) as batch_op:
        batch_op.drop_column("direction_sha256")
        batch_op.drop_column("direction_json")
