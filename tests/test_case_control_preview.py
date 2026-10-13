"""Case-level actions use the shared preview/apply boundary."""

import asyncio

from app.db.models import Case, CaseTitleCandidate, Thumbnail
from app.services.stage_control import StageControlService


def _case(db):
    case = Case(canonical_title="Preview case", slug="preview-case-control")
    db.add(case)
    db.flush()
    candidate = CaseTitleCandidate(
        case_id=case.id, language="en", title="The Preview Case",
        title_norm="the preview case", status="candidate",
    )
    thumbnail = Thumbnail(
        case_id=case.id, language="en", version=1, status="draft",
        critic_json='{"verdict":"pass"}', sha256="thumbnail-hash",
    )
    db.add_all([candidate, thumbnail])
    db.commit()
    return case, candidate, thumbnail


def test_naming_and_thumbnail_actions_preview_then_apply(db_session, monkeypatch):
    case, candidate, thumbnail = _case(db_session)
    service = StageControlService(db_session)

    naming_preview = service.preview_case_action(case.id, "naming_approve", candidate.id)
    assert naming_preview["scope"][0]["type"] == "naming_approve"
    assert naming_preview["estimated_provider_calls"]["total"] == 0

    def approve(db, _case, candidate_id, *, revise=False):
        row = db.get(CaseTitleCandidate, candidate_id)
        row.status = "selected"
        db.commit()
        return None

    monkeypatch.setattr("app.naming.agent.approve_candidate", approve)
    naming_result = asyncio.run(service.apply_case_action(naming_preview["preview_id"], case_id=case.id))
    assert naming_result["status"] == "completed"
    assert naming_result["changed_outside_scope"] == []
    assert db_session.get(CaseTitleCandidate, candidate.id).status == "selected"

    thumb_preview = service.preview_case_action(case.id, "thumbnail_reject", thumbnail.id)
    thumb_result = asyncio.run(service.apply_case_action(thumb_preview["preview_id"], case_id=case.id))
    assert thumb_result["executed_scope"] == thumb_result["previewed_scope"]
    assert db_session.get(Thumbnail, thumbnail.id).status == "rejected"
