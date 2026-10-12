from unittest.mock import patch

from app.db.models import ResearchJob


class DownSearchProvider:
    name = "truecrime"

    def is_configured(self):
        return True

    async def check_health(self):
        return False


def test_research_start_fails_fast_with_structured_search_warning(client, db_session):
    case_id = client.post(
        "/api/cases",
        json={"canonical_title": "Search Down Case", "language": "en", "force": True},
    ).json()["id"]
    with patch("app.api.deps.get_research_provider", return_value=DownSearchProvider()):
        response = client.post(f"/api/cases/{case_id}/research")
    assert response.status_code == 503
    assert response.json()["code"] == "SEARCH_UNAVAILABLE"
    assert response.json()["severity"] == "error"
    assert db_session.query(ResearchJob).filter(ResearchJob.case_id == case_id).count() == 0
