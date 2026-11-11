"""The opening block stage of a language's chain: one English opening for the film however many languages ask, a native row per language, and a block that
did not pass its checks leaves the film without it."""

import asyncio
import json

from test_host import _plan_story, _version
from test_opening_block import GOOD_CONTEXT, GOOD_HOOK, OpeningGen

from app.agents.opening import OpeningWriter
from app.core.ai_config import ai_config
from app.db.models import FilmOpening
from app.documentary.pipeline import opening as STAGE


def fake_media(calls):
    async def make_media(db, case_id, row, master, renderer=None, library=None):
        calls.append((row.language, row.kind, master.id))
        return {"audio": {"total": 23.0}, "music": {"prompt": "drone"}}
    return make_media


def test_the_english_opening_is_made_once_for_every_language_that_asks_at_the_same_time(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    de = _version(db_session, case, master, "de")
    de.language = "de"
    db_session.commit()
    gen, made = OpeningGen(), []
    monkeypatch.setattr("app.agents.opening.get_generation_provider", lambda: gen)
    monkeypatch.setattr(STAGE.OM, "make_media", fake_media(made))

    async def both():
        writer = OpeningWriter()
        return await asyncio.gather(STAGE.make_opening(db_session, case, "en", master, writer), STAGE.make_opening(db_session, case, "de", de, writer))

    en_result, de_result = asyncio.run(both())
    assert [r for r, _ in gen.calls].count("opening_director") == 1 and [r for r, _ in gen.calls].count("opening_native_writer") == 1
    assert en_result["hook"] == GOOD_HOOK and en_result["seconds"] == 23.0 and de_result["status"] == "valid"
    assert sorted(made) == sorted([("en", "master", made[0][2]), ("de", "native", made[0][2])])
    assert db_session.query(FilmOpening).filter(FilmOpening.case_id == case.id).count() == 2


def test_a_second_run_uses_the_openings_that_exist(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = OpeningGen()
    monkeypatch.setattr("app.agents.opening.get_generation_provider", lambda: gen)
    monkeypatch.setattr(STAGE.OM, "make_media", fake_media([]))
    asyncio.run(STAGE.make_opening(db_session, case, "en", master))
    asyncio.run(STAGE.make_opening(db_session, case, "en", master))
    assert [r for r, _ in gen.calls].count("opening_director") == 1


def test_an_english_opening_that_failed_its_checks_leaves_every_film_without_a_block(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen, made = OpeningGen(hooks=["Hello and welcome to the channel."]), []
    monkeypatch.setattr("app.agents.opening.get_generation_provider", lambda: gen)
    monkeypatch.setattr(STAGE.OM, "make_media", fake_media(made))
    out = asyncio.run(STAGE.make_opening(db_session, case, "en", master))
    assert "did not pass its checks" in out["degraded"] and made == []


def test_the_block_can_be_switched_off(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    monkeypatch.setattr(ai_config.opening_block, "enabled", False)
    out = asyncio.run(STAGE.make_opening(db_session, case, "en", master))
    assert out["skipped"] is True and db_session.query(FilmOpening).filter(FilmOpening.case_id == case.id).count() == 0


def test_the_film_takes_only_a_valid_block_whose_audio_is_unchanged(db_session, tmp_path, monkeypatch):
    import hashlib

    from app.documentary import storage
    from app.documentary.production import opening_timeline as OT

    monkeypatch.setattr(storage, "cases_root", lambda: tmp_path / "cases")
    case, master, _ = _plan_story(db_session)
    wav = tmp_path / "block.wav"
    wav.write_bytes(b"RIFFaudio")
    media = {"audio": {"path": str(wav), "sha256": hashlib.sha256(wav.read_bytes()).hexdigest(), "total": 23.0, "context": [4.0, 21.0], "hook": [0.0, 3.0]}}
    row = FilmOpening(case_id=case.id, language="en", kind="master", story_version_id=master.id, status="valid",
                      opening_json=json.dumps({"hook": GOOD_HOOK, "context": GOOD_CONTEXT}), media_json=json.dumps(media))
    db_session.add(row)
    db_session.commit()
    assert OT.opening_item(db_session, case.id, "en")["seconds"] == 23.0
    wav.write_bytes(b"changed")
    assert OT.opening_item(db_session, case.id, "en") is None          # the audio changed since it was made
    wav.write_bytes(b"RIFFaudio")
    row.status = "needs_review"
    db_session.commit()
    assert OT.opening_item(db_session, case.id, "en") is None
