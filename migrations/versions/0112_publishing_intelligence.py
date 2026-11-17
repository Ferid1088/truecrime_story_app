"""AI publishing intelligence (2026-11-16): the metadata of a publication (title, hashtags, keywords, chapters, call to action) and where it came from, the order of a Short on its
platform, and the saved runs of the AI publishing plan. Nothing existing is changed or removed; every new column has a default."""

import sqlalchemy as sa
from alembic import op

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publications") as batch:
        batch.add_column(sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("metadata_source", sa.String(10), nullable=False, server_default="default"))
        batch.add_column(sa.Column("sequence", sa.Integer(), nullable=True))
    op.create_table(
        "publishing_plan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="running"),
        sa.Column("input_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("progress_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("agent_run_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("applied_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_publishing_plan_runs_case_id", "publishing_plan_runs", ["case_id"])
    op.create_index("ix_publishing_plan_runs_language", "publishing_plan_runs", ["language"])
    op.create_index("ix_publishing_plan_runs_status", "publishing_plan_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_publishing_plan_runs_status", table_name="publishing_plan_runs")
    op.drop_index("ix_publishing_plan_runs_language", table_name="publishing_plan_runs")
    op.drop_index("ix_publishing_plan_runs_case_id", table_name="publishing_plan_runs")
    op.drop_table("publishing_plan_runs")
    with op.batch_alter_table("publications") as batch:
        batch.drop_column("sequence")
        batch.drop_column("metadata_source")
        batch.drop_column("metadata_json")
