"""The Opening tab: the first seconds of each language's film (before the host's first appearance): only languages that have an opening are listed, the latest version counts, the voice and the
pictures are links only when their files exist, and the files are served."""

import json
import uuid

import pytest

from app.core import files
from app.db.models import Case, FilmOpening


@pytest.fixture()
def case(db_session, monkeypatch):
    monkeypatch.setattr(files, "is_allowed", lambda path: True)
    row = Case(canonical_title="Opening case", slug=f"opening-{uuid.uuid4().hex[:8]}", language="en")
    db_session.add(row)
    db_session.commit()
    return row


def add(db, case, language, *, kind="native", status="valid", version=1, opening=None, media=None, issues=None):
    row = FilmOpening(case_id=case.id, language=language, kind=kind, status=status, version=version, opening_json=json.dumps(opening or {"hook": f"hook {language}", "context": f"context {language}", "music": {"mood": "dread", "prompt": "cello"}, "scenes": []}),
                      media_json=json.dumps(media or {}), validation_json=json.dumps({"issues": issues or []}), generation_model="m")
    db.add(row)
    db.commit()
    return row


def voiced(tmp_path, name="opening.wav"):
    wav = tmp_path / name
    wav.write_bytes(b"RIFF....WAVE")
    return {"audio": {"path": str(wav), "seconds": 20.0, "hook": [0.0, 2.0], "context": [3.5, 18.0]}}


def test_only_openings_with_a_voice_are_listed_and_the_latest_version_counts(db_session, client, case, tmp_path):
    add(db_session, case, "de", status="needs_review", version=1, opening={"hook": "old", "context": "old", "music": {}, "scenes": []}, media=voiced(tmp_path, "old.wav"))
    add(db_session, case, "de", version=2, opening={"hook": "Neuer Hook", "context": "Text", "music": {}, "scenes": []}, media=voiced(tmp_path, "new.wav"))
    add(db_session, case, "en", kind="master", media=voiced(tmp_path, "en.wav"))
    add(db_session, case, "fa", version=6)                                  # the text is written, the voice is not made: no opening yet
    add(db_session, case, "tr", media={"audio": {"path": str(tmp_path / "gone.wav"), "seconds": 5}})        # the file is gone
    out = client.get(f"/api/cases/{case.id}/openings").json()
    assert [x["language"] for x in out["languages"]] == ["en", "de"] and out["languages"][1]["hook"] == "Neuer Hook" and out["languages"][1]["version"] == 2
    assert out["languages"][0]["kind"] == "master" and out["languages"][0]["status"] == "valid"


def test_a_case_without_any_opening_lists_nothing(db_session, client, case):
    assert client.get(f"/api/cases/{case.id}/openings").json()["languages"] == [] and client.get("/api/cases/99999/openings").status_code == 404


def test_the_master_shows_the_hooks_that_were_weighed_and_which_was_chosen(db_session, client, case, tmp_path):
    add(db_session, case, "en", kind="master", media=voiced(tmp_path), opening={"hook": "b", "context": "c", "music": {"mood": "dread", "prompt": "p"}, "scenes": ["a door", "a hall"],
                                                       "candidates": [{"text": "a", "kind": "quote", "why": "x"}, {"text": "b", "kind": "moment", "why": "y"}], "chosen": 1})
    en = client.get(f"/api/cases/{case.id}/openings").json()["languages"][0]
    assert [(c["text"], c["chosen"]) for c in en["candidates"]] == [("a", False), ("b", True)] and en["music"] == {"mood": "dread", "prompt": "p"} and [s["text"] for s in en["scenes"]] == ["a door", "a hall"]


def test_the_pictures_are_links_only_when_their_files_exist_and_the_files_are_served(db_session, client, case, tmp_path):
    wav, png = tmp_path / "opening.wav", tmp_path / "garage.png"
    wav.write_bytes(b"RIFF....WAVE")
    png.write_bytes(b"\x89PNG....")
    add(db_session, case, "de", opening={"hook": "h", "context": "c", "music": {}, "scenes": ["garage", "hall"]},
        media={"audio": {"path": str(wav), "seconds": 20.3, "hook": [0.0, 2.3], "context": [3.8, 18.4]}, "pictures": [{"scene": "garage", "path": str(png)}, {"scene": "hall", "path": str(tmp_path / "missing.png")}]})
    add(db_session, case, "tr", media={"audio": {"path": str(tmp_path / "gone.wav"), "seconds": 5}})
    languages = {x["language"]: x for x in client.get(f"/api/cases/{case.id}/openings").json()["languages"]}
    de = languages["de"]
    assert "tr" not in languages and "/openings/de/audio?v=" in de["audio"]["url"] and de["audio"]["seconds"] == 20.3 and de["audio"]["hook"] == [0.0, 2.3]
    assert [s["picture_url"] is not None for s in de["scenes"]] == [True, False]
    served = client.get(de["audio"]["url"])
    assert served.content.startswith(b"RIFF") and served.headers["cache-control"] == "no-cache" and "etag" in served.headers and client.get(de["scenes"][0]["picture_url"]).content.startswith(b"\x89PNG")
    assert client.get(f"/api/cases/{case.id}/openings/tr/audio").status_code == 404 and client.get(f"/api/cases/{case.id}/openings/de/picture/1").status_code == 404
    assert client.get(f"/api/cases/{case.id}/openings/es/audio").status_code == 404 and client.get(f"/api/cases/{case.id}/openings/de/picture/9").status_code == 404


def test_a_new_audio_has_a_new_address_so_that_no_browser_plays_the_old_one(db_session, client, case, tmp_path):
    first = tmp_path / "one.wav"
    first.write_bytes(b"RIFF....WAVE")
    row = add(db_session, case, "de", media={"audio": {"path": str(first), "sha256": "a" * 64, "seconds": 20.0}})
    before = client.get(f"/api/cases/{case.id}/openings").json()["languages"][0]["audio"]["url"]
    row.media_json = json.dumps({"audio": {"path": str(first), "sha256": "b" * 64, "seconds": 20.0}})
    db_session.commit()
    after = client.get(f"/api/cases/{case.id}/openings").json()["languages"][0]["audio"]["url"]
    assert before != after and before.endswith("?v=" + "a" * 12) and after.endswith("?v=" + "b" * 12)


def test_the_checks_of_an_opening_are_shown(db_session, client, case, tmp_path):
    add(db_session, case, "fr", status="needs_review", media=voiced(tmp_path), issues=[{"type": "too_long", "fix": "Shorten the context."}])
    assert client.get(f"/api/cases/{case.id}/openings").json()["languages"][0]["issues"] == [{"type": "too_long", "fix": "Shorten the context."}]
