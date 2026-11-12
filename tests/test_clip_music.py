"""The music beds of the short clips: each short gets its own bed from the database; new beds are made until the library holds the configured number, after that
one is taken at random; the same idea keeps its bed in every language. Nothing is paid: the generator is a fake."""

from __future__ import annotations

import json
import random
from uuid import uuid4

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, ShortClip, ShortClipMusicTrack
from app.documentary import storage
from app.shortform import clip_media as CM
from app.shortform import clip_music as MU
from app.shortform import clip_pictures as CP


class FakeLibrary:
    """Stands in for the paid sound generator: writes a small file and counts the calls."""

    def __init__(self, folder):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.made = []

    async def _generate(self, prompt, seconds, loop, path):
        self.made.append(prompt)
        path.write_bytes(b"RIFFfake")
        return 123


@pytest.fixture
def setup(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(MU, "book_opening", lambda *a, **k: None)
    db_session.query(ShortClipMusicTrack).delete()
    case = Case(canonical_title="Music", slug=f"music-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    for key in ("C1", "C2", "C3"):
        for language in ("en", "fa"):
            db_session.add(ShortClip(case_id=case.id, language=language, clip_key=key, kind="master" if language == "en" else "native", status="valid", script_json="{}"))
    db_session.commit()
    yield db_session, case, FakeLibrary(tmp_path / "music")
    db_session.query(ShortClip).filter_by(case_id=case.id).delete()
    db_session.query(ShortClipMusicTrack).delete()
    db_session.query(Case).filter_by(id=case.id).delete()
    db_session.commit()


def row(db, case, language, key):
    return next(r for r in db.query(ShortClip).filter_by(case_id=case.id, language=language, clip_key=key))


def give(db, clip, track):
    clip.media_json = json.dumps({"music": {"track_id": track.id, "code": track.track_code}})
    db.commit()


def test_every_variation_is_different_and_the_walk_reaches_enough_of_them_for_the_library():
    variations = ai_config.short_clips_settings().video.music.variations
    target = ai_config.short_clips_settings().video.music.library_target
    keys = {MU.variation_key(MU.variation_of(i, variations)) for i in range(target)}
    assert len(keys) == target
    for i in range(20):
        first, second = MU.variation_of(i, variations), MU.variation_of(i + 1, variations)
        assert sum(first[name] != second[name] for name in variations) >= 2


def test_each_short_gets_a_new_bed_with_a_different_sound_while_the_library_is_below_its_target(setup):
    db, case, library = setup
    music = MU.ClipMusic(db, library)
    first = asyncio_run(music.choose(case.id, "C1"))
    give(db, row(db, case, "en", "C1"), first)
    second = asyncio_run(music.choose(case.id, "C2"))
    assert first.id != second.id and first.variation_key != second.variation_key and first.prompt != second.prompt
    assert len(library.made) == 2 and storage.resolve(first.file_path).exists()
    assert db.query(ShortClipMusicTrack).count() == 2 and first.characters_paid == 123


def test_the_same_idea_in_another_language_keeps_its_bed_and_pays_nothing(setup):
    db, case, library = setup
    music = MU.ClipMusic(db, library)
    first = asyncio_run(music.choose(case.id, "C1"))
    give(db, row(db, case, "en", "C1"), first)
    again = asyncio_run(music.choose(case.id, "C1"))
    assert again.id == first.id and len(library.made) == 1


def test_at_the_target_no_new_bed_is_made_and_a_random_one_is_taken_that_other_ideas_do_not_use(setup, monkeypatch):
    db, case, library = setup
    monkeypatch.setattr(ai_config.short_clips_settings().video.music, "library_target", 3)
    music = MU.ClipMusic(db, library, random.Random(7))
    beds = []
    for key in ("C1", "C2", "C3"):
        bed = asyncio_run(music.choose(case.id, key))
        give(db, row(db, case, "en", key), bed)
        beds.append(bed)
    assert len(library.made) == 3 and len({b.id for b in beds}) == 3
    # a fourth idea: the library is full, so nothing is made; all three are used by other ideas, so any of them may come
    extra = ShortClip(case_id=case.id, language="en", clip_key="C4", kind="master", status="valid", script_json="{}")
    db.add(extra)
    db.commit()
    taken = asyncio_run(music.choose(case.id, "C4"))
    assert len(library.made) == 3 and taken.id in {b.id for b in beds}
    # drop one idea's bed: it is the only one no other idea uses, so it is the one chosen
    other = row(db, case, "en", "C3")
    other.media_json = "{}"
    db.commit()
    assert asyncio_run(music.choose(case.id, "C4")).id == beds[2].id


def test_a_full_library_is_taken_at_random_so_different_draws_can_differ(setup, monkeypatch):
    db, case, library = setup
    monkeypatch.setattr(ai_config.short_clips_settings().video.music, "library_target", 4)
    music = MU.ClipMusic(db, library, random.Random(1))
    for _ in range(4):
        asyncio_run(music._make(case.id))
    picks = {asyncio_run(music.choose(case.id, "C9")).id for _ in range(30)}
    assert len(picks) > 1 and len(library.made) == 4


def test_the_clip_keeps_which_bed_it_used_and_the_bed_counts_its_uses(setup):
    db, case, library = setup
    clip = row(db, case, "en", "C1")
    first = asyncio_run(CM.ensure_music(db, clip, library))
    again = asyncio_run(CM.ensure_music(db, clip, library))
    track = db.get(ShortClipMusicTrack, first["track_id"])
    assert first["track_id"] == again["track_id"] and json.loads(clip.media_json)["music"]["track_id"] == track.id and track.use_count == 1


def test_a_clip_prefers_pictures_no_other_clip_shows_and_otherwise_the_least_shown():
    found = [{"code": c, "shared": shared, "order": i} for i, (c, shared) in enumerate([("A", 3), ("B", 2), ("C", 1), ("D", 0)])]

    def codes(used):
        return [f["code"] for f in CP.ranked(found, used)]

    assert codes({}) == ["A", "B", "C", "D"]
    assert codes({"A": 1, "B": 1}) == ["C", "D", "A", "B"]
    assert codes({"A": 2, "B": 1, "C": 1, "D": 1}) == ["B", "C", "D", "A"]


def test_the_pictures_of_the_languages_other_clips_are_counted_and_the_clips_own_are_not(setup):
    db, case, _ = setup
    for key, codes in (("C1", ["P1", "P2"]), ("C2", ["P2", "P3"]), ("C3", ["P2"])):
        clip = row(db, case, "en", key)
        clip.media_json = json.dumps({"video": {"pictures": [{"code": c, "what": ""} for c in codes]}})
    other_language = row(db, case, "fa", "C1")
    other_language.media_json = json.dumps({"video": {"pictures": [{"code": "P9", "what": ""}]}})
    db.commit()
    assert dict(CM.pictures_of_other_ideas(db, row(db, case, "en", "C1"))) == {"P2": 2, "P3": 1}
    assert dict(CM.pictures_of_other_ideas(db, row(db, case, "en", "C3"))) == {"P1": 1, "P2": 2, "P3": 1}


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
