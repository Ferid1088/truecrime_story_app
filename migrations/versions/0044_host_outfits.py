"""Outfits per case and language; a look belongs to an outfit and has a stance; a scene records
its outfit and the look chosen for it.

Additive: a new table and nullable / defaulted columns; every existing look keeps its row (it
has no outfit of a video until one is given, and sits)."""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_outfits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("outfit_ref", sa.String(100), nullable=True),
        sa.Column("reference_image_path", sa.String(500), nullable=True),
        sa.Column("reference_image_sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_host_outfits_case_id", "host_outfits", ["case_id"])
    op.create_index("ix_host_outfits_language", "host_outfits", ["language"])
    op.create_index("ix_host_outfits_status", "host_outfits", ["status"])
    with op.batch_alter_table("host_looks") as batch:
        batch.add_column(sa.Column("host_outfit_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("stance", sa.String(10), nullable=False,
                                   server_default="SEATED"))
        batch.create_foreign_key("fk_host_looks_outfit", "host_outfits", ["host_outfit_id"], ["id"])
        batch.create_index("ix_host_looks_host_outfit_id", ["host_outfit_id"])
    with op.batch_alter_table("host_scenes") as batch:
        batch.add_column(sa.Column("host_outfit_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("look_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("look_choice_json", sa.Text(), nullable=False,
                                   server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("host_scenes") as batch:
        batch.drop_column("look_choice_json")
        batch.drop_column("look_id")
        batch.drop_column("host_outfit_id")
    with op.batch_alter_table("host_looks") as batch:
        batch.drop_index("ix_host_looks_host_outfit_id")
        batch.drop_constraint("fk_host_looks_outfit", type_="foreignkey")
        batch.drop_column("stance")
        batch.drop_column("host_outfit_id")
    op.drop_table("host_outfits")
