"""Part 2(e) item 3: cache fact extraction by content hash, prompt version
and model, so the same content is never extracted twice — across cases
that share a source, and across a restart."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("content_hash", "prompt_version", "model",
                            name="uq_extraction_cache_key"),
    )
    op.create_index(
        "ix_extraction_cache_content_hash", "extraction_cache", ["content_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_cache_content_hash", table_name="extraction_cache")
    op.drop_table("extraction_cache")
