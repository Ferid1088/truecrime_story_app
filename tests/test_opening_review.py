"""The hand review of the film's opening block (Persian): the same review as the host's with the hook and the context text as blocks. A ticked take is the part's voice
at once (at the opening's speed), its corrected words the part's words, and the block's audio is made again from the parts. The voice is a fake: nothing is paid."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_host import _plan_story, _version
from test_opening_media import FakeLibrary
from test_studio import FakeVoice, _renderer

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin
from app.db.models import FilmOpening, VoiceBlock, VoiceBlockTake, VoiceBlockWord, VoiceReview
from app.documentary import opening_block as OB
from app.documentary import opening_media as OM
from app.documentary import storage
from app.documentary.block_review import opening_review as OR
from app.documentary.block_review import service as S
from app.documentary.production import opening_timeline as OT

pytestmark = pytest.mark.skipif(not ffmpeg_bin(), reason="ffmpeg is needed")

HOOK = "یه نفر اومده تو خونه‌ی من و زنمو کشته."
CONTEXT = "یه زن تو تختش دو بار تیر خورد. شوهرش می‌گه یه غریبه اومده تو. پس چرا در گاراژ باز مونده؟"


class Voice(FakeVoice):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def synthesize(self, req):
        self.requests.append(req)
        return await super().synthesize(req)


@pytest.fixture
def opening(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    case, master, _ = _plan_story(db_session)
    version = _version(db_session, case, master, "fa")
    version.language = "fa"
    row = FilmOpening(case_id=case.id, language="fa", kind="native", story_version_id=version.id, status="valid",
                      opening_json=json.dumps({"hook": HOOK, "context": CONTEXT, "music": {"mood": "tension", "prompt": "cello"}}))
    db_session.add(row)
    db_session.commit()
    voice = Voice()
    asyncio.run(OM.make_media(db_session, case.id, row, row, _renderer(voice), FakeLibrary(tmp_path)))
    yield db_session, version, row, voice
    review = S.get_review(db_session, version, OR.OPENING)
    if review is not None:
        for block in S.blocks_of(db_session, review):
            db_session.query(VoiceBlockTake).filter_by(block_pk=block.id).delete()
            db_session.query(VoiceBlockWord).filter_by(block_pk=block.id).delete()
        db_session.query(VoiceBlock).filter_by(review_id=review.id).delete()
        db_session.query(VoiceReview).filter_by(id=review.id).delete()
        db_session.commit()


def test_opening_registers_the_hook_and_the_context_with_their_takes_without_paying_and_once(opening):
    db, version, row, voice = opening
    paid = len(voice.requests)
    review = OR.open_review(db, version)
    blocks = S.blocks_of(db, review)
    assert review.kind == "opening" and [b.block_id for b in blocks] == ["opening_hook", "opening_context"]
    assert [b.voice_text for b in blocks] == [HOOK, CONTEXT] and all(S.active_take(db, b).source == "original" for b in blocks)
    assert OR.open_review(db, version).id == review.id and len(voice.requests) == paid


def test_the_opening_review_is_only_for_the_languages_set(opening, monkeypatch):
    db, version, _, _ = opening
    monkeypatch.setattr(ai_config.voice, "opening_review_languages", [])
    with pytest.raises(S.ReviewError, match="not set for fa"):
        OR.open_review(db, version)


def test_a_correction_sent_becomes_the_parts_voice_and_words_and_the_audio_is_made_again(opening):
    db, version, row, voice = opening
    before = json.loads(row.media_json)
    assert OT.opening_item(db, row.case_id, "fa")["seconds"] == before["audio"]["total"]
    review = OR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    S.save_edit(db, review, block.block_id, "یه نفر اومده تو خونه‌م و زنمو با گلوله کشته.", [])
    take = asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    db.refresh(row)
    media, opening_json = json.loads(row.media_json), json.loads(row.opening_json)
    assert take.take_no == 2 and [(t.take_no, t.active) for t in S.takes_of(db, block)] == [(1, False), (2, True)]
    assert opening_json["hook"] == "یه نفر اومده تو خونه‌م و زنمو با گلوله کشته." and opening_json["context"] == CONTEXT
    assert media["hook"]["reviewed"] and media["hook"]["path"] != before["hook"]["path"] and media["audio"]["sha256"] != before["audio"]["sha256"]
    assert media["hook"]["post_speed"] == OB.opening_post_speed("fa") == 1.3
    assert (voice.requests[-1].settings["stability"], voice.requests[-1].settings["speed"]) == (OB.voice_settings_for("fa")["stability"], OB.voice_settings_for("fa")["speed"])
    assert OT.opening_item(db, row.case_id, "fa")["text"]["hook"] == opening_json["hook"], "the film takes the corrected block"


def test_ticking_the_earlier_take_puts_its_voice_and_words_back(opening):
    db, version, row, voice = opening
    review = OR.open_review(db, version)
    block = S.blocks_of(db, review)[1]
    original = json.loads(row.media_json)["audio"]["sha256"]
    S.save_edit(db, review, block.block_id, "یه متن تازه. با دو جمله.", [])
    asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    db.refresh(row)
    assert json.loads(row.opening_json)["context"] == "یه متن تازه. با دو جمله." and json.loads(row.media_json)["audio"]["sha256"] != original
    S.activate(db, review, block.block_id, 1)
    db.refresh(row)
    assert json.loads(row.opening_json)["context"] == CONTEXT and json.loads(row.media_json)["audio"]["sha256"] == original
    assert storage.resolve(json.loads(row.media_json)["audio"]["path"]).exists()


def test_a_take_is_refused_when_its_settings_are_not_the_openings(opening):
    db, version, _, voice = opening
    review = OR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    S.save_edit(db, review, block.block_id, "یه متن", [])
    take = S.takes_of(db, block)[0]
    record = json.loads(take.request_json)
    record["settings"] = {**record["settings"], "stability": 0.9}
    take.request_json = json.dumps(record)
    db.commit()
    paid = len(voice.requests)
    with pytest.raises(S.ReviewError, match="not the opening's"):
        asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    assert len(voice.requests) == paid


def test_the_routes_review_the_opening_and_there_is_no_route_to_assemble_the_narration(opening):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, _, _ = opening
    client = TestClient(app)
    base = f"/api/documentary/versions/{version.id}/opening-voice-review"
    assert client.get(base).json() == {"exists": False, "kind": "opening", "version_id": version.id, "can_open": True}
    opened = client.post(f"{base}/open").json()
    assert opened["kind"] == "opening" and [b["block_id"] for b in opened["blocks"]] == ["opening_hook", "opening_context"]
    assert opened["blocks"][0]["takes"][0]["voice"]["post_speed"] == 1.3, "the review shows the opening's speed, not the narration's"
    for path in ("approve", "assemble", "assembled-audio"):
        assert client.request("POST" if path != "assembled-audio" else "GET", f"{base}/{path}").status_code in (404, 405)
    assert client.get(f"{base}/blocks/opening_hook/takes/1/audio").headers["content-type"] == "audio/mpeg"


def test_a_review_of_an_older_opening_that_nobody_corrected_is_replaced(opening):
    db, version, row, voice = opening
    old = OR.open_review(db, version)
    assert OR.current_review(db, version).id == old.id
    newer = FilmOpening(case_id=row.case_id, language="fa", kind="native", story_version_id=version.id, status="valid", opening_json=json.dumps({"hook": HOOK + " دوباره.", "context": CONTEXT}))
    db.add(newer)
    db.commit()
    assert OR.current_review(db, version) is None, "the newest opening has no voice yet: the old review is not shown"


def test_the_owner_approves_the_opening_and_any_change_reopens_the_review(opening):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, _, _ = opening
    client = TestClient(app)
    base = f"/api/documentary/versions/{version.id}/opening-voice-review"
    assert client.post(f"{base}/finish").status_code == 409                       # nothing to approve before the review is open
    client.post(f"{base}/open")
    opened = client.get(base).json()
    assert opened["approved"] is False and opened["status"] == "open"
    done = client.post(f"{base}/finish").json()
    assert done["approved"] is True and done["status"] == "approved" and client.get(base).json()["approved"] is True
    assert client.put(f"{base}/blocks/opening_hook", json={"text": "یه نفر اومده تو خونه‌م و زنمو کشته.", "words": []}).status_code == 200
    assert client.get(base).json()["approved"] is False                           # a change takes the approval away
    assert client.post(f"{base}/finish").status_code == 409                       # the corrected block is not voiced yet: send it first
    assert client.post(f"{base}/reopen").json()["approved"] is False


def test_approving_the_opening_is_refused_while_a_corrected_part_is_not_voiced(opening):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, _, _ = opening
    client = TestClient(app)
    base = f"/api/documentary/versions/{version.id}/opening-voice-review"
    client.post(f"{base}/open")
    client.put(f"{base}/blocks/opening_context", json={"text": "متن اصلاح‌شده.", "words": []})
    refused = client.post(f"{base}/finish")
    assert refused.status_code == 409 and "not voiced yet" in refused.json()["detail"] and client.get(base).json()["approved"] is False
