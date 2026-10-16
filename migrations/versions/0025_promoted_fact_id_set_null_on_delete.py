"""A real research re-extraction deletes and re-inserts every Fact for a
case (never an in-place update). claim_clusters.promoted_fact_id and
transcript_claims.promoted_fact_id pointed at specific fact rows with the
implicit NO ACTION delete behaviour, so a stale "promoted to fact #N"
pointer from an earlier run blocked the delete outright (observed live on
case 5, 2026-10-16: ForeignKeyViolation on facts.id=5042 from
claim_clusters). Both now SET NULL instead, so a fresh extraction can
proceed and the dedup/cluster row itself is kept, just no longer claiming
to point at a specific fact."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# SQLite's batch recreate needs a table definition to copy from. This MUST
# be a frozen snapshot of the schema as it existed at THIS point in
# migration history (down_revision "0024"), never a live reference to
# app.db.models/Base.metadata — a later migration adding a column (e.g.
# 0026's transcript_claims.quote_verified) would otherwise make Alembic
# try to SELECT a column that does not exist yet when replaying migrations
# from scratch on SQLite (a real failure this caused once, caught by the
# full test suite before it reached any shared database).
_SNAPSHOT = sa.MetaData()

_CLAIM_CLUSTERS_0024 = sa.Table(
    "claim_clusters", _SNAPSHOT,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("case_id", sa.Integer, sa.ForeignKey("cases.id"), nullable=False),
    sa.Column("canonical_claim_en", sa.Text, nullable=False),
    sa.Column("member_claim_ids_json", sa.Text, nullable=False),
    sa.Column("independent_source_family_count", sa.Integer, nullable=False),
    sa.Column("languages_json", sa.Text, nullable=False),
    sa.Column("claim_type", sa.String(60), nullable=False),
    sa.Column("narrative_value", sa.String(40), nullable=True),
    sa.Column("support_status", sa.String(40), nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("promoted_fact_id", sa.Integer,
             sa.ForeignKey("facts.id", ondelete="SET NULL"), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

_TRANSCRIPT_CLAIMS_0024 = sa.Table(
    "transcript_claims", _SNAPSHOT,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("case_id", sa.Integer, sa.ForeignKey("cases.id"), nullable=False),
    sa.Column("video_source_id", sa.Integer, sa.ForeignKey("video_sources.id"), nullable=False),
    sa.Column("segment_ids_json", sa.Text, nullable=False),
    sa.Column("original_language", sa.String(20), nullable=False),
    sa.Column("original_claim", sa.Text, nullable=True),
    sa.Column("canonical_claim_en", sa.Text, nullable=False),
    sa.Column("claim_type", sa.String(60), nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("certainty", sa.String(40), nullable=False),
    sa.Column("speaker", sa.String(300), nullable=True),
    sa.Column("quote_classification", sa.String(40), nullable=True),
    sa.Column("timestamp_start", sa.Float, nullable=True),
    sa.Column("timestamp_end", sa.Float, nullable=True),
    sa.Column("verification_status", sa.String(40), nullable=False),
    sa.Column("supporting_source_ids_json", sa.Text, nullable=False),
    sa.Column("cluster_id", sa.Integer, sa.ForeignKey("claim_clusters.id"), nullable=True),
    sa.Column("promoted_fact_id", sa.Integer,
             sa.ForeignKey("facts.id", ondelete="SET NULL"), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


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
    # recreate the table from the frozen snapshot above (which now declares
    # ondelete="SET NULL"). copy_from does not carry the table's indexes
    # through the rebuild, so they are explicitly recreated after.
    with op.batch_alter_table(
        "claim_clusters", schema=None, recreate="always",
        copy_from=_CLAIM_CLUSTERS_0024,
    ):
        pass
    with op.batch_alter_table(
        "transcript_claims", schema=None, recreate="always",
        copy_from=_TRANSCRIPT_CLAIMS_0024,
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
    # SQLite: nothing to do without a name to drop — a true downgrade
    # workflow on SQLite would need its own recreate back to a pre-0025
    # snapshot, which no code path here has ever exercised.
