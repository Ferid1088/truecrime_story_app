"""Database schema setup: create tables, add columns missing in older databases,
backfill ids. Called once at startup (app/main.py)."""

from sqlalchemy import inspect, text

from app.db.base import Base, engine
from app.db.models import Case


def _add_model_columns(conn, model) -> None:
    """ALTER TABLE ADD COLUMN for every column the model declares and the
    existing table lacks (SQLite; scalar defaults only)."""
    table = model.__tablename__
    if table not in inspect(conn).get_table_names():
        return
    have = {c["name"] for c in inspect(conn).get_columns(table)}
    for col in model.__table__.columns:
        if col.name in have:
            continue
        ddl = f'ALTER TABLE {table} ADD COLUMN "{col.name}" {col.type.compile(dialect=conn.dialect)}'
        default = getattr(col.default, "arg", None)
        if isinstance(default, bool):
            ddl += f" DEFAULT {int(default)}"
        elif isinstance(default, (int, float)):
            ddl += f" DEFAULT {default}"
        elif isinstance(default, str):
            ddl += " DEFAULT '" + default.replace("'", "''") + "'"
        conn.execute(text(ddl))


def _ensure_columns():
    """Lightweight guard for columns added after the initial schema.

    create_all only creates missing tables, never alters existing ones,
    so add missing columns to pre-existing SQLite tables explicitly.
    """
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("facts")}
        if "event_date" not in cols:
            conn.execute(text("ALTER TABLE facts ADD COLUMN event_date VARCHAR(40)"))
        for col, ddl in {
            "original_claim": "ALTER TABLE facts ADD COLUMN original_claim TEXT",
            "original_language": "ALTER TABLE facts ADD COLUMN original_language VARCHAR(20) DEFAULT 'en'",
            "narrative_value": "ALTER TABLE facts ADD COLUMN narrative_value VARCHAR(40)",
            "evidence_strength": "ALTER TABLE facts ADD COLUMN evidence_strength VARCHAR(40)",
            "supporting_text": "ALTER TABLE facts ADD COLUMN supporting_text TEXT",
            "chunk_ids_json": "ALTER TABLE facts ADD COLUMN chunk_ids_json TEXT DEFAULT '[]'",
            "quote_status": "ALTER TABLE facts ADD COLUMN quote_status VARCHAR(40)",
            "speaker": "ALTER TABLE facts ADD COLUMN speaker VARCHAR(300)",
            "people_json": "ALTER TABLE facts ADD COLUMN people_json TEXT DEFAULT '[]'",
            "locations_json": "ALTER TABLE facts ADD COLUMN locations_json TEXT DEFAULT '[]'",
            "transcript_claim_ids_json": "ALTER TABLE facts ADD COLUMN transcript_claim_ids_json TEXT DEFAULT '[]'",
        }.items():
            if col not in cols:
                conn.execute(text(ddl))
        # Stable case identity was added to the ORM after the original
        # SQLite database was created. Keep this migration deliberately
        # narrow: it adds only the missing nullable column and its index.
        case_cols = {c["name"] for c in inspect(conn).get_columns("cases")}
        if "case_uid" not in case_cols:
            conn.execute(text("ALTER TABLE cases ADD COLUMN case_uid VARCHAR(20)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cases_case_uid ON cases(case_uid)"))
        story_cols = {c["name"] for c in inspect(conn).get_columns("story_versions")}
        if "language" not in story_cols:
            conn.execute(text("ALTER TABLE story_versions ADD COLUMN language VARCHAR(20) DEFAULT 'fa'"))
        for col, ddl in {
            "generation_provider": "ALTER TABLE story_versions ADD COLUMN generation_provider VARCHAR(50)",
            "generation_model": "ALTER TABLE story_versions ADD COLUMN generation_model VARCHAR(200)",
            "similarity_status": "ALTER TABLE story_versions ADD COLUMN similarity_status VARCHAR(30) DEFAULT 'not_evaluated'",
            "status": "ALTER TABLE story_versions ADD COLUMN status VARCHAR(20) DEFAULT 'draft'",
            "is_best": "ALTER TABLE story_versions ADD COLUMN is_best BOOLEAN DEFAULT 0",
            "text_hash": "ALTER TABLE story_versions ADD COLUMN text_hash VARCHAR(64)",
            "narrative_structure": "ALTER TABLE story_versions ADD COLUMN narrative_structure TEXT",
            "kind": "ALTER TABLE story_versions ADD COLUMN kind VARCHAR(20) DEFAULT 'direct'",
            "master_version_id": "ALTER TABLE story_versions ADD COLUMN master_version_id INTEGER",
            "derived_from_master_version": "ALTER TABLE story_versions ADD COLUMN derived_from_master_version INTEGER",
            "native_quality_score": "ALTER TABLE story_versions ADD COLUMN native_quality_score FLOAT",
            "semantic_consistency_score": "ALTER TABLE story_versions ADD COLUMN semantic_consistency_score FLOAT",
            "factual_consistency_score": "ALTER TABLE story_versions ADD COLUMN factual_consistency_score FLOAT",
        }.items():
            if col not in story_cols:
                conn.execute(text(ddl))
        # similarity_score must be nullable (not_evaluated -> NULL), but the
        # original schema created it NOT NULL. SQLite cannot relax a column
        # constraint in place, so rebuild the table once if needed.
        nullable = {
            c["name"]: c["nullable"] for c in inspect(conn).get_columns("story_versions")
        }
        if nullable.get("similarity_score") is False:
            cols = ", ".join(
                f'"{c["name"]}"' for c in inspect(conn).get_columns("story_versions")
            )
            conn.execute(text("ALTER TABLE story_versions RENAME TO story_versions_old"))
            conn.execute(text("""
                CREATE TABLE story_versions (
                    id INTEGER NOT NULL PRIMARY KEY,
                    case_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    narrative_angle TEXT NOT NULL,
                    story_text TEXT NOT NULL,
                    engagement_score FLOAT NOT NULL,
                    similarity_score FLOAT,
                    critic_notes TEXT,
                    created_at DATETIME NOT NULL,
                    language VARCHAR(20) DEFAULT 'fa',
                    generation_provider VARCHAR(50),
                    generation_model VARCHAR(200),
                    similarity_status VARCHAR(30) DEFAULT 'not_evaluated',
                    status VARCHAR(20) DEFAULT 'draft',
                    is_best BOOLEAN DEFAULT 0,
                    text_hash VARCHAR(64),
                    narrative_structure TEXT,
                    kind VARCHAR(20) DEFAULT 'direct',
                    master_version_id INTEGER,
                    derived_from_master_version INTEGER,
                    native_quality_score FLOAT,
                    semantic_consistency_score FLOAT,
                    factual_consistency_score FLOAT,
                    FOREIGN KEY(case_id) REFERENCES cases (id)
                )
            """))
            conn.execute(text(
                f"INSERT INTO story_versions ({cols}) SELECT {cols} FROM story_versions_old"
            ))
            # Scores recorded by the old no-op similarity critic (0 source
            # texts) were placeholders, not real evaluations.
            conn.execute(text(
                "UPDATE story_versions SET similarity_score = NULL "
                "WHERE similarity_status = 'not_evaluated'"
            ))
            conn.execute(text("DROP TABLE story_versions_old"))
        run_cols = {c["name"] for c in inspect(conn).get_columns("agent_runs")}
        for col, ddl in {
            "provider": "ALTER TABLE agent_runs ADD COLUMN provider VARCHAR(50)",
            "model": "ALTER TABLE agent_runs ADD COLUMN model VARCHAR(200)",
            "role": "ALTER TABLE agent_runs ADD COLUMN role VARCHAR(50)",
            "temperature": "ALTER TABLE agent_runs ADD COLUMN temperature FLOAT",
            "fallback_used": "ALTER TABLE agent_runs ADD COLUMN fallback_used BOOLEAN DEFAULT 0",
            "input_tokens": "ALTER TABLE agent_runs ADD COLUMN input_tokens INTEGER",
            "output_tokens": "ALTER TABLE agent_runs ADD COLUMN output_tokens INTEGER",
            "total_tokens": "ALTER TABLE agent_runs ADD COLUMN total_tokens INTEGER",
            "estimated_cost_usd": "ALTER TABLE agent_runs ADD COLUMN estimated_cost_usd FLOAT",
            "generation_id": "ALTER TABLE agent_runs ADD COLUMN generation_id VARCHAR(200)",
            "text_hash": "ALTER TABLE agent_runs ADD COLUMN text_hash VARCHAR(64)",
        }.items():
            if col not in run_cols:
                conn.execute(text(ddl))
        src_cols = {c["name"] for c in inspect(conn).get_columns("sources")}
        for col, ddl in {
            "summary": "ALTER TABLE sources ADD COLUMN summary TEXT",
            "summary_en": "ALTER TABLE sources ADD COLUMN summary_en TEXT",
            "content_status": "ALTER TABLE sources ADD COLUMN content_status VARCHAR(30) DEFAULT 'summary_only'",
            "value_flags": "ALTER TABLE sources ADD COLUMN value_flags TEXT",
            "published_at": "ALTER TABLE sources ADD COLUMN published_at VARCHAR(80)",
            "retrieved_at": "ALTER TABLE sources ADD COLUMN retrieved_at DATETIME",
            "research_provider": "ALTER TABLE sources ADD COLUMN research_provider VARCHAR(50)",
            "external_reference": "ALTER TABLE sources ADD COLUMN external_reference VARCHAR(500)",
            "status": "ALTER TABLE sources ADD COLUMN status VARCHAR(50) DEFAULT 'active'",
            "source_family": "ALTER TABLE sources ADD COLUMN source_family VARCHAR(300)",
            "retrieval_method": "ALTER TABLE sources ADD COLUMN retrieval_method VARCHAR(50)",
            "retrieval_notes": "ALTER TABLE sources ADD COLUMN retrieval_notes TEXT",
            "declared_language": "ALTER TABLE sources ADD COLUMN declared_language VARCHAR(20)",
            "detected_language": "ALTER TABLE sources ADD COLUMN detected_language VARCHAR(20)",
            "language_confidence": "ALTER TABLE sources ADD COLUMN language_confidence FLOAT",
            "language_detection_method": "ALTER TABLE sources ADD COLUMN language_detection_method VARCHAR(40)",
        }.items():
            if col not in src_cols:
                conn.execute(text(ddl))
        if "research_jobs" in inspect(conn).get_table_names():
            rj_cols = {c["name"] for c in inspect(conn).get_columns("research_jobs")}
            for col, ddl in {
                "current_stage": "ALTER TABLE research_jobs ADD COLUMN current_stage VARCHAR(60)",
                "model": "ALTER TABLE research_jobs ADD COLUMN model VARCHAR(200)",
                "profile": "ALTER TABLE research_jobs ADD COLUMN profile VARCHAR(60)",
                "error_code": "ALTER TABLE research_jobs ADD COLUMN error_code VARCHAR(50)",
                "search_calls": "ALTER TABLE research_jobs ADD COLUMN search_calls INTEGER",
                "fetch_calls": "ALTER TABLE research_jobs ADD COLUMN fetch_calls INTEGER",
                "input_tokens": "ALTER TABLE research_jobs ADD COLUMN input_tokens INTEGER",
                "output_tokens": "ALTER TABLE research_jobs ADD COLUMN output_tokens INTEGER",
                "total_tokens": "ALTER TABLE research_jobs ADD COLUMN total_tokens INTEGER",
                "model_cost_usd": "ALTER TABLE research_jobs ADD COLUMN model_cost_usd FLOAT",
                "search_cost_usd": "ALTER TABLE research_jobs ADD COLUMN search_cost_usd FLOAT",
                "fetch_cost_usd": "ALTER TABLE research_jobs ADD COLUMN fetch_cost_usd FLOAT",
                "total_cost_usd": "ALTER TABLE research_jobs ADD COLUMN total_cost_usd FLOAT",
                "sources_discovered": "ALTER TABLE research_jobs ADD COLUMN sources_discovered INTEGER",
                "sources_accepted": "ALTER TABLE research_jobs ADD COLUMN sources_accepted INTEGER",
                "sources_rejected": "ALTER TABLE research_jobs ADD COLUMN sources_rejected INTEGER",
                "languages_completed": "ALTER TABLE research_jobs ADD COLUMN languages_completed TEXT",
            }.items():
                if col not in rj_cols:
                    conn.execute(text(ddl))
        if "documentary_jobs" in inspect(conn).get_table_names():
            dj = {c["name"]: c for c in inspect(conn).get_columns("documentary_jobs")}
            for col, ddl in {
                "refresh_visuals": "ALTER TABLE documentary_jobs ADD COLUMN refresh_visuals BOOLEAN DEFAULT 0",
                "from_zero": "ALTER TABLE documentary_jobs ADD COLUMN from_zero BOOLEAN DEFAULT 0",
                "target_minutes": "ALTER TABLE documentary_jobs ADD COLUMN target_minutes FLOAT",
                "batch_id": "ALTER TABLE documentary_jobs ADD COLUMN batch_id VARCHAR(40)",
                "production_type": "ALTER TABLE documentary_jobs ADD COLUMN production_type VARCHAR(20) DEFAULT 'original'",
                "follow_up_id": "ALTER TABLE documentary_jobs ADD COLUMN follow_up_id INTEGER",
            }.items():
                if col not in dj:
                    conn.execute(text(ddl))
            if dj.get("master_version_id", {}).get("nullable") is False:
                # "from zero" jobs have no master yet: relax NOT NULL by
                # rebuilding the table once (SQLite cannot alter it).
                cols = ", ".join(f'"{c}"' for c in
                                 [c["name"] for c in inspect(conn).get_columns("documentary_jobs")])
                conn.execute(text("ALTER TABLE documentary_jobs RENAME TO documentary_jobs_old"))
                for (idx,) in conn.execute(text(
                        "SELECT name FROM sqlite_master WHERE type='index' AND "
                        "tbl_name='documentary_jobs_old' AND name NOT LIKE 'sqlite_%'")).all():
                    conn.execute(text(f'DROP INDEX "{idx}"'))
                conn.commit()
                from app.db.models import DocumentaryJob
                DocumentaryJob.__table__.create(conn)
                conn.execute(text(
                    f"INSERT INTO documentary_jobs ({cols}) SELECT {cols} FROM documentary_jobs_old"))
                conn.execute(text("DROP TABLE documentary_jobs_old"))
        # Case lifecycle + media library columns: added from the models
        # (new nullable / defaulted columns only).
        from app.db.models import (DiscoveryCandidate as _DC, EpisodeIdentity as _EI,
                                   HostScene as _HS, ProductionScript as _PS,
                                   VisualAsset as _VA, Video as _VD)
        from app.db.models import EpistemicClaim as _EC
        for model in (Case, _DC, _VA, _HS, _PS, _VD, _EI, _EC):
            _add_model_columns(conn, model)
        conn.commit()


def _backfill_case_uids():
    from app.db.base import SessionLocal
    from app.identity.titles import backfill_case_uids

    with SessionLocal() as _db:
        backfill_case_uids(_db)


def init_schema() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _backfill_case_uids()
