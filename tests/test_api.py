import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_case_crud_flow():
    r = client.post("/api/cases", json={"canonical_title": "Test Case Alpha", "language": "en"})
    assert r.status_code == 200
    case_id = r.json()["id"]

    r = client.get("/api/cases")
    assert r.status_code == 200
    assert any(c["id"] == case_id for c in r.json())

    r = client.get(f"/api/cases/{case_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Test Case Alpha"
    assert body["status"] == "new"
    assert body["sources"] == 0
    assert body["facts"] == 0

    r = client.patch(f"/api/cases/{case_id}", json={"status": "archived"})
    assert r.status_code == 200
    assert r.json()["status"] == "archived"

    r = client.patch(f"/api/cases/{case_id}", json={"status": "new"})
    assert r.status_code == 200


def test_duplicate_slug_gets_suffix():
    r1 = client.post("/api/cases", json={"canonical_title": "Same Title"})
    r2 = client.post("/api/cases", json={"canonical_title": "Same Title"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["slug"] != r2.json()["slug"]


def test_source_add_and_list():
    r = client.post("/api/cases", json={"canonical_title": "Source Case"})
    case_id = r.json()["id"]

    r = client.post(
        f"/api/cases/{case_id}/sources",
        json={
            "title": "Article",
            "url": "https://example.com/a",
            "source_type": "article",
            "language": "en",
            "publisher": "Example News",
            "raw_text": "Authorized text body.",
            "reliability_score": 0.8,
            "is_authorized_text": True,
        },
    )
    assert r.status_code == 200

    r = client.get(f"/api/cases/{case_id}/sources")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Article"
    assert rows[0]["publisher"] == "Example News"
    assert rows[0]["reliability_score"] == 0.8


def test_add_source_missing_case_404():
    r = client.post(
        "/api/cases/99999/sources",
        json={"title": "x", "url": "https://x.com", "source_type": "other"},
    )
    assert r.status_code == 404


def test_source_delete_flow():
    r = client.post("/api/cases", json={"canonical_title": "Delete Source Case"})
    case_id = r.json()["id"]

    r = client.post(
        f"/api/cases/{case_id}/sources",
        json={
            "title": "Doomed Article",
            "url": "https://example.com/d",
            "source_type": "article",
            "language": "en",
        },
    )
    assert r.status_code == 200
    source_id = r.json()["id"]

    r = client.delete(f"/api/cases/{case_id}/sources/{source_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] == source_id

    assert client.get(f"/api/cases/{case_id}/sources").json() == []
    assert client.delete(f"/api/cases/{case_id}/sources/{source_id}").status_code == 404
    assert client.delete(f"/api/cases/99999/sources/{source_id}").status_code == 404


def test_empty_research_views():
    r = client.post("/api/cases", json={"canonical_title": "Empty Case"})
    case_id = r.json()["id"]

    assert client.get(f"/api/cases/{case_id}/facts").json() == []
    assert client.get(f"/api/cases/{case_id}/contradictions").json() == []
    assert client.get(f"/api/cases/{case_id}/timeline").json() == []
    assert client.get(f"/api/cases/{case_id}/stories").json() == []
    assert client.get(f"/api/cases/{case_id}/agent-runs").json() == []


def test_missing_case_404s():
    for path in [
        "/api/cases/99999",
        "/api/cases/99999/sources",
        "/api/cases/99999/facts",
        "/api/cases/99999/timeline",
        "/api/cases/99999/contradictions",
        "/api/cases/99999/stories",
        "/api/cases/99999/agent-runs",
    ]:
        assert client.get(path).status_code == 404, path


def test_generate_story_without_facts_fails():
    r = client.post("/api/cases", json={"canonical_title": "No Facts Case"})
    case_id = r.json()["id"]
    r = client.post(f"/api/cases/{case_id}/generate-story", json={"target_minutes": 45})
    assert r.status_code == 500
    assert "facts" in r.json()["detail"].lower()


def test_latest_story_404_when_none():
    r = client.post("/api/cases", json={"canonical_title": "No Story Case"})
    case_id = r.json()["id"]
    assert client.get(f"/api/cases/{case_id}/story/latest").status_code == 404


def test_improve_story_missing_version_404():
    r = client.post("/api/cases", json={"canonical_title": "Improve Case"})
    case_id = r.json()["id"]
    r = client.post(
        f"/api/cases/{case_id}/improve-story",
        json={"story_version_id": 999, "instruction": "improve hook"},
    )
    assert r.status_code == 404


def test_dashboard_shape():
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    for key in [
        "total_cases", "cases_researched", "stories_completed",
        "cases_waiting", "sources_collected",
    ]:
        assert key in body["stats"]
    assert isinstance(body["recent_cases"], list)
    assert isinstance(body["agent_activity"], list)


def test_settings_status_no_keys_exposed():
    r = client.get("/api/settings/status")
    assert r.status_code == 200
    body = r.json()
    assert "configured" in body["generation"]
    assert "configured" in body["youtube"]
    assert "connected" in body["database"]
    assert "api_key" not in str(body).lower()


def test_db_overview():
    r = client.get("/api/db/overview")
    assert r.status_code == 200
    body = r.json()
    for section in ["cases", "sources", "facts", "contradictions", "stories", "discovery_history"]:
        assert "count" in body[section]
        assert "items" in body[section]


def test_discovery_investigate_and_ignore_404():
    r = client.post("/api/discovery/99999/investigate", json={"language": "en"})
    assert r.status_code == 404
    r = client.post("/api/discovery/99999/ignore")
    assert r.status_code == 404


def test_agent_runs_endpoint():
    r = client.get("/api/agent-runs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
