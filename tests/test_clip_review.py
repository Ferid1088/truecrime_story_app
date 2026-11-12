"""The hand review of the short clips (Persian): the same review as the host's and the opening's with the parts of the clips as blocks. A ticked take is the part's voice
at once (at the clips' speed) and its corrected words the part's words in the clip's script. The voice is a fake: nothing is paid."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_host import _plan_story, _version
from test_studio import FakeVoice, _renderer

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin
from app.db.models import ShortClip, VoiceBlock, VoiceBlockTake, VoiceBlockWord, VoiceReview
from app.documentary import opening_block as OB
from app.documentary.block_review import clip_review as CR
from app.documentary.block_review import service as S
from app.shortform import clip_media as CM

pytestmark = pytest.mark.skipif(not ffmpeg_bin(), reason="ffmpeg is needed")

SCRIPT = {"hook": "یه نفر اومده تو خونه‌م.", "context": "نصفه‌شب. تیپ‌سیتی. یه تماس آشفته.", "detail": "کالب می‌گه در گاراژ بازه. می‌گه یه نفر اشلی رو زده.",
          "reaction": "این فقط روایتِ خودشه. تو اول به چی گوش می‌دی؟", "cta": "ماجرای کامل رو ببین."}


def script_of(key: str) -> dict:
    """The script of a clip: the second clip says the same things in other words (the voice provider keeps what it was asked for once)."""
    return SCRIPT if key == "C1" else {k: v.replace(".", " دوم.", 1) if v.endswith(".") else v.replace("؟", " دوم؟") for k, v in SCRIPT.items()}


class Voice(FakeVoice):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def synthesize(self, req):
        self.requests.append(req)
        return await super().synthesize(req)


@pytest.fixture
def clips(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    case, master, _ = _plan_story(db_session)
    version = _version(db_session, case, master, "fa")
    version.language = "fa"
    for key, angle in (("C1", "question"), ("C2", "evidence")):
        db_session.add(ShortClip(case_id=case.id, language="fa", clip_key=key, kind="native", story_version_id=version.id, status="valid",
                                 script_json=json.dumps({"clip_key": key, "angle": angle, **script_of(key)}, ensure_ascii=False)))
    db_session.commit()
    voice = Voice()
    asyncio.run(CM.voice_clips(db_session, case.id, "fa", _renderer(voice)))
    yield db_session, version, voice
    review = S.get_review(db_session, version, CR.CLIPS)
    if review is not None:
        for block in S.blocks_of(db_session, review):
            db_session.query(VoiceBlockTake).filter_by(block_pk=block.id).delete()
            db_session.query(VoiceBlockWord).filter_by(block_pk=block.id).delete()
        db_session.query(VoiceBlock).filter_by(review_id=review.id).delete()
        db_session.query(VoiceReview).filter_by(id=review.id).delete()
        db_session.commit()


def row(db, version, key):
    return next(r for r in CR.rows_of(db, version) if r.clip_key == key)


def test_every_part_of_every_clip_has_its_own_voice_in_the_opening_hooks_settings(clips):
    db, version, voice = clips
    assert len(voice.requests) == 10                                     # five parts of two clips
    settings = OB.voice_settings_for("fa")
    assert all((r.settings["stability"], r.settings["speed"]) == (settings["stability"], settings["speed"]) for r in voice.requests)
    media = json.loads(row(db, version, "C1").media_json)
    assert list(media["voice"]) == ["hook", "context", "detail", "reaction", "cta"] and media["voice"]["hook"]["post_speed"] == OB.post_speed_for("fa") == 1.2
    assert media["voice_seconds"] == pytest.approx(sum(v["seconds"] for v in media["voice"].values()))


def test_a_saved_voice_is_not_paid_for_again(clips):
    db, version, voice = clips
    paid = len(voice.requests)
    asyncio.run(CM.voice_clips(db, version.case_id, "fa", _renderer(voice)))
    assert len(voice.requests) == paid


def test_opening_registers_each_part_in_the_order_of_the_clips_without_paying_and_once(clips):
    db, version, voice = clips
    paid = len(voice.requests)
    review = CR.open_review(db, version)
    blocks = S.blocks_of(db, review)
    assert review.kind == "clips" and [b.block_id for b in blocks][:6] == ["clip_C1_hook", "clip_C1_context", "clip_C1_detail", "clip_C1_reaction", "clip_C1_cta", "clip_C2_hook"]
    assert len(blocks) == 10 and all(S.active_take(db, b).source == "original" for b in blocks)
    assert CR.open_review(db, version).id == review.id and len(voice.requests) == paid


def test_the_clip_review_is_only_for_the_languages_set(clips, monkeypatch):
    db, version, _ = clips
    monkeypatch.setattr(ai_config.voice, "clip_review_languages", [])
    with pytest.raises(S.ReviewError, match="not set for fa"):
        CR.open_review(db, version)


def test_a_correction_sent_becomes_the_parts_voice_and_words_and_the_other_parts_stay(clips):
    db, version, voice = clips
    before = json.loads(row(db, version, "C1").media_json)
    review = CR.open_review(db, version)
    block = next(b for b in S.blocks_of(db, review) if b.block_id == "clip_C1_hook")
    S.save_edit(db, review, block.block_id, "یه نفر اومده تو خونه‌ی من.", [])
    take = asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    db.refresh(row(db, version, "C1"))
    c1 = row(db, version, "C1")
    media, script = json.loads(c1.media_json), json.loads(c1.script_json)
    assert take.take_no == 2 and [(t.take_no, t.active) for t in S.takes_of(db, block)] == [(1, False), (2, True)]
    assert script["hook"] == "یه نفر اومده تو خونه‌ی من." and script["context"] == SCRIPT["context"]
    assert media["voice"]["hook"]["reviewed"] and media["voice"]["hook"]["path"] != before["voice"]["hook"]["path"]
    assert media["voice"]["context"]["path"] == before["voice"]["context"]["path"], "the other parts keep their voice"
    assert json.loads(row(db, version, "C2").script_json)["hook"] == script_of("C2")["hook"], "the other clip is not touched"
    assert (voice.requests[-1].settings["stability"], voice.requests[-1].settings["speed"]) == (OB.voice_settings_for("fa")["stability"], OB.voice_settings_for("fa")["speed"])


def test_ticking_the_earlier_take_puts_its_voice_and_words_back(clips):
    db, version, voice = clips
    review = CR.open_review(db, version)
    block = next(b for b in S.blocks_of(db, review) if b.block_id == "clip_C2_cta")
    original = json.loads(row(db, version, "C2").media_json)["voice"]["cta"]["path"]
    S.save_edit(db, review, block.block_id, "یه دعوت تازه.", [])
    asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    c2 = row(db, version, "C2")
    db.refresh(c2)
    assert json.loads(c2.script_json)["cta"] == "یه دعوت تازه." and json.loads(c2.media_json)["voice"]["cta"]["path"] != original
    S.activate(db, review, block.block_id, 1)
    db.refresh(c2)
    assert json.loads(c2.script_json)["cta"] == script_of("C2")["cta"] and json.loads(c2.media_json)["voice"]["cta"]["path"] == original


def test_a_take_is_refused_when_its_settings_are_not_the_clips(clips):
    db, version, voice = clips
    review = CR.open_review(db, version)
    block = S.blocks_of(db, review)[0]
    S.save_edit(db, review, block.block_id, "یه متن", [])
    take = S.takes_of(db, block)[0]
    record = json.loads(take.request_json)
    record["settings"] = {**record["settings"], "stability": 0.9}
    take.request_json = json.dumps(record)
    db.commit()
    paid = len(voice.requests)
    with pytest.raises(S.ReviewError, match="not the clips'"):
        asyncio.run(S.send_block(db, review, block.block_id, provider=voice))
    assert len(voice.requests) == paid


def test_the_routes_review_the_clips_and_there_is_no_route_to_assemble_or_approve(clips):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, _ = clips
    client = TestClient(app)
    base = f"/api/documentary/versions/{version.id}/clip-voice-review"
    assert client.get(base).json() == {"exists": False, "kind": "clips", "version_id": version.id, "can_open": True}
    opened = client.post(f"{base}/open").json()
    assert opened["kind"] == "clips" and len(opened["blocks"]) == 10 and opened["blocks"][0]["takes"][0]["voice"]["post_speed"] == 1.2
    for path in ("approve", "assemble", "assembled-audio"):
        assert client.request("POST" if path != "assembled-audio" else "GET", f"{base}/{path}").status_code in (404, 405)
    assert client.get(f"{base}/blocks/clip_C1_hook/takes/1/audio").headers["content-type"] == "audio/mpeg"


def test_the_owner_can_say_he_is_finished_and_any_change_reopens_the_review(clips):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, _ = clips
    client = TestClient(app)
    base = f"/api/documentary/versions/{version.id}/clip-voice-review"
    client.post(f"{base}/open")
    assert client.get(base).json()["approved"] is False
    done = client.post(f"{base}/finish").json()
    assert done["approved"] is True and done["status"] == "approved"
    assert client.get(base).json()["approved"] is True
    take = client.put(f"{base}/blocks/clip_C1_hook", json={"text": "یه نفر اومده تو خونه‌م.", "words": []})
    assert take.status_code == 200
    assert client.get(base).json()["approved"] is False
    client.post(f"{base}/finish")
    assert client.post(f"{base}/reopen").json()["approved"] is False


def test_finishing_is_refused_while_a_corrected_block_is_not_voiced(clips):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, _ = clips
    client = TestClient(app)
    base = f"/api/documentary/versions/{version.id}/clip-voice-review"
    client.post(f"{base}/open")
    client.put(f"{base}/blocks/clip_C1_hook", json={"text": "یه نفر دیگه اومده تو خونه‌م.", "words": []})
    refused = client.post(f"{base}/finish")
    assert refused.status_code == 409 and "not voiced yet" in refused.json()["detail"]
    assert client.get(base).json()["approved"] is False


def test_the_host_and_opening_reviews_have_no_finish_route(clips):
    from fastapi.testclient import TestClient

    from app.main import app

    db, version, _ = clips
    client = TestClient(app)
    for base in ("host-voice-review", "opening-voice-review"):
        assert client.post(f"/api/documentary/versions/{version.id}/{base}/finish").status_code in (404, 405)
