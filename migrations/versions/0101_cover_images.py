"""Generated cover scenes: one row for each attempt of the image model (tokens, runtime, cost), and a proposal that carries a scene description and its image instead of a
picture of the film (2026-11-11)."""

import sqlalchemy as sa
from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cover_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("cover_key", sa.String(10), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("size", sa.String(20), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("file_path", sa.String(300), nullable=True),
        sa.Column("request_id", sa.String(80), nullable=True),
        sa.Column("text_tokens_in", sa.Integer(), nullable=True),
        sa.Column("image_tokens_in", sa.Integer(), nullable=True),
        sa.Column("text_tokens_out", sa.Integer(), nullable=True),
        sa.Column("image_tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("cost_eur", sa.Float(), nullable=True),
        sa.Column("cost_source", sa.String(30), nullable=True),
        sa.Column("provider_call_id", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for col in ("case_id", "language", "cover_key", "proposal_id"):
        op.create_index(f"ix_cover_images_{col}", "cover_images", [col])
    with op.batch_alter_table("cover_proposals") as batch:
        batch.add_column(sa.Column("scene_prompt", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("image_id", sa.Integer(), sa.ForeignKey("cover_images.id", name="fk_cover_proposals_image"), nullable=True))
        batch.drop_column("scene_code")
    # proposals made before this change chose a picture of the film: that choice no longer exists, so they are open again
    op.execute("UPDATE cover_proposals SET status = 'open' WHERE status = 'proposed'")


def downgrade() -> None:
    with op.batch_alter_table("cover_proposals") as batch:
        batch.add_column(sa.Column("scene_code", sa.String(40), nullable=True))
        batch.drop_column("image_id")
        batch.drop_column("scene_prompt")
    op.drop_table("cover_images")
