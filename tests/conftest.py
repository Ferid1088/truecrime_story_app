import os
import tempfile

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


@pytest.fixture(autouse=True)
def _bypass_provider_preflight(monkeypatch):
    """The OpenRouter authorization preflight is a live-network probe —
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
