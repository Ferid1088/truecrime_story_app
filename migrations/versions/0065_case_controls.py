"""Case controls: pause/resume/stop state on cases, the trash columns of the
soft-delete path, the audit trail (``case_events``), the tombstone of a deleted
case (``case_tombstones``) and the "case deleted" marker on its cost ledger rows.

Additive: two new tables, five new columns; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.add_column(sa.Column("control_state", sa.String(length=20), nullable=True))
        batch_op.add_column(
            sa.Column("paused_languages_json", sa.Text(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_cases_control_state", "cases", ["control_state"])
    with op.batch_alter_table("provider_calls", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("case_deleted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "case_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        # no FK: the audit row must outlive the case it describes
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("case_name", sa.String(length=500), nullable=True),
        sa.Column("action", sa.String(length=30), nullable=False, index=True),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("stage", sa.String(length=60), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("state_before", sa.String(length=30), nullable=True),
        sa.Column("state_after", sa.String(length=30), nullable=True),
        sa.Column("cost_eur_at_event", sa.Float(), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])
    op.create_table(
        "case_tombstones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False, index=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("slug", sa.String(length=500), nullable=True),
        sa.Column("who", sa.String(length=120), nullable=False),
        sa.Column("total_cost_eur", sa.Float(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restore_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("case_tombstones")
    op.drop_table("case_events")
    with op.batch_alter_table("provider_calls", schema=None) as batch_op:
        batch_op.drop_column("case_deleted")
    op.drop_index("ix_cases_control_state", "cases")
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_column("purge_after")
        batch_op.drop_column("trashed_at")
        batch_op.drop_column("paused_languages_json")
        batch_op.drop_column("control_state")
