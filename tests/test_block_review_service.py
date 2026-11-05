"""The hand review of a voiced narration: open it from the narration, correct a block (yellow, its words red), voice it again with the finglish as an alias,
tick takes, assemble from the ticked takes, approve. The voice is a fake: nothing is paid."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from test_voice_render import FakeTTS, _block

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    StoryVersion,
    VoiceBlock,
    VoiceBlockTake,
    VoiceBlockWord,
    VoiceReview,
)
from app.documentary.block_review import edits as E
from app.documentary.block_review import service as S
from app.documentary.voice_render import VoiceRenderer

TEXTS = ["او آمد و نشست.", "دورِ تخت نبود.", "بعد رفت."]


class Voice(FakeTTS):
    """A fake voice that also makes pronunciation dictionaries."""

    def __init__(self):
        super().__init__()
        self.dictionaries: list[dict] = []

    async def create_pronunciation_dictionary(self, name: str, rules: list[dict]) -> dict:
        self.dictionaries.append({"name": name, "rules": rules})
        return {"id": f"dict{len(self.dictionaries)}", "version_id": f"v{len(self.dictionaries)}"}


@pytest.fixture
def narration(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    case = Case(canonical_title="Scratch: block review", slug=f"scratch-br-{uuid4().hex[:8]}", status="new", language="fa", origin="scratch")
    db_session.add(case)
    db_session.flush()
    version = StoryVersion(case_id=case.id, version=1, kind="spoken", language="fa", story_text=" ".join(TEXTS), narrative_angle="a", status="ready")
    db_session.add(version)
    db_session.commit()
    plan = {"language": "fa", "blocks": [{**_block(f"FA_B0{i}_g01_01", f"B0{i}", t), "pause_after_ms": 300, "pause_after_kind": "paragraph"} for i, t in enumerate(TEXTS, 1)]}
    monkeypatch.setattr(S, "performance_for_version", lambda db, v: plan)
    monkeypatch.setattr(ai_config.voice, "review_languages", ["fa"])
    voice = Voice()
    asyncio.run(VoiceRenderer(provider=voice, use_asr=False, use_listener=False).render(plan, case_id=case.id, story_version_id=version.id))
    yield db_session, case, version, voice
    review = S.get_review(db_session, version)
    if review is not None:
        for block in S.blocks_of(db_session, review):
            db_session.query(VoiceBlockTake).filter_by(block_pk=block.id).delete()
            db_session.query(VoiceBlockWord).filter_by(block_pk=block.id).delete()
        db_session.query(VoiceBlock).filter_by(review_id=review.id).delete()
        db_session.query(VoiceReview).filter_by(id=review.id).delete()
    db_session.query(StoryVersion).filter_by(case_id=case.id).delete()
    db_session.query(Case).filter_by(id=case.id).delete()
    db_session.commit()


def test_opening_registers_every_block_and_its_take_without_paying_and_once(narration):
    db, case, version, voice = narration
    before = len(voice.requests)
    review = S.open_review(db, version)
    assert [b.block_id for b in S.blocks_of(db, review)] == ["FA_B01_g01_01", "FA_B02_g01_01", "FA_B03_g01_01"]
    assert all(S.active_take(db, b).source == "original" and S.active_take(db, b).take_no == 1 for b in S.blocks_of(db, review))
    assert S.open_review(db, version).id == review.id and len(voice.requests) == before
    assert S.blocks_of(db, review)[1].voice_text == TEXTS[1] and not any(b.edited for b in S.blocks_of(db, review))


def test_a_correction_turns_the_block_yellow_and_its_words_red_for_good_and_waits_as_a_draft(narration):
    db, _, version, voice = narration
    review = S.open_review(db, version)
    sent = len(voice.requests)
    block = S.save_edit(db, review, "FA_B02_g01_01", None, [E.WordEdit(0, "دُورِ", "dore")])
    assert block.edited and block.draft_text == "دُورِ تخت نبود." and block.voice_text == TEXTS[1]
    words = S.words_of(db, block)
    assert (words[0].was, words[0].surface, words[0].finglish, words[0].pending) == ("دورِ", "دُورِ", "dore", True)
    assert S.needs_sending(db, block) and len(voice.requests) == sent, "nothing is voiced by a correction alone"


def test_sending_a_block_voices_it_with_the_finglish_as_an_alias_keeps_the_old_take_ticked_off_and_marks_stay(narration):
    db, _, version, voice = narration
    review = S.open_review(db, version)
    S.save_edit(db, review, "FA_B02_g01_01", None, [E.WordEdit(0, "دُورِ", "dore")])
    take = asyncio.run(S.send_block(db, review, "FA_B02_g01_01", provider=voice))
    block = S.block_of(db, review, "FA_B02_g01_01")
    assert voice.dictionaries == [{"name": f"review-{version.id}-FA_B02_g01_01-t2", "rules": [{"string_to_replace": "دُورِ", "type": "alias", "alias": "dore"}]}]
    sent = voice.requests[-1]
    assert sent.text == "دُورِ تخت نبود." and sent.pronunciation_dictionaries == [{"pronunciation_dictionary_id": "dict1", "version_id": "v1"}]
    assert sent.seed == json.loads(S.takes_of(db, block)[0].request_json)["seed"] + 1 and sent.settings == json.loads(S.takes_of(db, block)[0].request_json)["settings"]
    assert [(t.take_no, t.active, t.source) for t in S.takes_of(db, block)] == [(1, False, "original"), (2, True, "correction")] and take.take_no == 2
    assert block.voice_text == "دُورِ تخت نبود." and block.draft_text is None and block.edited
    assert S.words_of(db, block)[0].pending is False and S.words_of(db, block)[0].surface == "دُورِ", "the word stays red"
    assert not S.needs_sending(db, block)
    with pytest.raises(S.ReviewError, match="no correction to send"):
        asyncio.run(S.send_block(db, review, "FA_B02_g01_01", provider=voice))


def test_a_person_ticks_any_take_and_the_assembly_uses_what_is_ticked_without_paying(narration):
    db, _, version, voice = narration
    review = S.open_review(db, version)
    S.save_edit(db, review, "FA_B02_g01_01", "دورِ تخت نبود، نه.", [])
    asyncio.run(S.send_block(db, review, "FA_B02_g01_01", provider=voice))
    paid = len(voice.requests)
    first = asyncio.run(S.assemble(db, review))
    assert S.active_takes(db, review) == {"FA_B01_g01_01": 1, "FA_B02_g01_01": 2, "FA_B03_g01_01": 1} and first["blocks"] == 3
    assert (S.reviewed_folder(version) / "narration.mp3").exists() and len(voice.requests) == paid, "assembling pays nothing"
    S.activate(db, review, "FA_B02_g01_01", 1)
    assert not S.assembled_is_current(db, review) and S.block_of(db, review, "FA_B02_g01_01").voice_text == TEXTS[1]
    again = asyncio.run(S.assemble(db, review))
    assert again["duration_seconds"] < first["duration_seconds"] and S.assembled_is_current(db, review) and len(voice.requests) == paid
    S.activate(db, review, "FA_B02_g01_01", 2)
    assert S.active_take(db, S.block_of(db, review, "FA_B02_g01_01")).take_no == 2, "the person may tick again and again"
    with pytest.raises(S.ReviewError, match="no take 9"):
        S.activate(db, review, "FA_B02_g01_01", 9)


def test_approving_makes_the_assembled_narration_the_films_and_keeps_the_old_one_and_needs_a_current_assembly(narration):
    db, _, version, voice = narration
    review = S.open_review(db, version)
    S.save_edit(db, review, "FA_B03_g01_01", "بعد رفت و آمد.", [])
    asyncio.run(S.send_block(db, review, "FA_B03_g01_01", provider=voice))
    with pytest.raises(S.ReviewError, match="assemble the narration again"):
        S.approve(db, review)
    asyncio.run(S.assemble(db, review))
    main = S.narration_folder(version)
    old_sha = S.sha256_of(main / "narration.mp3")
    state = S.approve(db, review)
    assert review.status == "approved" and state["approved"] is True and state["narration_sha256"] == S.sha256_of(main / "narration.mp3") != old_sha
    kept = list(main.glob("before_review_*/narration.mp3"))
    assert len(kept) == 1 and S.sha256_of(kept[0]) == old_sha
    manifest = json.loads((main / "manifest.json").read_text(encoding="utf-8"))
    assert "/reviewed/" not in json.dumps(manifest["files"])
