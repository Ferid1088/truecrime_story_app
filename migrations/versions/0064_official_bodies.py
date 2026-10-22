"""P2 official-source discovery: the official bodies derived per case (with the evidence
where the name came from and, when proven, the channel's stored proof) plus the channel
proof on a video source so a clip's source record can carry it.

Additive: one new table, one nullable column; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "official_bodies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("name_key", sa.String(length=320), nullable=False),
        sa.Column("body_type", sa.String(length=40), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="found"),
        sa.Column("skip_reason", sa.String(length=200), nullable=True),
        sa.Column("channel_id", sa.String(length=60), nullable=True),
        sa.Column("channel_title", sa.String(length=300), nullable=True),
        sa.Column("channel_url", sa.Text(), nullable=True),
        sa.Column("uploads_playlist_id", sa.String(length=60), nullable=True),
        sa.Column("proof_json", sa.Text(), nullable=True),
        sa.Column("proved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "name_key", name="uq_official_bodies_case_name"),
    )
    op.create_index("ix_official_bodies_case_id", "official_bodies", ["case_id"])
    with op.batch_alter_table("video_sources", schema=None) as batch_op:
        batch_op.add_column(sa.Column("channel_proof_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("video_sources", schema=None) as batch_op:
        batch_op.drop_column("channel_proof_json")
    op.drop_index("ix_official_bodies_case_id", table_name="official_bodies")
    op.drop_table("official_bodies")
