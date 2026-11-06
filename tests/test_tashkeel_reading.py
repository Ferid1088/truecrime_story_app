"""The reading text of an Arabic narration (Tashkeel): the editor vowelises every word of the block in its sentence, the words it flags are vowelised
again blind by another model, code compares the two and an arbiter settles the differences; the letters of a word never change; what the voice is given
is the block's text with the vowelised words. The agents are fakes."""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from app.core.ai_config import ai_config
from app.documentary.reading import ask as ASK
from app.documentary.reading import stage as RS
from app.documentary.reading import tashkeel as TK
from app.documentary.reading.ask import ReadingFailed
from app.documentary.reading.block import BlockInput

TEXT = "كتب الرجل رسالة، ثم رحل [calm] بعد ثلاث ساعات."


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


def agent(*answers):
    seen = []

    async def run(gen, body):
        seen.append(json.loads(body))
        return answers[min(len(seen), len(answers)) - 1], None

    run.seen = seen
    return run


def never():
    async def run(gen, body):
        raise AssertionError("no model may be called")
    return run


def item(text: str = TEXT, block_id: str = "AR_01") -> BlockInput:
    return BlockInput(block_id, "B01", text, english="He wrote a letter, then left after three hours.")


GOOD = ["كَتَبَ", "الرَّجُلُ", "رِسَالَةً", "ثُمَّ", "رَحَلَ", "بَعْدَ", "ثَلَاثِ", "سَاعَاتٍ"]


def written(marks=GOOD, flagged=(), names=()):
    return {"words": [{"n": n, "text": t, "confidence": "low" if n in flagged else "high", "ambiguous": n in flagged, "name": n in names, "why": ""}
                      for n, t in enumerate(marks)]}


def run_stage(*items, writer, checker=None, arbiter=None):
    stage = TK.TashkeelStage(None, 1, list(items), writer, checker or never(), arbiter or never())
    return asyncio.run(stage.run()), stage


def test_the_marks_of_a_letter_are_compared_in_one_order():
    assert TK.canonical("الرَّجُلُ") == TK.canonical("الرَّجُلُ".replace("َّ", "ّ" + "َ"))
    assert TK.bare("رَحَلَ") == "رحل"


def test_every_word_is_vowelised_and_the_letters_are_the_texts():
    result, _ = run_stage(item(), writer=agent(written()))
    assert [r.text for r in result.readings["AR_01"]] == GOOD
    assert [TK.bare(r.text) for r in result.readings["AR_01"]] == [w.plain for w in item().words]
    assert result.open_doubts == 0 and not result.doubts


def test_an_answer_that_changes_a_letter_is_refused_and_asked_again_with_the_problem():
    wrong = written(["كَتَبَ", "الرَّجُلُ", "رِسَالَهً", *GOOD[3:]])            # ة became ه
    writer = agent(wrong, written())
    result, _ = run_stage(item(), writer=writer)
    assert [r.text for r in result.readings["AR_01"]] == GOOD
    assert "does not have the letters" in " ".join(writer.seen[1]["problems"])


def test_an_answer_that_stays_wrong_stops_the_stage():
    with pytest.raises(ReadingFailed, match="stays wrong"):
        run_stage(item(), writer=agent(written(GOOD[:-1])))


def test_flagged_words_are_vowelised_again_blind_and_agreement_needs_no_arbiter():
    checker = agent({"words": [{"n": 0, "text": "كَتَبَ"}]})
    result, stage = run_stage(item(), writer=agent(written(flagged={0})), checker=checker)
    assert [w["n"] for w in checker.seen[0]["words"]] == [0] and "كَتَبَ" not in json.dumps(checker.seen[0], ensure_ascii=False), \
        "the checker is not shown the editor's answer"
    assert result.readings["AR_01"][0].text == "كَتَبَ" and stage.stats["disagreements"] == 0


def test_a_difference_goes_to_the_arbiter_who_may_choose_the_independent_reading():
    checker = agent({"words": [{"n": 0, "text": "كُتِبَ"}]})
    arbiter = agent({"words": [{"n": 0, "choice": "b", "text": "", "why": "the letter was written"}]})
    result, stage = run_stage(item(), writer=agent(written(flagged={0})), checker=checker, arbiter=arbiter)
    assert result.readings["AR_01"][0].text == "كُتِبَ" and stage.stats["arbitrated"] == 1
    assert arbiter.seen[0]["words"][0]["a"] == "كَتَبَ" and arbiter.seen[0]["words"][0]["b"] == "كُتِبَ"


def test_a_word_nobody_can_settle_is_left_as_written_and_reported():
    checker = agent({"words": [{"n": 0, "text": "كُتِبَ"}]})
    arbiter = agent({"words": [{"n": 0, "choice": "unsure", "text": "", "why": "the sentence does not decide"}]})
    result, _ = run_stage(item(), writer=agent(written(flagged={0})), checker=checker, arbiter=arbiter)
    assert result.doubts == {"AR_01": {0}} and "left as written" in result.notes["AR_01"][0]
    voicing = TK.TashkeelVoicing(result)
    assert voicing.text("AR_01", TEXT).startswith("كتب "), "a doubted word reaches the voice as it is written"
    assert "الرَّجُلُ" in voicing.text("AR_01", TEXT)


def test_with_all_words_checked_the_checker_reads_every_word(monkeypatch):
    monkeypatch.setattr(ai_config.reading, "blind_check", "all")
    checker = agent({"words": [{"n": n, "text": t} for n, t in enumerate(GOOD)]})
    run_stage(item(), writer=agent(written()), checker=checker)
    assert len(checker.seen[0]["words"]) == len(GOOD)


def test_a_block_without_a_doubtful_word_calls_no_checker():
    run_stage(item(), writer=agent(written()), checker=never(), arbiter=never())


def test_the_voice_is_given_the_text_with_the_vowelised_words_its_punctuation_and_tags_unchanged():
    result, _ = run_stage(item(), writer=agent(written()))
    given = TK.TashkeelVoicing(result).text("AR_01", TEXT)
    assert given == "كَتَبَ الرَّجُلُ رِسَالَةً، ثُمَّ رَحَلَ [calm] بَعْدَ ثَلَاثِ سَاعَاتٍ."


def test_flagged_mode_vowelises_only_the_flagged_words():
    result, _ = run_stage(item(), writer=agent(written(flagged={0}, names={1})), checker=agent({"words": [{"n": 0, "text": "كَتَبَ"}, {"n": 1, "text": "الرَّجُلُ"}]}))
    given = TK.TashkeelVoicing(result, "flagged").text("AR_01", TEXT)
    assert given == "كَتَبَ الرَّجُلُ رسالة، ثم رحل [calm] بعد ثلاث ساعات."


def test_a_block_whose_words_are_not_the_words_of_its_reading_is_left_as_written():
    result, _ = run_stage(item(), writer=agent(written()))
    assert TK.TashkeelVoicing(result).text("AR_01", "كلمة واحدة فقط") == "كلمة واحدة فقط"


def test_a_number_still_in_the_text_is_not_vowelised_and_the_rest_is():
    text = "رحل بعد 21 ساعة"
    reading = {"words": [{"n": 0, "text": "رَحَلَ"}, {"n": 3, "text": "سَاعَةً"}, {"n": 1, "text": "بَعْدَ"}]}
    result, _ = run_stage(item(text), writer=agent(reading))
    assert TK.TashkeelVoicing(result).text("AR_01", text) == "رَحَلَ بَعْدَ 21 سَاعَةً"


def test_a_rerun_with_the_runs_own_cache_pays_nothing_again(tmp_path):
    cache = tmp_path / "ar_run.json"
    asyncio.run(TK.TashkeelStage(None, 1, [item()], agent(written()), never(), never(), cache).run())
    again = asyncio.run(TK.TashkeelStage(None, 1, [item()], never(), never(), never(), cache).run())
    assert [r.text for r in again.readings["AR_01"]] == GOOD


def test_the_reading_stage_makes_arabic_by_tashkeel_and_persian_by_its_own_engine():
    assert RS.engine("ar") == "tashkeel" and RS.engine("fa") == "persian" and RS.required("ar")
    voicing = asyncio.run(RS.voicing_of(None, "ar", TK.TashkeelResult({"AR_01": [TK.VocalWord(n, t) for n, t in enumerate(GOOD)]}), [item()]))
    assert isinstance(voicing, TK.TashkeelVoicing)


def test_arabic_keeps_the_speech_check_and_not_the_persian_listening():
    assert ai_config.ppie.fresh("ar") and ai_config.asr_after_voice("ar") and not ai_config.listens_after_voice("ar")
    assert not ai_config.asr_after_voice("fa")
