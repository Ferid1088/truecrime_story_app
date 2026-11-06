"""The host's voice gets the same hand review as the narration: a review has a kind (narration | host), one review per version and kind."""

import sqlalchemy as sa
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("voice_reviews") as batch:
        batch.add_column(sa.Column("kind", sa.String(12), nullable=False, server_default="narration"))
        batch.drop_constraint("uq_voice_review_version", type_="unique")
        batch.create_unique_constraint("uq_voice_review_version_kind", ["story_version_id", "kind"])


def downgrade() -> None:
    with op.batch_alter_table("voice_reviews") as batch:
        batch.drop_constraint("uq_voice_review_version_kind", type_="unique")
        batch.create_unique_constraint("uq_voice_review_version", ["story_version_id"])
        batch.drop_column("kind")
