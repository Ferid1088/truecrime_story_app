"""The hand review of the host's voice (Persian): the same review as the narration's with the host's scenes as blocks. A ticked take is the scene's voice at once
(at the post speed) and the avatar is made again from it; a later run keeps it; nothing here assembles or approves. The voice is a fake: nothing is paid."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_studio import FakeVoice, _renderer, _segments

from app.core.ai_config import ai_config
from app.db.models import HostScene, VoiceBlock, VoiceBlockTake, VoiceBlockWord, VoiceReview
from app.documentary import host_scenes as HS
from app.documentary import storage
from app.documentary.block_review import edits as E
from app.documentary.block_review import host_review as HR
from app.documentary.block_review import service as S


class Voice(FakeVoice):
    def __init__(self):
        super().__init__()
        self.requests, self.dictionaries = [], []

    async def synthesize(self, req):
        self.requests.append(req)
        return await super().synthesize(req)

    async def create_pronunciation_dictionary(self, name: str, rules: list[dict]) -> dict:
        self.dictionaries.append({"name": name, "rules": rules})
        return {"id": f"dict{len(self.dictionaries)}", "version_id": f"v{len(self.dictionaries)}"}


@pytest.fixture
def host(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    row = _segments(db_session, "fa")
    voice = Voice()
    for scene in HS.plan_host_scenes(db_session, row):
        if scene.text:
            asyncio.run(HS.run_scene(db_session, scene, "voice", renderer=_renderer(voice)))
    version = db_session.get(HR.StoryVersion, row.story_version_id)
    voice.row = row
    yield db_session, version, voice
    review = S.get_review(db_session, version, HR.HOST)
    if review is not None:
        for block in S.blocks_of(db_session, review):
            db_session.query(VoiceBlockTake).filter_by(block_pk=block.id).delete()
            db_session.query(VoiceBlockWord).filter_by(block_pk=block.id).delete()
        db_session.query(VoiceBlock).filter_by(review_id=review.id).delete()
        db_session.query(VoiceReview).filter_by(id=review.id).delete()
        db_session.commit()


def scene_of(db, block) -> HostScene:
    return HR.scene_of(db, block)


def test_opening_registers_each_host_scene_with_its_take_without_paying_and_once(host):
    db, version, voice = host
    paid = len(voice.requests)
    review = HR.open_review(db, version)
    blocks = S.blocks_of(db, review)
    assert review.kind == "host" and len(blocks) == 2 and [b.position for b in blocks] == [0, 1]
    assert all(S.active_take(db, b).source == "original" for b in blocks)
    assert blocks[0].voice_text == scene_of(db, blocks[0]).text and HR.open_review(db, version).id == review.id and len(voice.requests) == paid


def test_a_host_review_and_a_narration_review_of_one_version_are_two_reviews(host):
    db, version, _ = host
    review = HR.open_review(db, version)
    assert S.get_review(db, version) is None and S.get_review(db, version, "host").id == review.id


def test_the_host_voice_review_is_only_for_the_languages_set(host, monkeypatch):
    db, version, _ = host
    monkeypatch.setattr(ai_config.voice, "host_review_languages", [])
    with pytest.raises(S.ReviewError, match="not set for fa"):
        HR.open_review(db, version)


def test_a_correction_sent_becomes_the_scenes_voice_with_the_alias_post_speed_and_a_new_avatar_and_the_old_take_stays(host):
    db, version, voice = host
    review = HR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    scene = scene_of(db, block)
    scene.status, scene.provider_asset_id, scene.avatar_video_path = "avatar_ready", "asset-1", "old.mp4"
    db.commit()
    before = (scene.voice_path, scene.voice_sha256)
    S.save_edit(db, review, block.block_id, None, [E.WordEdit(0, "Gutten", "guten")])
    take = asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    db.refresh(scene)
    assert take.take_no == 2 and [(t.take_no, t.active) for t in S.takes_of(db, block)] == [(1, False), (2, True)]
    assert voice.dictionaries[0]["rules"] == [{"string_to_replace": "Gutten", "type": "alias", "alias": "guten"}]
    assert (scene.voice_path, scene.voice_sha256) != before
    meta = json.loads(scene.voice_meta_json)
    assert meta["reviewed"] and meta["post_speed"] == 1.1 and (meta["settings"]["speed"], meta["settings"]["stability"]) == (1.2, 0.2)
    assert (scene.provider_asset_id, scene.avatar_video_path, scene.status) == (None, None, "voice_ready"), "the avatar is made again from the new voice"
    assert json.loads(scene.voice_checks_json)["tts_text"] == "Gutten Abend. Heute geht es um eine Tür." and scene.text.startswith("Gutten"), "the scene's words are the corrected ones"


def test_ticking_the_earlier_take_puts_its_voice_back_and_a_later_run_keeps_the_chosen_take(host):
    db, version, voice = host
    review = HR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    scene = scene_of(db, block)
    original = scene.voice_sha256
    S.save_edit(db, review, block.block_id, None, [E.WordEdit(0, "Gutten", "guten")])
    asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    chosen = scene.voice_sha256
    calls = voice.calls
    asyncio.run(HS.run_scene(db, scene, "voice", renderer=_renderer(voice)))
    assert voice.calls == calls and scene.voice_sha256 == chosen, "a reviewed take is not made again by a later run"
    S.activate(db, review, block.block_id, 1)
    db.refresh(scene)
    assert scene.voice_sha256 == original and json.loads(scene.voice_checks_json)["reviewed_take"] == 1


def test_a_take_is_refused_when_the_host_style_is_not_the_story_voices(host, monkeypatch):
    db, version, voice = host
    review = HR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    S.save_edit(db, review, block.block_id, "یه متن", [])
    take = S.takes_of(db, block)[0]
    record = json.loads(take.request_json)
    record["settings"] = {**record["settings"], "stability": 0.5}
    take.request_json = json.dumps(record)
    db.commit()
    paid = len(voice.requests)
    with pytest.raises(S.ReviewError, match="not the story voice's"):
        asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    assert len(voice.requests) == paid


def test_there_is_no_route_to_assemble_or_approve_the_host_voice(host):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, _ = host
    HR.open_review(db, version)
    client = TestClient(app)
    base = f"/api/documentary/versions/{version.id}/host-voice-review"
    assert client.get(base).json()["approved"] is False and client.get(base).json()["kind"] == "host"
    for path in ("approve", "assemble", "assembled-audio"):
        assert client.request("POST" if path != "assembled-audio" else "GET", f"{base}/{path}").status_code in (404, 405)
    assert client.get(f"{base}/blocks/{S.blocks_of(db, S.get_review(db, version, 'host'))[0].block_id}/takes/1/audio").headers["content-type"] == "audio/mpeg"


def newer_host_text(db, version, voice):
    """The host's words are written again: a newer host segments row with scenes that have a voice."""
    from app.db.models import HostSegments

    old = voice.row
    segments = json.loads(old.segments_json)
    for seg in segments:
        seg["avatar_dialogue"] = (seg["avatar_dialogue"] + " Und noch ein neuer Gedanke.") if seg["avatar_dialogue"] else ""
    row = HostSegments(case_id=old.case_id, story_version_id=old.story_version_id, host_plan_id=old.host_plan_id, language=old.language, version=old.version + 1,
                       segments_json=json.dumps(segments, ensure_ascii=False), status="valid")
    db.add(row)
    db.commit()
    for scene in HS.plan_host_scenes(db, row):
        asyncio.run(HS.run_scene(db, scene, "voice", renderer=_renderer(voice)))
    return row


def test_a_review_of_an_older_host_text_that_nobody_corrected_is_replaced_by_the_review_of_the_newest(host):
    db, version, voice = host
    old = HR.open_review(db, version)
    old_ids = [b.block_id for b in S.blocks_of(db, old)]
    newer_host_text(db, version, voice)
    assert HR.current_review(db, version) is None, "the review of the older text is not shown"
    new = HR.open_review(db, version)
    assert S.get_review(db, version, "host").id == new.id and [b.block_id for b in S.blocks_of(db, new)] != old_ids
    assert db.query(VoiceBlock).filter(VoiceBlock.block_id.in_(old_ids)).count() == 0, "the blocks of the older text are gone"
    assert "Und noch ein neuer Gedanke" in S.blocks_of(db, new)[0].canonical_text


def test_a_review_of_an_older_host_text_with_corrections_is_kept_and_the_new_one_waits_for_the_person(host):
    db, version, voice = host
    review = HR.open_review(db, version)
    S.save_edit(db, review, S.blocks_of(db, review)[0].block_id, None, [E.WordEdit(0, "Gutten", "guten")])
    newer_host_text(db, version, voice)
    with pytest.raises(S.ReviewError, match="has corrections: it is kept"):
        HR.open_review(db, version)
    assert db.query(VoiceReview).filter_by(id=review.id).count() == 1 and HR.current_review(db, version) is None


def test_a_voice_the_person_chose_is_not_rewritten_or_voiced_again_to_fit_the_planned_seconds(host):
    """The time check rewrites a host text (and voices it again) when the voice misses its planned seconds by more than the tolerance; the voice a person ticked in the
    review is the exception: the person's ear decides."""
    db, version, voice = host
    review = HR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    scene = scene_of(db, block)
    S.save_edit(db, review, block.block_id, None, [E.WordEdit(0, "Gutten", "guten")])
    asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    db.refresh(scene)
    scene.planned_duration = scene.voice_seconds * 3                          # far outside the tolerance
    db.commit()
    asked, calls, text_before = [], voice.calls, scene.text

    async def retimer(db_, scene_, seconds):
        asked.append(seconds)

    asyncio.run(HS._voice_within_plan(db, scene, _renderer(voice), retimer))
    assert asked == [] and voice.calls == calls and scene.text == text_before
    meta = json.loads(scene.voice_meta_json)
    meta.pop("reviewed")                                                       # a voice nobody chose is still fitted to its time
    scene.voice_meta_json = json.dumps(meta)
    asyncio.run(HS._voice_within_plan(db, scene, _renderer(voice), retimer))
    assert asked, "a voice nobody reviewed is rewritten to fit"


def test_the_host_tab_lists_only_the_scenes_of_the_newest_host_text_and_a_new_text_has_its_scenes_at_once(host):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, voice = host
    client = TestClient(app)
    listed = lambda: sorted(s["id"] for s in client.get(f"/api/host-scenes?case_id={version.case_id}&language=fa").json())          # noqa: E731
    old_ids = sorted(s.id for s in HR.scenes_of(db, version))
    assert listed() == old_ids
    newer = newer_host_text(db, version, voice)
    new_ids = sorted(s.id for s in db.query(HostScene).filter_by(host_segments_id=newer.id))
    assert new_ids and listed() == new_ids, "the scenes of the older text are not listed"
    everything = client.get(f"/api/host-scenes?case_id={version.case_id}&language=fa&current=false").json()
    assert {s["id"] for s in everything} == set(old_ids) | set(new_ids), "nothing is deleted: the older scenes stay in the database"


def test_the_corrected_words_of_the_ticked_take_are_the_scenes_words_the_host_tab_shows(host):
    db, version, voice = host
    review = HR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    scene = scene_of(db, block)
    before = scene.text
    S.save_edit(db, review, block.block_id, "گفتِ تازه‌ی من، با کالِب.", [])
    asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    db.refresh(scene)
    assert scene.text == "گفت تازه‌ی من، با کالب." != before, "the corrected words, without the vowel marks of the voice"
    assert scene.text_sha256 == HS._sha(scene.text) and json.loads(scene.voice_meta_json)["text_sha256"] == scene.text_sha256
    segments = json.loads(db.get(HS.HostSegments, scene.host_segments_id).segments_json)
    assert [s["avatar_dialogue"] for s in segments if s["segment_id"] == scene.host_segment_id] == [scene.text]
    calls = voice.calls
    asyncio.run(HS.run_scene(db, scene, "voice", renderer=_renderer(voice)))
    assert voice.calls == calls, "a later run keeps the corrected voice: the record follows the new text"
    S.activate(db, review, block.block_id, 1)
    db.refresh(scene)
    assert scene.text == HR.display_text(S.takes_of(db, block)[0].text_sent), "ticking the earlier take puts its words back"


def test_syncing_the_words_keeps_an_avatar_that_is_already_requested_and_asks_for_a_new_direction_only_before_it(host):
    db, version, voice = host
    review = HR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    scene = scene_of(db, block)
    take = S.takes_of(db, block)[0]
    take.text_sent = "کالِب آمد."
    scene.direction_json, scene.direction_sha256 = json.dumps({"x": 1}), "d" * 64
    assert HR.sync_text(db, scene, take) and scene.direction_json is None, "no avatar yet: a direction from the words that changed is asked for again"
    scene.direction_json, scene.direction_sha256, scene.provider_job_id = json.dumps({"x": 1}), "d" * 64, "heygen-1"
    take.text_sent = "باتنِر آمد."
    assert HR.sync_text(db, scene, take) and scene.provider_job_id == "heygen-1" and scene.direction_json is not None, "an avatar in the making is never invalidated by a text sync"
    assert not HR.sync_text(db, scene, take), "the same words again change nothing"
