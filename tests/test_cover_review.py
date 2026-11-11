"""The cover review routes (documentary > Covers): the covers of a language, the owner's change of a title or a scene, the preview, approve and skip. A cover can be
approved only after the preview of its present title and scene; changing an approved cover takes the approval away."""

from pathlib import Path

import pytest
from PIL import Image
from test_host import _plan_story

from app.core import files
from app.core.ai_config import ai_config
from app.covers import scene as SC
from app.covers import service as SV
from app.covers.api import final_path
from app.db.models import CoverProposal
from app.documentary import storage

ROOT = Path(__file__).resolve().parents[1]
OUTFIT = ROOT / "data" / "outfits" / "en" / "navyBlazerBeigeChinos" / "en_navyBlazerBeigeChinos_front.png"


@pytest.fixture()
def covers(db_session, client, monkeypatch, tmp_path):
    case, _, _ = _plan_story(db_session)
    monkeypatch.setattr(storage, "case_dir", lambda case_id: tmp_path / "cases" / str(case_id))
    monkeypatch.setattr(files, "is_allowed", lambda path: True)
    picture = tmp_path / "scene.jpg"
    Image.effect_noise((900, 700), 60).convert("RGB").save(picture)
    monkeypatch.setattr(SV, "film_avatar", lambda db, case_id, language: ("en/navyBlazerBeigeChinos", OUTFIT))
    monkeypatch.setattr(SC, "candidates", lambda db, case_, language: [{"code": c, "kind": "place", "path": picture, "what": f"picture {c}"} for c in ("VIS_1", "VIS_2")])
    keys = ["long", "C1"]
    for key in keys:
        db_session.add(CoverProposal(case_id=case.id, language="en", cover_key=key, kind="long" if key == "long" else "short", status="proposed", title="The open door",
                                     scene_code="VIS_1", confidence=0.8, reason="a door"))
    db_session.commit()
    return case, client


def url(case, *parts):
    return "/".join(["/api/cases", str(case.id), "covers", *parts])


def test_the_summary_counts_every_language_against_one_film_cover_and_five_shorts(covers):
    case, client = covers
    out = client.get(url(case, "summary")).json()
    assert out["expected_per_language"] == 6 and out["languages"]["en"]["proposed"] == 2 and out["languages"]["en"]["missing"] == 4
    assert out["languages"]["de"]["missing"] == 6 and set(out["languages"]) == set(ai_config.covers_settings().themes)


def test_a_language_shows_its_covers_and_the_candidate_scenes(covers):
    case, client = covers
    out = client.get(url(case, "en")).json()
    assert [c["key"] for c in out["covers"]] == ["long", "C1"] and [c["code"] for c in out["candidates"]] == ["VIS_1", "VIS_2"]
    assert out["safe_zones"] == "to_test" and out["platform_cover_ways"]["instagram_reel"] == "to_test"
    assert client.get(url(case, "xx")).status_code == 404


def test_the_owner_changes_a_title_and_a_scene_into_a_newer_version(covers):
    case, client = covers
    out = client.put(url(case, "en", "C1"), json={"title": "Who opened it", "scene_code": "VIS_2"}).json()
    assert out["version"] == 2 and out["source"] == "owner" and out["title"] == "Who opened it" and out["scene_code"] == "VIS_2" and out["status"] == "proposed"
    assert client.get(url(case, "en")).json()["covers"][1]["title"] == "Who opened it"
    assert client.put(url(case, "en", "C1"), json={"title": "one two three four five"}).status_code == 422
    assert client.put(url(case, "en", "C1"), json={"scene_code": "VIS_99"}).status_code == 422
    cleared = client.put(url(case, "en", "C1"), json={"clear_scene": True}).json()
    assert cleared["scene_code"] is None and cleared["status"] == "open"


def test_a_cover_is_approved_only_after_its_preview_and_only_with_a_title_and_a_scene(covers):
    case, client = covers
    assert client.post(url(case, "en", "C1", "approve")).status_code == 409                       # no preview yet
    shown = client.post(url(case, "en", "C1", "preview")).json()
    assert shown["preview_url"] and shown["problems"] == []
    image = client.get(shown["preview_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    done = client.post(url(case, "en", "C1", "approve")).json()
    assert done["status"] == "approved" and done["approved_url"] and done["validation"]["size"] == [2160, 3840]
    assert Image.open(final_path(case.id, "en", "C1")).size == (2160, 3840)
    client.put(url(case, "en", "long"), json={"clear_scene": True})
    assert client.post(url(case, "en", "long", "approve")).status_code == 409


def test_changing_an_approved_cover_takes_the_approval_away_and_needs_a_new_preview(covers):
    case, client = covers
    client.post(url(case, "en", "C1", "preview"))
    client.post(url(case, "en", "C1", "approve"))
    changed = client.put(url(case, "en", "C1"), json={"title": "Another title"}).json()
    assert changed["status"] == "proposed" and changed["approved_url"] is None and changed["preview_url"] is None
    assert client.post(url(case, "en", "C1", "approve")).status_code == 409


def test_a_cover_can_be_skipped_and_the_scene_pictures_are_served(covers):
    case, client = covers
    assert client.post(url(case, "en", "C1", "skip")).json()["status"] == "skipped"
    assert client.get(url(case, "en")).json()["covers"][1]["status"] == "skipped"
    picture = client.get(url(case, "en", "scenes", "VIS_1", "image"))
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"
    assert client.get(url(case, "en", "scenes", "VIS_9", "image")).status_code == 404


def test_the_long_cover_is_made_in_the_size_of_the_platforms(covers):
    case, client = covers
    client.post(url(case, "en", "long", "preview"))
    assert client.post(url(case, "en", "long", "approve")).json()["validation"]["size"] == [3840, 2160]
    assert Image.open(final_path(case.id, "en", "long")).size == (3840, 2160)
    assert final_path(case.id, "en", "long").stat().st_size <= ai_config.covers_settings().formats["long"].max_bytes
