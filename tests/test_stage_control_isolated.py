"""Backend stage-control E2E executed against the disposable E2E database."""

from __future__ import annotations

from test_stage_control import _fixture


def test_edit_german_spoken_chapter_keeps_other_language_artifacts_identical(
    db_session, client, monkeypatch
):
    case, _job, roots = _fixture(db_session)
    before = {language: roots[language].output_hash for language in ("en", "fa", "ar")}
    preview = client.post(f"/api/documentary/{case.id}/stage-control/preview", json={
        "stage": "spoken:de", "action": "edit",
        "edit": {"language": "de", "chapter_index": 0, "chapter_text": "new German chapter"},
    })
    assert preview.status_code == 200, preview.text
    assert {item["language"] for item in preview.json()["estimated_scope"]} == {"de"}
    monkeypatch.setattr("app.documentary.jobs.launch", lambda _job_id: None)
    applied = client.post(f"/api/documentary/{case.id}/stage-control/apply", json={
        "preview_id": preview.json()["preview_id"],
    })
    assert applied.status_code == 200, applied.text
    assert {language: roots[language].output_hash for language in ("en", "fa", "ar")} == before
