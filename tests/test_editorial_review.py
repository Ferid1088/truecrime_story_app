"""Automatic editorial review of pictures with unclear rights: strict, logged, never over a gate."""

import asyncio
import json
import uuid

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.db.models import AppSetting, Case, EditorialReviewDecision, VisualAsset
from app.documentary.visuals import editorial_review as ER
from app.providers.generation.base import GenerationResult


def good(**over) -> dict:
    v = {"subject": "A studio portrait of Lizzie Borden", "identity": "certain", "right_subject": "yes",
         "period": "yes", "caption_matches": "yes", "other_case": False, "watermark": False,
         "graphic": False, "confidence": 0.9, "reasons": ["the caption names her"], "verdict": "approve"}
    v.update(over)
    return v


def test_a_clear_pass_is_approved_and_every_doubt_is_a_rejection():
    approved, reasons = ER.decide(good())
    assert approved and reasons == []
    for over, why in (
        ({"identity": "uncertain"}, "identity"), ({"identity": "wrong_person"}, "different person"),
        ({"right_subject": "unclear"}, "claimed to show"), ({"period": "no"}, "period"),
        ({"caption_matches": "unclear"}, "caption"), ({"other_case": True}, "another case"),
        ({"watermark": True}, "watermark"), ({"graphic": True}, "graphic"),
        ({"confidence": 0.5}, "confidence"), ({"verdict": "reject"}, "did not approve"),
    ):
        approved, reasons = ER.decide(good(**over))
        assert not approved and any(why in r for r in reasons), (over, reasons)
    assert not ER.decide({})[0] and not ER.decide(None)[0]           # no answer is no approval


def test_an_uncertain_identity_is_never_approved_even_when_the_reviewer_says_approve():
    approved, reasons = ER.decide(good(identity="uncertain", verdict="approve", confidence=0.99))
    assert not approved and any("identity" in r for r in reasons)


def test_the_setting_defaults_to_the_config_and_the_owner_can_switch_it(db_session):
    db_session.query(AppSetting).filter_by(key=ER.SETTING_KEY).delete()
    db_session.commit()
    assert ER.auto_review_enabled(db_session) is ai_config.editorial_review.auto_default is True
    ER.set_auto_review(db_session, False)
    assert ER.auto_review_enabled(db_session) is False
    ER.set_auto_review(db_session, True)
    assert ER.auto_review_enabled(db_session) is True


class ReviewerGen:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None, **kw):
        payload = json.loads(user)
        self.calls.append((role, payload["title"], bool(images)))
        return dict(self.answers[payload["title"]]), GenerationResult(
            text="{}", model="m/review", provider="fake")


def _asset(db, case, title, rights="editorial_review_required", verification="unverified", tmp_path=None):
    path = tmp_path / f"{uuid.uuid4().hex[:6]}.jpg"
    Image.new("RGB", (64, 64), (120, 120, 120)).save(path, "JPEG")
    a = VisualAsset(case_id=case.id, asset_code=f"VIS_{uuid.uuid4().hex[:8]}", provider="brave_images",
                    rights_status=rights, title=title, caption=f"caption of {title}",
                    local_path=str(path), thumbnail_path=str(path), verification_status=verification)
    db.add(a)
    db.commit()
    return a


def _case(db):
    c = Case(canonical_title=f"Review {uuid.uuid4().hex[:6]}", slug=uuid.uuid4().hex, language="en")
    db.add(c)
    db.commit()
    return c


def test_pending_pictures_are_reviewed_logged_and_only_clear_passes_are_approved(
        db_session, tmp_path, monkeypatch):
    case = _case(db_session)
    ok = _asset(db_session, case, "Lizzie portrait", tmp_path=tmp_path)
    wrong = _asset(db_session, case, "Another woman", tmp_path=tmp_path)
    modern = _asset(db_session, case, "Modern house", tmp_path=tmp_path)
    blocked = _asset(db_session, case, "Stock shot", rights="do_not_use", tmp_path=tmp_path)
    cleared = _asset(db_session, case, "Wikimedia photo", rights="public_domain", tmp_path=tmp_path)
    gen = ReviewerGen({
        "Lizzie portrait": good(),
        "Another woman": good(identity="wrong_person", reasons=["a different woman, per the caption"]),
        "Modern house": good(period="no", reasons=["modern cars and signs"]),
    })
    monkeypatch.setattr("app.agents.visual_editorial_review.get_generation_provider", lambda: gen)
    counts = asyncio.run(ER.review_pending(db_session, case))
    assert counts == {"reviewed": 3, "approved": 1, "rejected": 2, "kept": 0, "errors": 0}
    assert {c[1] for c in gen.calls} == {"Lizzie portrait", "Another woman", "Modern house"}
    for a in (ok, wrong, modern, blocked, cleared):
        db_session.refresh(a)
    assert ok.editorial_approved_at is not None and ok.editorial_approved_by.startswith("auto-review (m/review)")
    assert wrong.editorial_approved_at is None and modern.editorial_approved_at is None
    # other rights statuses are never touched
    assert blocked.editorial_approved_at is None and cleared.editorial_approved_at is None
    assert blocked.rights_status == "do_not_use"
    rows = {d.asset_code: d for d in db_session.query(EditorialReviewDecision).filter_by(case_id=case.id)}
    assert set(rows) == {ok.asset_code, wrong.asset_code, modern.asset_code}
    assert rows[wrong.asset_code].verdict == "rejected" and rows[wrong.asset_code].model == "m/review"
    assert any("different person" in r for r in json.loads(rows[wrong.asset_code].reasons_json))
    assert rows[ok.asset_code].verdict == "approved"
    # a second run asks the reviewer about nothing again
    gen.calls.clear()
    assert asyncio.run(ER.review_pending(db_session, case))["reviewed"] == 0 and not gen.calls
    assert asyncio.run(ER.review_pending(db_session, case, redo=True))["reviewed"] == 2   # the rejected two


def test_an_approved_picture_is_usable_for_a_render_that_allows_it_and_a_rejected_one_is_not(
        db_session, tmp_path, monkeypatch):
    from app.documentary.visuals import rights as R

    case = _case(db_session)
    ok = _asset(db_session, case, "Lizzie portrait", tmp_path=tmp_path)
    no = _asset(db_session, case, "Another woman", tmp_path=tmp_path)
    gen = ReviewerGen({"Lizzie portrait": good(), "Another woman": good(identity="uncertain")})
    monkeypatch.setattr("app.agents.visual_editorial_review.get_generation_provider", lambda: gen)
    asyncio.run(ER.review_pending(db_session, case))
    db_session.refresh(ok), db_session.refresh(no)
    assert R.usable(ok, "preview") is True and R.usable(no, "preview") is False


def test_the_review_api_setting_decisions_and_manual_run(client, db_session, tmp_path, monkeypatch):
    case = _case(db_session)
    _asset(db_session, case, "Lizzie portrait", tmp_path=tmp_path)
    gen = ReviewerGen({"Lizzie portrait": good(identity="uncertain", reasons=["nobody named"])})
    monkeypatch.setattr("app.agents.visual_editorial_review.get_generation_provider", lambda: gen)
    assert client.put("/api/documentary/visuals/editorial-review", json={"enabled": False}).json() == {"enabled": False}
    assert client.get(f"/api/documentary/visuals/editorial-review?case_id={case.id}").json()["enabled"] is False
    client.put("/api/documentary/visuals/editorial-review", json={"enabled": True})
    ran = client.post(f"/api/cases/{case.id}/visuals/editorial-review").json()
    assert ran["rejected"] == 1
    overview = client.get(f"/api/documentary/visuals/editorial-review?case_id={case.id}").json()
    assert overview["counts"] == {"rejected": 1} and overview["waiting"] == 1
    rows = client.get(f"/api/documentary/visuals/editorial-review/decisions?case_id={case.id}").json()
    assert rows[0]["verdict"] == "rejected" and rows[0]["model"] == "m/review"
    assert any("identity" in r for r in rows[0]["reasons"]) and rows[0]["thumb_url"].endswith("thumb=1")
    assert client.post("/api/cases/999999/visuals/editorial-review").status_code == 404
