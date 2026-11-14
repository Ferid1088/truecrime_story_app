"""The music of the film openings: every case gets three different dark, mysterious thriller tracks that all its languages hear; new tracks are made, three per case, until the library holds the configured
number (60), after that one is taken at random; every made track is kept in the database and booked once. Nothing is paid: the generator is a fake."""

from __future__ import annotations

import asyncio
import json
import random
from uuid import uuid4

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, FilmOpening, OpeningMusicTrack, ProviderCall
from app.documentary import opening_media as OM
from app.documentary import storage
from app.documentary.opening_music import OpeningMusic


class FakeLibrary:
    def __init__(self, folder):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.made, self.lengths = [], []

    async def _generate(self, prompt, seconds, loop, path):
        self.made.append(prompt)
        self.lengths.append(seconds)
        path.write_bytes(b"RIFFfake")
        return 99


@pytest.fixture
def setup(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.opening_block_settings().music, "tracks", 3)
    yield db_session, tmp_path, FakeLibrary(tmp_path / "music")


def new_case(db):
    case = Case(canonical_title="Music", slug=f"opening-music-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


def give(db, case, language, tracks):
    db.add(FilmOpening(case_id=case.id, language=language, kind="native", status="valid", opening_json="{}",
                       media_json=json.dumps({"music": {"tracks": [{"track_id": t.id} for t in tracks]}})))
    db.commit()


def choose(db, library, case, rng=None):
    return asyncio.run(OpeningMusic(db, library, rng).choose(case.id))


def test_a_new_case_gets_three_new_different_thriller_tracks_that_are_kept_and_booked_once(setup):
    db, _, library = setup
    case = new_case(db)
    tracks = choose(db, library, case)
    music = ai_config.opening_block_settings().music
    assert len(tracks) == music.tracks == 3 and library.made == [t.prompt for t in tracks] and len(set(library.made)) == 3 and library.lengths == [30, 30, 30]
    assert all("orchestral thriller music" in t.prompt and len(t.prompt) <= music.prompt_max_chars and storage.resolve(t.file_path).exists() for t in tracks)
    assert [t.track_code for t in tracks] == ["openingbed_0000", "openingbed_0001", "openingbed_0002"] and db.query(OpeningMusicTrack).count() == 3
    for t in tracks:
        booked = db.query(ProviderCall).filter(ProviderCall.request_id == f"music_opening:{t.track_code}").all()
        assert len(booked) == 1 and booked[0].case_id == case.id and booked[0].estimated


def test_every_language_of_a_case_hears_the_same_tracks_and_nothing_more_is_made(setup):
    db, _, library = setup
    case = new_case(db)
    first = choose(db, library, case)
    give(db, case, "en", first)
    assert [t.id for t in choose(db, library, case)] == [t.id for t in first]
    assert len(library.made) == 3 and db.query(OpeningMusicTrack).count() == 3


def test_each_case_gets_its_own_new_tracks_with_another_sound_until_the_library_is_full(setup, monkeypatch):
    db, _, library = setup
    monkeypatch.setattr(ai_config.opening_block_settings().music, "library_target", 6)
    sets = []
    for _ in range(2):
        case = new_case(db)
        tracks = choose(db, library, case)
        give(db, case, "en", tracks)
        sets.append(tracks)
    assert len(library.made) == 6 and len({t.track_code for s in sets for t in s}) == 6 and len({t.variation_key for s in sets for t in s}) == 6
    assert db.query(OpeningMusicTrack).count() == 6


def test_a_full_library_makes_nothing_new_and_a_case_takes_three_different_ones_at_random(setup, monkeypatch):
    db, _, library = setup
    monkeypatch.setattr(ai_config.opening_block_settings().music, "library_target", 4)
    choose(db, library, new_case(db))
    assert len(library.made) == 3
    taken = [choose(db, library, new_case(db), random.Random(seed)) for seed in range(10)]
    assert len(library.made) == 4 and db.query(OpeningMusicTrack).count() == 4
    assert all(len({t.id for t in s}) == 3 for s in taken)
    assert len({tuple(t.id for t in s) for s in taken}) >= 2


def test_a_track_whose_file_is_gone_is_not_taken_and_the_library_makes_a_new_one(setup, monkeypatch):
    db, _, library = setup
    monkeypatch.setattr(ai_config.opening_block_settings().music, "library_target", 3)
    first = choose(db, library, new_case(db))
    storage.resolve(first[0].file_path).unlink()
    again = choose(db, library, new_case(db))
    assert len(library.made) == 4 and all(storage.resolve(t.file_path).exists() for t in again) and len({t.id for t in again}) == 3


def test_the_opening_media_takes_its_music_from_the_library(setup):
    db, tmp_path, library = setup
    case = new_case(db)
    music = asyncio.run(OM.ensure_music(db, case.id, library))
    tracks = music["tracks"]
    assert len(tracks) == 3 and len({t["track_id"] for t in tracks}) == 3 and all(t["path"] and t["seconds"] == 30 and "mysterious" in t["prompt"] for t in tracks)
    assert len(library.made) == 3
