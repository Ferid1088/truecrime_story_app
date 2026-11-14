"""The cover review routes (documentary > Covers): the covers of a language, the owner's change of a title or a scene, the scene picture the image model makes (paid, kept
once made), the preview, approve and skip, and the cost book. A cover can be approved only after the preview of its present title and picture; changing an approved
cover takes the approval away."""

import io
from pathlib import Path

import pytest
from PIL import Image
from test_cover_images import SCENE, FakeImages, bad, good
from test_host import _plan_story

from app.core import files
from app.core.ai_config import ai_config
from app.covers import images as IM
from app.covers import service as SV
from app.covers.api import final_path
from app.db.models import CoverProposal
from app.documentary import storage

ROOT = Path(__file__).resolve().parents[1]
OUTFIT = ROOT / "data" / "outfits" / "en" / "navyBlazerBeigeChinos" / "en_navyBlazerBeigeChinos_front.png"


def noise() -> bytes:
    buffer = io.BytesIO()
    Image.effect_noise((600, 400), 60).convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture()
def covers(db_session, client, monkeypatch, tmp_path):
    case, _, _ = _plan_story(db_session)
    monkeypatch.setattr(storage, "case_dir", lambda case_id: tmp_path / "cases" / str(case_id))
    monkeypatch.setattr(files, "is_allowed", lambda path: True)
    monkeypatch.setattr(SV, "film_avatar", lambda db, case_id, language: ("en/navyBlazerBeigeChinos", OUTFIT))
    provider = FakeImages([])
    monkeypatch.setattr(IM, "APIMasterImageProvider", lambda: provider)
    for key in ("long", "C1"):
        db_session.add(CoverProposal(case_id=case.id, language="en", cover_key=key, kind="long" if key == "long" else "short", status="proposed", title="The open door",
                                     scene_prompt=SCENE, confidence=0.8, reason="a door"))
    db_session.commit()
    provider.picture = lambda: good().__class__(ok=True, content=noise(), usage=good().usage, request_id="1", duration_ms=39200)
    return case, client, provider


def url(case, *parts):
    return "/".join(["/api/cases/", str(case.id), "covers", *parts]).replace("//", "/")


def press_image(client, case, key, provider, count=1):
    provider.results = [provider.picture() for _ in range(count)]
    return client.post(url(case, "en", key, "image"), json={})


def test_the_summary_counts_every_language_against_one_film_cover_and_five_shorts(covers):
    case, client, _ = covers
    out = client.get(url(case, "summary")).json()
    assert out["expected_per_language"] == 6 and out["languages"]["en"]["proposed"] == 2 and out["languages"]["en"]["missing"] == 4
    assert out["languages"]["de"]["missing"] == 6 and set(out["languages"]) == set(ai_config.covers_settings().themes)


def test_a_language_shows_its_covers_their_scene_descriptions_and_the_cost_book(covers):
    case, client, _ = covers
    out = client.get(url(case, "en")).json()
    assert [c["key"] for c in out["covers"]] == ["long", "C1"] and out["covers"][0]["scene_prompt"] == SCENE and out["covers"][0]["image"] is None
    assert out["costs"]["pictures"] == 0 and out["costs"]["estimated"] is True and out["costs"]["rate_status"] == "unverified"
    assert out["safe_zones"] == "to_test" and client.get(url(case, "xx")).status_code == 404


def test_the_owner_changes_a_title_and_a_scene_into_a_newer_version_a_title_keeps_the_picture(covers):
    case, client, provider = covers
    press_image(client, case, "C1", provider)
    kept = client.put(url(case, "en", "C1"), json={"title": "Who opened it first"}).json()
    assert kept["version"] == 2 and kept["source"] == "owner" and kept["title"] == "Who opened it first" and kept["image"] is not None          # the picture does not depend on the title
    dropped = client.put(url(case, "en", "C1"), json={"scene": "A garage door raised halfway on a snowy night, light spilling out"}).json()
    assert dropped["image"] is None and dropped["status"] == "proposed"
    assert client.put(url(case, "en", "C1"), json={"title": "one two three four five six seven eight nine ten eleven"}).status_code == 422
    assert client.put(url(case, "en", "C1"), json={"scene": "A man with a pistol in a hall"}).status_code == 422
    assert client.put(url(case, "en", "C1"), json={"scene": ""}).json()["status"] == "open"


def test_the_picture_is_made_once_with_its_cost_and_only_again_when_asked(covers):
    case, client, provider = covers
    made = press_image(client, case, "long", provider).json()
    assert made["image"]["cost_usd"] == 0.04752 and made["image"]["duration_s"] == 39.2 and made["image"]["image_tokens_out"] == 1584
    assert client.get(made["image"]["url"]).headers["content-type"] == "image/png"
    assert press_image(client, case, "long", provider).json()["image"]["id"] == made["image"]["id"] and len(provider.calls) == 1     # nothing paid twice
    provider.results = [provider.picture()]
    again = client.post(url(case, "en", "long", "image"), json={"again": True})
    assert again.json()["image"]["id"] != made["image"]["id"] and len(provider.calls) == 2
    book = client.get(url(case, "en", "costs")).json()
    assert book["pictures"] == 2 and book["image_tokens_out"] == 3168 and book["cost_usd"] == 0.09504 and book["seconds"] == 78.4 and book["rows"][0]["cost_source"] == "assumed-unverified"


def test_a_failed_picture_is_reported_and_the_attempts_are_in_the_cost_book(covers):
    case, client, provider = covers
    provider.results = [bad(), bad(), bad()]
    failed = client.post(url(case, "en", "long", "image"), json={})
    assert failed.status_code == 409 and "3 attempts failed" in failed.json()["detail"]
    book = client.get(url(case, "en", "costs")).json()
    assert book["failed"] == 3 and book["pictures"] == 0 and book["cost_usd"] == 0 and all(r["error"] for r in book["rows"])


def test_a_cover_is_approved_only_after_its_picture_and_its_preview(covers):
    case, client, provider = covers
    assert client.post(url(case, "en", "C1", "preview")).status_code == 409                       # no picture yet
    assert client.post(url(case, "en", "C1", "approve")).status_code == 409
    press_image(client, case, "C1", provider)
    assert client.post(url(case, "en", "C1", "approve")).status_code == 409                       # no preview yet
    shown = client.post(url(case, "en", "C1", "preview")).json()
    assert shown["preview_url"] and shown["problems"] == []
    assert client.get(shown["preview_url"]).headers["content-type"] == "image/jpeg"
    done = client.post(url(case, "en", "C1", "approve")).json()
    assert done["status"] == "approved" and done["approved_url"] and done["validation"]["size"] == [2160, 3840]
    assert Image.open(final_path(case.id, "en", "C1")).size == (2160, 3840)


def test_changing_an_approved_cover_takes_the_approval_away_and_needs_a_new_preview(covers):
    case, client, provider = covers
    press_image(client, case, "C1", provider)
    client.post(url(case, "en", "C1", "preview"))
    client.post(url(case, "en", "C1", "approve"))
    changed = client.put(url(case, "en", "C1"), json={"title": "Another title again"}).json()
    assert changed["status"] == "proposed" and changed["approved_url"] is None and changed["preview_url"] is None
    assert client.post(url(case, "en", "C1", "approve")).status_code == 409


def test_a_cover_can_be_skipped(covers):
    case, client, _ = covers
    assert client.post(url(case, "en", "C1", "skip")).json()["status"] == "skipped"
    assert client.get(url(case, "en")).json()["covers"][1]["status"] == "skipped"


def test_the_long_cover_is_made_in_the_size_of_the_platforms(covers):
    case, client, provider = covers
    press_image(client, case, "long", provider)
    client.post(url(case, "en", "long", "preview"))
    assert client.post(url(case, "en", "long", "approve")).json()["validation"]["size"] == [3840, 2160]
    assert Image.open(final_path(case.id, "en", "long")).size == (3840, 2160)
    assert final_path(case.id, "en", "long").stat().st_size <= ai_config.covers_settings().formats["long"].max_bytes
