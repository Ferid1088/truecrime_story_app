"""The host's outfit per section (S1 ... S4) and language: chosen with the case, before any text or host scene exists."""
from uuid import uuid4

from fastapi.testclient import TestClient
from test_host_outfit_window import library  # noqa: F401  (the fixture of the library photos)

from app.db.models import Case, HostScene
from app.documentary import outfits as OF
from app.main import app

client = TestClient(app)


def _case(db):
    case = Case(canonical_title="Sections", slug=f"hs-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


def _choose(case, section, ref, library_rows, situation="sitHands", language="en"):
    slug = ref.split("/")[1]
    return client.post(f"/api/cases/{case.id}/host-section-outfits", json={
        "language": language, "section": section, "option_key": f"registry:{ref}",
        "item_id": library_rows[(slug, situation)].id})


def test_the_sections_are_s1_to_s4_and_an_empty_case_has_no_outfit_yet(db_session):
    case = _case(db_session)
    view = client.get(f"/api/cases/{case.id}/host-section-outfits", params={"language": "en"}).json()
    assert [s["section"] for s in view["sections"]] == ["S1", "S2", "S3", "S4"]
    assert all(s["outfit"] is None and not s["chosen"] for s in view["sections"])


def test_each_section_gets_its_own_outfit_before_any_scene_exists(db_session, library):  # noqa: F811
    case = _case(db_session)
    assert _choose(case, "S1", "en/blazerJeans", library).status_code == 200
    body = _choose(case, "S3", "en/brownSweater", library).json()
    names = {s["section"]: (s["outfit"] or {}).get("name") for s in body["sections"]}
    assert names["S1"] and names["S3"] and names["S1"] != names["S3"] and names["S2"] is None
    assert [s["chosen"] for s in body["sections"]] == [True, False, True, False]
    assert db_session.query(HostScene).filter_by(case_id=case.id).count() == 0


def test_a_scene_wears_the_outfit_of_its_section_else_the_videos_outfit(db_session, library):  # noqa: F811
    case = _case(db_session)
    video_outfit = OF.select_outfit(db_session, OF.apply_choice(
        db_session, case.id, "en", "registry:en/brownSweater", [library[("brownSweater", "sitHands")].id], "test",
        select=False)["outfit"])          # (not "select": that would remember the choice for every later case)
    _choose(case, "S2", "en/blazerJeans", library)
    scene = HostScene(case_id=case.id, language="en", host_segment_id="S2", story_version_id=1, host_segments_id=1,
                      channel="c", studio_profile_id="p", studio_asset_id="a", framing_preset="MEDIUM")
    other = HostScene(case_id=case.id, language="en", host_segment_id="S1", story_version_id=1, host_segments_id=1,
                      channel="c", studio_profile_id="p", studio_asset_id="a", framing_preset="MEDIUM")
    db_session.add_all([scene, other])
    db_session.commit()
    try:
        assert OF.scene_outfit(db_session, scene).name != video_outfit.name
        assert OF.scene_outfit(db_session, other).id == video_outfit.id
        scene.chosen_outfit_id = video_outfit.id                  # the choice for one scene alone wins
        assert OF.scene_outfit(db_session, scene).id == video_outfit.id
    finally:                         # the database is shared between tests: leave no scene behind
        db_session.delete(scene)
        db_session.delete(other)
        db_session.commit()


def test_taking_the_choice_back_and_the_guards(db_session, library):  # noqa: F811
    case = _case(db_session)
    _choose(case, "S4", "en/blazerJeans", library)
    back = client.post(f"/api/cases/{case.id}/host-section-outfits",
                       json={"language": "en", "section": "S4", "option_key": None}).json()
    assert not back["sections"][3]["chosen"]
    assert client.post(f"/api/cases/{case.id}/host-section-outfits",
                       json={"language": "en", "section": "S5", "option_key": None}).status_code == 409
    assert client.post(f"/api/cases/{case.id}/host-section-outfits",
                       json={"language": "en", "section": "S1", "option_key": "registry:en/blazerJeans"}).status_code == 422


def test_each_language_keeps_its_own_sections(db_session, library):  # noqa: F811
    case = _case(db_session)
    _choose(case, "S1", "en/blazerJeans", library)
    other = client.get(f"/api/cases/{case.id}/host-section-outfits", params={"language": "de"}).json()
    assert all(s["outfit"] is None for s in other["sections"])
