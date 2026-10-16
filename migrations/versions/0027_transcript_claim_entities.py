"""Part 3(e) item 1: a transcript claim's own people/locations, so a
video's entities can be promoted onto the canonical Fact (app.services.
claims.promote_clusters) and feed the case's cross-source entity list —
previously a video-promoted Fact always had empty people_json/
locations_json, since TranscriptClaim never captured them at all."""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transcript_claims", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("people_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("locations_json", sa.Text(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("transcript_claims", schema=None) as batch_op:
        batch_op.drop_column("locations_json")
        batch_op.drop_column("people_json")
