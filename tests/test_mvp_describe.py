"""Just before the film is planned: what each approved picture or clip shows, and which of the case's people it shows."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.db.models import Case, VisualAsset
from app.documentary.mvp import describe as DS

ENTITIES = [{"key": "jill_smith", "name": "Jill Smith", "type": "person"}, {"key": "caleb_flynn", "name": "Caleb Flynn", "type": "person"}]


def asset(code="VIS_1", sha="abc", spec=None, **kw):
    return VisualAsset(case_id=1, asset_code=code, asset_type=kw.pop("asset_type", "photo"), local_path=f"{code}.jpg", sha256=sha,
                       spec_json=json.dumps(spec or {}), **kw)


def test_only_people_of_the_case_stay_in_a_card_and_an_answer_that_is_no_card_is_refused():
    card = DS.valid({"shows": "A woman at a podium.", "people": [{"entity": "jill_smith", "sure": "yes"},
                                                                {"entity": "someone_else", "sure": "yes"}, {"entity": "caleb_flynn"}],
                     "setting": "courtroom", "kind": "portrait"}, {"jill_smith", "caleb_flynn"})
    assert card["people"] == [{"entity": "jill_smith", "sure": "yes"}, {"entity": "caleb_flynn", "sure": "likely"}]
    assert DS.valid({"people": []}, {"jill_smith"}) is None and DS.valid("x", {"jill_smith"}) is None


def test_a_card_belongs_to_the_file_it_was_made_for_and_the_director_reads_it_instead_of_the_title():
    a = asset(spec={"picture_card": {"shows": "Jill Smith speaks at a podium.", "people": [{"entity": "jill_smith"}], "setting": "courtroom",
                                     "sha": "abc"}}, title="Prosecutors said Caleb staged a home invasion")
    assert DS.people_of(a) == ["jill_smith"] and DS.depicts(a).startswith("Jill Smith speaks at a podium.")
    a.sha256 = "changed"
    assert DS.card_of(a) is None and DS.people_of(a) == [] and DS.depicts(a) == "Prosecutors said Caleb staged a home invasion"


def test_every_approved_picture_gets_a_card_once_and_a_failed_answer_is_not_a_card(db_session, monkeypatch):
    case = Case(canonical_title="Describe", slug="describe-1", language="en")
    db_session.add(case)
    db_session.commit()
    photos = []
    for n in range(2):
        a = VisualAsset(case_id=case.id, asset_code=f"VIS_{n}", asset_type="photo", local_path="x.jpg", sha256=f"sha{n}", spec_json="{}")
        db_session.add(a)
        photos.append(a)
    db_session.commit()
    answers = [{"shows": "A woman.", "people": [{"entity": "jill_smith", "sure": "yes"}], "setting": "x", "kind": "portrait"},
               {"nonsense": True}]
    calls = []

    async def fake_run_agent(name, gen, body, images=None):
        calls.append(name)
        return answers[len(calls) - 1], SimpleNamespace(model="m", provider="p", usage=None)

    monkeypatch.setattr(DS, "run_agent", fake_run_agent)
    async def unused_image(asset):
        return "unused"

    monkeypatch.setattr(DS, "checked_image", unused_image)
    monkeypatch.setattr(DS.IM, "data_url", lambda p: "data:image/png;base64,")
    monkeypatch.setattr(DS, "get_generation_provider", lambda: object())
    monkeypatch.setattr(DS, "stamp_run", lambda run, res, role: None)
    monkeypatch.setattr(DS, "limit_size", lambda name: 1)
    stats = asyncio.run(DS.describe_approved(db_session, case, photos, ENTITIES))
    assert (stats["described"], stats["failed"]) == (1, 1)
    assert DS.people_of(photos[0]) == ["jill_smith"] and DS.card_of(photos[1]) is None
    calls.clear()
    answers[:] = [{"shows": "A man.", "people": [], "setting": "y", "kind": "portrait"}]
    again = asyncio.run(DS.describe_approved(db_session, case, photos, ENTITIES))
    assert again["described"] == 1 and len(calls) == 1, "the picture that has a card is not asked again"


def test_the_picture_check_runs_on_a_plain_copy_of_the_asset_never_the_orm_object(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.db.models import VisualAsset
    from app.documentary.mvp import media

    seen = []
    monkeypatch.setattr(media, "image_for_check", lambda asset: seen.append(asset) or "thumb.jpg")
    asset = VisualAsset(asset_type="photo", thumbnail_path="t.jpg", local_path="p.jpg", case_id=1, asset_code="VIS_1")
    assert asyncio.run(media.checked_image(asset)) == "thumb.jpg"
    assert isinstance(seen[0], SimpleNamespace) and seen[0].asset_code == "VIS_1" and seen[0].thumbnail_path == "t.jpg"


def test_a_snapshot_has_every_column_of_the_asset_so_a_worker_thread_never_needs_the_session():
    from app.db.models import VisualAsset
    from app.documentary.mvp.media import snapshot

    copy = snapshot(VisualAsset(asset_type="video", local_path="v.mp4", clip_start=1.5, clip_end=9.0, duration_seconds=7.5, asset_code="VIS_2"))
    assert (copy.asset_type, copy.clip_start, copy.clip_end, copy.duration_seconds) == ("video", 1.5, 9.0, 7.5)
    assert set(vars(copy)) == set(VisualAsset.__table__.columns.keys())
