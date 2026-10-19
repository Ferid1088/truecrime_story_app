"""HeyGen costs in the ledger (estimated, one entry per video) and the Unicode order of the
Persian and Arabic texts."""
import json

import pytest
from test_host_outfits import _scene
from test_studio import _segments

from app.core.ai_config import ai_config
from app.db.models import ProviderCall
from app.documentary import heygen_cost as HC
from app.documentary import studio as ST
from app.documentary.text_order import logical_order_issues


def _finished(db, tmp_path, monkeypatch, seconds=28.0, job="job-ledger-1"):
    scene = _scene(db, tmp_path, monkeypatch)
    scene.provider_job_id, scene.avatar_video_path = job, "x/v.webm"
    scene.avatar_meta_json = json.dumps({"duration": seconds})
    db.commit()
    return scene


def test_a_video_is_booked_once_as_an_estimate_from_the_price_list(db_session, tmp_path, monkeypatch):
    scene = _finished(db_session, tmp_path, monkeypatch, 30.0)
    row = HC.book_video(db_session, scene)
    from app.services.cost_model import load_pricing

    per_minute = float(load_pricing()["heygen"]["eur_per_minute"])
    assert row.stage == "heygen:avatar" and row.estimated is True and row.cost_source == "estimate"
    assert row.units == 30.0 and row.cost_eur == pytest.approx(30.0 / 60 * per_minute, abs=1e-5)
    assert row.case_id == scene.case_id and row.request_id == "heygen:job-ledger-1"
    again = HC.book_video(db_session, scene)
    assert again.id == row.id
    assert db_session.query(ProviderCall).filter_by(stage="heygen:avatar",
                                                    request_id="heygen:job-ledger-1").count() == 1


def test_nothing_is_booked_before_the_video_exists(db_session, tmp_path, monkeypatch):
    scene = _scene(db_session, tmp_path, monkeypatch)
    assert HC.book_video(db_session, scene) is None
    scene.provider_job_id = "job-x"
    assert HC.book_video(db_session, scene) is None       # a job without a finished video


def test_existing_videos_are_booked_retroactively_without_duplicates(db_session, tmp_path, monkeypatch):
    first = _finished(db_session, tmp_path, monkeypatch, 28.0, "job-ledger-a")
    second = _scene(db_session, tmp_path, monkeypatch, "de")
    second.provider_job_id, second.avatar_video_path = "job-ledger-b", "x/b.webm"
    second.avatar_meta_json = json.dumps({"duration": 26.0})
    db_session.commit()
    booked = {r.request_id for r in HC.book_existing(db_session)}
    assert {"heygen:job-ledger-a", "heygen:job-ledger-b"} <= booked
    count = db_session.query(ProviderCall).filter_by(stage="heygen:avatar").count()
    HC.book_existing(db_session)
    assert db_session.query(ProviderCall).filter_by(stage="heygen:avatar").count() == count
    assert first.id != second.id


def test_the_booking_counts_in_the_cases_spend(db_session, tmp_path, monkeypatch, client):
    scene = _finished(db_session, tmp_path, monkeypatch, 60.0, "job-spend")
    before = client.get(f"/api/cases/{scene.case_id}/costs").json()["spent_eur"]
    HC.book_video(db_session, scene)
    after = client.get(f"/api/cases/{scene.case_id}/costs").json()["spent_eur"]
    from app.services.cost_model import load_pricing

    assert after - before == pytest.approx(float(load_pricing()["heygen"]["eur_per_minute"]), abs=0.001)


def test_the_avatar_step_books_its_video(db_session, tmp_path, monkeypatch):
    from test_host_outfits import _look, _run
    from test_studio import FakeAvatar

    scene = _scene(db_session, tmp_path, monkeypatch)
    from app.documentary import outfits as OF

    outfit = OF.select_outfit(db_session, OF.create_outfit(
        db_session, scene.case_id, "en", "Navy", outfit_ref="navy_suit"))
    _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    _run(db_session, scene, FakeAvatar())
    rows = db_session.query(ProviderCall).filter_by(stage="heygen:avatar",
                                                    case_id=scene.case_id).all()
    assert len(rows) == 1 and rows[0].request_id == f"heygen:{scene.provider_job_id}"
    _run(db_session, scene, FakeAvatar())              # a rerun books nothing more
    assert db_session.query(ProviderCall).filter_by(stage="heygen:avatar",
                                                    case_id=scene.case_id).count() == 1


def test_persian_and_arabic_texts_must_be_logical_plain_unicode():
    assert logical_order_issues("لیزی وقتی اندرو برگشت خونه، بالای پله‌ها بود.") == []
    assert logical_order_issues("في بيت واحد، مات شخصان. لكن من كان هناك؟") == []
    reversed_text = "في بيت واحد، مات شخصان."[::-1]
    assert "starts_with_punctuation_as_if_reversed" in logical_order_issues(reversed_text)
    assert "presentation_forms" in logical_order_issues("ﺍﻟﻳ")        # shaped letters
    assert "direction_marks" in logical_order_issues("‫لیزی بود.‬")
    assert "not_nfc" in logical_order_issues("é لیزی")
    assert logical_order_issues("  ") == ["empty"]


def test_a_scene_with_a_reversed_persian_text_is_not_planned(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    row = _segments(db_session, "fa")
    segs = json.loads(row.segments_json)
    segs[0]["avatar_dialogue"] = "لیزی وقتی اندرو برگشت."[::-1]
    row.segments_json = json.dumps(segs, ensure_ascii=False)
    db_session.commit()
    with pytest.raises(ST.StudioError, match="logical order"):
        plan_host_scenes(db_session, row)


def test_the_stored_persian_and_arabic_opening_texts_read_in_logical_order(db_session):
    """The real opening texts of case 8 (when this database has them): first word first, the
    sentence mark at the end, no reversed form of a name, the file and the database agree."""
    from app.db.models import HostScene
    from app.documentary import storage

    scenes = [s for s in db_session.query(HostScene).filter(
        HostScene.language.in_(("fa", "ar")), HostScene.position == "opening") if s.voice_path]
    for scene in scenes:
        assert logical_order_issues(scene.text) == []
        side = storage.resolve(scene.voice_path)
        side = side.with_name(side.name + ".json")
        if side.exists():
            assert json.loads(side.read_text(encoding="utf-8"))["text"] == scene.text
