"""The autopilot (Start button): one row per run with its step log and "needs you" list, the channel
settings placeholders per language and platform (never a credential), and the dry-run publish
requests with their schedule. (The title suggestions a language gets are the existing
case_title_candidates.)

Additive: new tables; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autopilot_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("languages_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("stage", sa.String(60), nullable=True),
        sa.Column("steps_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("needs_you_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("estimate_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("film_job_ids_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_autopilot_runs_case_id", "autopilot_runs", ["case_id"])
    op.create_index("ix_autopilot_runs_status", "autopilot_runs", ["status"])
    op.create_table(
        "channel_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("handle", sa.String(200), nullable=False, server_default=""),
        sa.Column("connected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("language", "platform", name="uq_channel_setting"),
    )
    op.create_table(
        "publish_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("autopilot_runs.id"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("short_id", sa.Integer(), nullable=True),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="dry_run"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_publish_requests_run_id", "publish_requests", ["run_id"])


def downgrade() -> None:
    op.drop_table("publish_requests")
    op.drop_table("channel_settings")
    op.drop_table("autopilot_runs")
