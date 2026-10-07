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
        "app.main._require_generation_authorized", _ok
    )
    monkeypatch.setattr(
        "app.main._generation_provider_status", _authorized
    )
