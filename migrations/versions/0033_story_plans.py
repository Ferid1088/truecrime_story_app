"""Part 5a: the story plan before writing. A stored, validated, approvable plan
(beats in order, reveals, evidence ids, target length, people introductions,
host slots); story versions and blueprints record the plan they came from."""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("target_minutes", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("validation_json", sa.Text(), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_model", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_story_plans_case_id", "story_plans", ["case_id"])
    op.create_index("ix_story_plans_status", "story_plans", ["status"])
    with op.batch_alter_table("story_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("story_plan_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_story_versions_story_plan_id", ["story_plan_id"])
    with op.batch_alter_table("editorial_blueprints", schema=None) as batch_op:
        batch_op.add_column(sa.Column("story_plan_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_editorial_blueprints_story_plan_id", ["story_plan_id"])


def downgrade() -> None:
    with op.batch_alter_table("editorial_blueprints", schema=None) as batch_op:
        batch_op.drop_index("ix_editorial_blueprints_story_plan_id")
        batch_op.drop_column("story_plan_id")
    with op.batch_alter_table("story_versions", schema=None) as batch_op:
        batch_op.drop_index("ix_story_versions_story_plan_id")
        batch_op.drop_column("story_plan_id")
    op.drop_index("ix_story_plans_status", table_name="story_plans")
    op.drop_index("ix_story_plans_case_id", table_name="story_plans")
    op.drop_table("story_plans")
