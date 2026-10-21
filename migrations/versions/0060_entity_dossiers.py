"""Entity dossiers (P1): one row per important element of a case (person, place, building,
object, event, document, vehicle) written once by a model from the case facts, plus any
identifier fetched from an official API (Wikidata, Commons). A read-only reference used in
query generation, the pre-filter, the verifier's input and candidate ranking.

Additive: a new table; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_dossiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("entity_key", sa.String(60), nullable=False),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("era", sa.String(20), nullable=False, server_default="modern"),
        sa.Column("aliases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("visual_description", sa.Text(), nullable=True),
        sa.Column("not_this_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("wikidata_id", sa.String(20), nullable=True),
        sa.Column("commons_category", sa.String(200), nullable=True),
        sa.Column("likely_sources_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("expected_captions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("negative_terms_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("supports_fact_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("generation_model", sa.String(60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "entity_key", name="uq_entity_dossiers_case_entity"),
    )
    op.create_index("ix_entity_dossiers_case_id", "entity_dossiers", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_entity_dossiers_case_id", table_name="entity_dossiers")
    op.drop_table("entity_dossiers")
