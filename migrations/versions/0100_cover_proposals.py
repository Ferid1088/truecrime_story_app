"""The title and the scene of each cover of a case in a language, with the owner's decisions (2026-11-11)."""

import sqlalchemy as sa
from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cover_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("cover_key", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(10), nullable=False, server_default="agent"),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("scene_code", sa.String(40), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("generation_model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for col in ("case_id", "language", "cover_key", "status"):
        op.create_index(f"ix_cover_proposals_{col}", "cover_proposals", [col])


def downgrade() -> None:
    op.drop_table("cover_proposals")
