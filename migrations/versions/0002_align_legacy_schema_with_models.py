"""Bring the legacy (ALTER TABLE era) schema in line with the ORM models.

Tightens ~30 columns to NOT NULL (verified: no NULLs in production), adds the indexes the
models declare, and the self-referencing foreign key on story_versions.master_version_id.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10 23:43:30.466456
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('agent_runs', schema=None) as batch_op:
        batch_op.alter_column('fallback_used',
               existing_type=sa.BOOLEAN(),
               nullable=False,
               existing_server_default=sa.text('0'))

    with op.batch_alter_table('cases', schema=None) as batch_op:
        batch_op.alter_column('resolution_status',
               existing_type=sa.VARCHAR(length=30),
               nullable=False,
               existing_server_default=sa.text("'UNKNOWN'"))
        batch_op.alter_column('aliases_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.alter_column('people_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.alter_column('identifiers_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.create_index(batch_op.f('ix_cases_resolution_status'), ['resolution_status'], unique=False)

    with op.batch_alter_table('discovery_candidates', schema=None) as batch_op:
        batch_op.alter_column('state',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               existing_server_default=sa.text("'suggested'"))
        batch_op.alter_column('resolution_status',
               existing_type=sa.VARCHAR(length=30),
               nullable=False,
               existing_server_default=sa.text("'UNKNOWN'"))
        batch_op.alter_column('aliases_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.alter_column('people_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.alter_column('source_urls_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.create_index(batch_op.f('ix_discovery_candidates_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_discovery_candidates_state'), ['state'], unique=False)

    with op.batch_alter_table('documentary_jobs', schema=None) as batch_op:
        batch_op.alter_column('production_type',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               existing_server_default=sa.text("'original'"))

    with op.batch_alter_table('epistemic_claims', schema=None) as batch_op:
        batch_op.alter_column('assertion_role',
               existing_type=sa.VARCHAR(length=40),
               nullable=False,
               existing_server_default=sa.text("'NARRATOR_ASSERTION'"))
        batch_op.create_index(batch_op.f('ix_epistemic_claims_assertion_role'), ['assertion_role'], unique=False)
        batch_op.create_index(batch_op.f('ix_epistemic_claims_parent_claim_key'), ['parent_claim_key'], unique=False)

    with op.batch_alter_table('facts', schema=None) as batch_op:
        batch_op.alter_column('original_language',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               existing_server_default=sa.text("'en'"))
        batch_op.alter_column('chunk_ids_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.alter_column('transcript_claim_ids_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.alter_column('people_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))
        batch_op.alter_column('locations_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'[]'"))

    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.alter_column('content_status',
               existing_type=sa.VARCHAR(length=30),
               nullable=False,
               existing_server_default=sa.text("'summary_only'"))
        batch_op.alter_column('status',
               existing_type=sa.VARCHAR(length=50),
               nullable=False,
               existing_server_default=sa.text("'active'"))

    with op.batch_alter_table('story_versions', schema=None) as batch_op:
        batch_op.alter_column('kind',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               existing_server_default=sa.text("'direct'"))
        batch_op.alter_column('language',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               existing_server_default=sa.text("'fa'"))
        batch_op.alter_column('similarity_status',
               existing_type=sa.VARCHAR(length=30),
               nullable=False,
               existing_server_default=sa.text("'not_evaluated'"))
        batch_op.alter_column('status',
               existing_type=sa.VARCHAR(length=20),
               nullable=False,
               existing_server_default=sa.text("'draft'"))
        batch_op.alter_column('is_best',
               existing_type=sa.BOOLEAN(),
               nullable=False,
               existing_server_default=sa.text('0'))
        batch_op.create_index(batch_op.f('ix_story_versions_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_story_versions_status'), ['status'], unique=False)
        batch_op.create_foreign_key("fk_story_versions_master_version_id", 'story_versions', ['master_version_id'], ['id'])

    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.alter_column('render_profile',
               existing_type=sa.VARCHAR(length=10),
               nullable=False,
               existing_server_default=sa.text("'preview'"))

    with op.batch_alter_table('visual_assets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_visual_assets_entity_key'), ['entity_key'], unique=False)

    with op.batch_alter_table('visual_audits', schema=None) as batch_op:
        batch_op.alter_column('detail_json',
               existing_type=sa.TEXT(),
               nullable=False,
               existing_server_default=sa.text("'{}'"))



def downgrade() -> None:
    # Undo = restore the verified backup taken before this step (scripts/db_tools.py backup/verify).
    raise RuntimeError("0002 is not reversible in place; restore the pre-migration backup instead.")
