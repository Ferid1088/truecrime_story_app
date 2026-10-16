"""Regional language tags ("en-US", "de-DE") stored by older transcript
ingestion are rewritten to the base language the app compares by; the
original regional tag is not kept (it carried no information the app used)."""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_TABLES = ("sources", "video_sources", "transcript_segments")


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        tagged = bind.execute(
            sa.text(f"SELECT DISTINCT language FROM {table} "
                    "WHERE language LIKE '%-%' OR language LIKE '%\\_%' ESCAPE '\\'")
        ).scalars().all()
        for tag in tagged:
            base = tag.replace("_", "-").split("-")[0].lower()
            bind.execute(
                sa.text(f"UPDATE {table} SET language = :base WHERE language = :tag"),
                {"base": base, "tag": tag},
            )


def downgrade() -> None:
    """The regional tags are not recoverable; nothing to undo."""
