import os
import sys
import tempfile

_PG_URL = os.environ.get("TC_TEST_DATABASE_URL")
_COPY_URL = os.environ.get("TC_COPY_DATABASE_URL")
_PG_ADMIN_URL = os.environ.get("TRUECRIME_TEST_ADMIN_URL")


def _postgres_admin_url(pg_url):
    """Return a verified local admin URL, or None with one actionable line."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    parsed = make_url(pg_url)
    candidates = [_PG_ADMIN_URL] if _PG_ADMIN_URL else []
    local_user = os.environ.get("PGUSER") or os.environ.get("USER") or "postgres"
    candidates.append(parsed.set(
        username=local_user, password=None, database="postgres"
    ).render_as_string(hide_password=False))
    if parsed.host in (None, "localhost", "127.0.0.1"):
        candidates.append(f"postgresql+psycopg:///postgres?user={local_user}")
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        engine = create_engine(candidate, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return candidate
        except Exception:
            pass
        finally:
            engine.dispose()
    print(
        "POSTGRES_TEST_ADMIN_SKIP: no usable TRUECRIME_TEST_ADMIN_URL or local "
        "admin connection; migration-copy tests will be skipped",
        flush=True,
    )
    return None


_PG_RESOLVED_ADMIN = None
if _COPY_URL:
    # An explicit disposable copy is used by the Phase-B reconciliation smoke
    # test.  Unlike the Postgres suite, it must not drop or recreate anything.
    os.environ["DATABASE_URL"] = _COPY_URL
elif _PG_URL:
    # Run the suite on Postgres: start every run from an empty schema (the tests share one database).
    import sqlalchemy as _sa
    from sqlalchemy.engine import make_url as _make_url

    _PG_RESOLVED_ADMIN = _postgres_admin_url(_PG_URL)
    if _PG_RESOLVED_ADMIN:
        _admin_url = _make_url(_PG_RESOLVED_ADMIN).set(
            database=_make_url(_PG_URL).database
        )
        _eng = _sa.create_engine(_admin_url, isolation_level="AUTOCOMMIT")
        with _eng.connect() as _c:
            _c.execute(_sa.text("DROP SCHEMA public CASCADE"))
            _c.execute(_sa.text("CREATE SCHEMA public"))
            _c.execute(_sa.text("ALTER SCHEMA public OWNER TO truecrime"))
        _eng.dispose()
        os.environ["TRUECRIME_TEST_ADMIN_URL"] = _PG_RESOLVED_ADMIN
    else:
        os.environ["TRUECRIME_TEST_ADMIN_UNAVAILABLE"] = "1"
    os.environ["DATABASE_URL"] = _PG_URL
else:
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"
# the in-app monitor scheduler never starts inside tests
os.environ["TRUECRIME_DISABLE_SCHEDULER"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def isolated_pipeline_db(monkeypatch, tmp_path):
    """Give the full documentary golden test a disposable database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.db.base as base
    import app.db.models  # noqa: F401 - populate Base.metadata
    from app.db.migrate import upgrade_database
    import app.documentary.music as music

    # The persistent-template builder supplies a disposable copy through
    # TC_COPY_DATABASE_URL.  Use that database so the child run can copy the
    # actual populated baseline; ordinary suite runs get a fresh per-test DB.
    database_url = os.environ.get("TC_COPY_DATABASE_URL")
    if database_url:
        engine = create_engine(database_url)
    else:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'pipeline-isolated.db'}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )
    upgrade_database(engine)
    monkeypatch.setattr(base, "engine", engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(base, "SessionLocal", factory)
    monkeypatch.setattr(sys.modules[__name__], "SessionLocal", factory)
    monkeypatch.setattr(music, "SessionLocal", factory)
    try:
        yield
    finally:
        engine.dispose()


@pytest.fixture
def isolated_host_history(db_session):
    """Give host integration tests a clean cross-case history.

    The test database is intentionally shared for speed, while host
    anti-repetition deliberately reads other cases' persisted segments.
    Clear that boundary explicitly; the test creates its own archive rows.
    """
    from app.db.models import HostMemory, HostSegments

    db_session.query(HostSegments).delete(synchronize_session=False)
    db_session.query(HostMemory).delete(synchronize_session=False)
    db_session.commit()
    return db_session


@pytest.fixture(autouse=True)
def _bypass_provider_preflight(monkeypatch):
    """The provider authorization preflight is a live-network probe —
    endpoints should not depend on external provider state in tests.
    Individual tests exercise the preflight itself via a fake provider."""
    async def _ok():
        return {"configured": True, "reachable": True, "authorized": True,
                "status": "authorized"}

    async def _authorized():
        return {"provider": "apimaster", "role": "generation",
                "configured": True, "reachable": True, "authorized": True,
                "models_available": [], "models_missing": [],
                "status": "ok"}

    monkeypatch.setattr(
        "app.api.deps.require_generation_authorized", _ok
    )
    monkeypatch.setattr(
        "app.api.deps.generation_provider_status", _authorized
    )


# --- tests that need optional packages: skipped (not failed) when missing ------
import importlib.util  # noqa: E402

_NEEDS = {
    "cv2": [
        "test_chapters.py::test_the_renderer_draws_the_cards",
        "test_media_library.py::test_clip_frames_follow_the_shot_time_and_hold_at_the_end",
        "test_media_library.py::test_render_plays_clips_muted_under_the_script_audio",
        "test_traceability.py::test_an_interrupted_render_leaves_no_film_under_the_final_name",
        "test_visual_direction.py::test_pipeline_runs_the_gap_stage_and_records_usage",
        "test_visual_production.py::test_render_engine_produces_mp4_with_audio_and_subtitles",
        "test_visual_production.py::test_one_button_pipeline_pilot_end_to_end",
        "test_visual_production.py::test_a_failing_language_does_not_stop_the_others",
        "test_intros.py",
    ],
    "whisper_normalizer": [
        "test_voice_render.py::test_normalization_keeps_numbers_apart_across_commas",
    ],
}


def pytest_ignore_collect(collection_path, config):
    """test_intros imports cv2 at module level: skip the module, not the run."""
    if collection_path.name == "test_intros.py" and importlib.util.find_spec("cv2") is None:
        return True
    return None


def pytest_collection_modifyitems(config, items):
    for dep, ids in _NEEDS.items():
        if importlib.util.find_spec(dep) is not None:
            continue
        mark = pytest.mark.skip(reason=f"optional package '{dep}' is not installed")
        for item in items:
            if any(item.nodeid.endswith(i) for i in ids):
                item.add_marker(mark)


@pytest.fixture(autouse=True)
def _no_title_drafting_in_pipeline_tests(monkeypatch):
    """Pipeline tests use scripted model answers; the naming stage (many
    model calls) has its own tests."""
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.case_naming, "generate_in_pipeline", False)
