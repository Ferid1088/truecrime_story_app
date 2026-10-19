"""A host plan can belong to a story plan instead of an editorial blueprint.

Stories written to a story plan (the language pipeline) have no blueprint; their host is planned
against the plan's acts. The blueprint reference becomes optional and the plan gets its own.

Additive and relaxing: a new nullable column, the old column loses NOT NULL."""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("host_plans") as batch:
        batch.add_column(sa.Column("story_plan_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_host_plans_story_plan", "story_plans",
                                 ["story_plan_id"], ["id"])
        batch.create_index("ix_host_plans_story_plan_id", ["story_plan_id"])
        batch.alter_column("blueprint_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("host_plans") as batch:
        batch.alter_column("blueprint_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_index("ix_host_plans_story_plan_id")
        batch.drop_constraint("fk_host_plans_story_plan", type_="foreignkey")
        batch.drop_column("story_plan_id")
