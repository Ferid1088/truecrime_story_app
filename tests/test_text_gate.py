"""The owner's review of Persian titles and cover texts before any cover is made: every text is listed, each can be corrected, the release is bound to exactly the texts he saw, a changed text
voids it, and no cover of that language is rendered without a valid release. Other languages are not held."""

import json

import pytest
from test_host import _plan_story

from app.core.ai_config import ai_config
from app.covers import text_gate as TG
from app.covers import variants as V
from app.db.models import CoverProposal, ShortClip, TextApproval

KEYS = ["long", "C1", "C2", "C3", "C4", "C5"]


@pytest.fixture()
def review(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.covers_settings(), "text_review_languages", ["fa"])
    case, master, _ = _plan_story(db_session)
    from app.identity.titles import sync_identity

    sync_identity(db_session, case, "fa", title="عنوان فیلم برای آزمایش بازبینی متن")
    for key in KEYS:
        db_session.add(CoverProposal(case_id=case.id, language="fa", cover_key=key, kind="long" if key == "long" else "short", version=1, source="agent", status="open",
                                     title=f"متن روی جلد {key} با هشت کلمه یا بیشتر برای آزمایش", scene_prompt=""))
        if key != "long":
            db_session.add(ShortClip(case_id=case.id, language="fa", clip_key=key, kind="native", story_version_id=master.id, version=1, status="valid",
                                     script_json=json.dumps({"package": {"title": f"عنوان کوتاه {key}"}})))
    db_session.commit()
    return case


def test_every_text_the_owner_reviews_is_listed_in_the_order_of_the_film(db_session, review):
    listed = TG.items(db_session, review, "fa")
    assert [(t["kind"], t["key"]) for t in listed] == [("film_title", "film"), ("cover_text", "long"), *[(kind, key) for key in KEYS[1:] for kind in ("short_title", "cover_text")]]
    assert listed[0]["text"] == "عنوان فیلم برای آزمایش بازبینی متن" and listed[2]["text"] == "عنوان کوتاه C1" and listed[3]["text"].startswith("متن روی جلد C1")


def test_no_cover_is_made_before_the_release_and_the_release_is_bound_to_the_texts_he_saw(db_session, review):
    with pytest.raises(TG.TextsNotReleased):
        TG.guard(db_session, review, "fa")
    with pytest.raises(V.VariantError) as refused:
        V.render(db_session, review, "fa", "C1")
    assert refused.value.reason == "texts_not_released"
    done = TG.release(db_session, review, "fa", "owner")
    assert done["released"] is True and done["released_by"] == "owner"
    TG.guard(db_session, review, "fa")                                                          # released: nothing stops a cover now
    TG.edit(db_session, review, "fa", "cover_text", "C2", "متن تازه روی جلد دوم که مالک اصلاح کرده است حالا")
    after = TG.status(db_session, review, "fa")
    assert after["released"] is False and after["changed_since_release"] is True
    with pytest.raises(TG.TextsNotReleased):
        TG.guard(db_session, review, "fa")


def test_a_correction_is_kept_as_the_owners_version_and_written_where_the_text_lives(db_session, review):
    TG.edit(db_session, review, "fa", "cover_text", "C3", "متن اصلاح شده توسط مالک برای جلد سوم با کلمات کافی")
    TG.edit(db_session, review, "fa", "short_title", "C4", "عنوان تازه که مالک نوشت")
    TG.edit(db_session, review, "fa", "film_title", "film", "عنوان تازه فیلم که مالک نوشت برای آزمایش")
    current = {t["key"] + ":" + t["kind"]: t["text"] for t in TG.items(db_session, review, "fa")}
    assert current["C3:cover_text"].startswith("متن اصلاح شده") and current["C4:short_title"] == "عنوان تازه که مالک نوشت" and current["film:film_title"].startswith("عنوان تازه فیلم")
    newest = db_session.query(CoverProposal).filter_by(case_id=review.id, language="fa", cover_key="C3").order_by(CoverProposal.id.desc()).first()
    assert (newest.source, newest.version) == ("owner", 2)


def test_a_bad_correction_is_refused_and_an_empty_text_blocks_the_release(db_session, review):
    with pytest.raises(TG.TextError):
        TG.edit(db_session, review, "fa", "cover_text", "C1", "کوتاه")                           # a cover text has 8 to 18 words
    with pytest.raises(TG.TextError):
        TG.edit(db_session, review, "fa", "short_title", "C1", "   ")
    clip = db_session.query(ShortClip).filter_by(case_id=review.id, language="fa", clip_key="C5").one()
    clip.script_json = json.dumps({"package": {"title": ""}})
    db_session.commit()
    with pytest.raises(TG.TextError, match="empty"):
        TG.release(db_session, review, "fa")
    assert db_session.query(TextApproval).filter_by(case_id=review.id).count() == 0


def test_a_language_that_is_not_reviewed_is_not_held_and_the_routes_work(db_session, review, client):
    TG.guard(db_session, review, "en")                                                          # English has no review
    assert client.get(f"/api/cases/{review.id}/texts/fa").json()["released"] is False
    bad = client.put(f"/api/cases/{review.id}/texts/fa/cover_text/C1", json={"text": "کوتاه"})
    assert bad.status_code == 422
    ok = client.put(f"/api/cases/{review.id}/texts/fa/short_title/C1", json={"text": "عنوان اصلاح شده از راه مسیر"})
    assert ok.status_code == 200 and client.post(f"/api/cases/{review.id}/texts/fa/release", json={}).json()["released"] is True
    assert client.delete(f"/api/cases/{review.id}/texts/fa/release").json()["released"] is False
    assert client.get("/api/cases/999999/texts/fa").status_code == 404
