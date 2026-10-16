"""A real research re-extraction deletes and re-inserts every Fact for a
case (never an in-place update). claim_clusters.promoted_fact_id and
transcript_claims.promoted_fact_id pointed at specific fact rows with the
implicit NO ACTION delete behaviour, so a stale "promoted to fact #N"
pointer from an earlier run blocked the delete outright (observed live on
case 5, 2026-10-16: ForeignKeyViolation on facts.id=5042 from
claim_clusters). Both now SET NULL instead, so a fresh extraction can
proceed and the dedup/cluster row itself is kept, just no longer claiming
to point at a specific fact."""

from alembic import op
from app.db.base import Base
import app.db.models  # noqa: F401 - populate Base.metadata

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Real, named constraints: a direct, minimal ALTER — no table
        # rebuild needed.
        op.drop_constraint("claim_clusters_promoted_fact_id_fkey", "claim_clusters", type_="foreignkey")
        op.create_foreign_key(
            "claim_clusters_promoted_fact_id_fkey", "claim_clusters", "facts",
            ["promoted_fact_id"], ["id"], ondelete="SET NULL",
        )
        op.drop_constraint("transcript_claims_promoted_fact_id_fkey", "transcript_claims", type_="foreignkey")
        op.create_foreign_key(
            "transcript_claims_promoted_fact_id_fkey", "transcript_claims", "facts",
            ["promoted_fact_id"], ["id"], ondelete="SET NULL",
        )
        return

    # SQLite: foreign keys are unnamed, so they can't be dropped by name —
    # recreate the table from the current model metadata (which now
    # declares ondelete="SET NULL"). copy_from does not carry the table's
    # indexes through the rebuild, so they are explicitly recreated after.
    with op.batch_alter_table(
        "claim_clusters", schema=None, recreate="always",
        copy_from=Base.metadata.tables["claim_clusters"],
    ):
        pass
    with op.batch_alter_table(
        "transcript_claims", schema=None, recreate="always",
        copy_from=Base.metadata.tables["transcript_claims"],
    ):
        pass
    op.create_index("ix_claim_clusters_case_id", "claim_clusters", ["case_id"])
    op.create_index("ix_transcript_claims_case_id", "transcript_claims", ["case_id"])
    op.create_index("ix_transcript_claims_cluster_id", "transcript_claims", ["cluster_id"])
    op.create_index("ix_transcript_claims_verification_status", "transcript_claims",
                    ["verification_status"])
    op.create_index("ix_transcript_claims_video_source_id", "transcript_claims",
                    ["video_source_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("claim_clusters_promoted_fact_id_fkey", "claim_clusters", type_="foreignkey")
        op.create_foreign_key(
            "claim_clusters_promoted_fact_id_fkey", "claim_clusters", "facts",
            ["promoted_fact_id"], ["id"],
        )
        op.drop_constraint("transcript_claims_promoted_fact_id_fkey", "transcript_claims", type_="foreignkey")
        op.create_foreign_key(
            "transcript_claims_promoted_fact_id_fkey", "transcript_claims", "facts",
            ["promoted_fact_id"], ["id"],
        )
    # SQLite: the upgrade's table recreation from the (by-then-reverted)
    # model metadata already restores the old behaviour on a true
    # downgrade workflow (edit the model back, then downgrade); nothing
    # to do here without a name to drop.
