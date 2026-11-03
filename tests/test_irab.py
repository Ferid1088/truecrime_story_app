"""The second text: two editors vowelize a block, code enforces the rules, the disputed words are decided or marked uncertain. The words are invented
and the models' answers are scripted."""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from app.core.ai_config import ai_config
from app.documentary.irab import rules as R
from app.documentary.irab.maker import IrabMaker
from app.documentary.reading import ask as ASK
from app.documentary.reading.block import BlockInput
from app.documentary.reading.words import substitute, tokenize


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


class Script:
    def __init__(self, answer):
        self.answer, self.requests = answer, []

    async def __call__(self, gen, body):
        self.requests.append(json.loads(body))
        return (self.answer(self.requests[-1]) if callable(self.answer) else self.answer), object()


TEXT = "[calm] او کتاب را برد، ۹۱۱ بود."
ITEM = BlockInput("B1", "S1", TEXT, english="He took the book. It was 911.")


def words(*rows):
    return {"words": [{"n": n, "irab": irab, "said": said} for n, irab, said in rows]}


GOOD = words((0, "او", ""), (1, "کِتاب", ""), (2, "را", ""), (3, "بُرد", ""), (4, "۹۱۱", "نُه یک یک"), (5, "بود", ""))


def maker(tmp_path, monkeypatch, write, check, arbiter=None, approved=None):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    return IrabMaker(None, 1, 7, "fa", approved, write_runner=write, check_runner=check, arbiter_runner=arbiter or Script({"words": []}))


def run(m, items=None):
    return asyncio.run(m.run(items or [ITEM]))["B1"]


def test_two_editors_that_agree_give_the_vowelized_text_and_the_text_the_voice_is_given(tmp_path, monkeypatch):
    block = run(maker(tmp_path, monkeypatch, Script(GOOD), Script(GOOD)))
    assert block.original == TEXT
    assert block.irab_text == "[calm] او کِتاب را بُرد، ۹۱۱ بود."
    assert block.second_text == "[calm] او کِتاب را بُرد، نُه یک یک بود.", "the number is said in words, the tag and the punctuation stay"
    assert {w.status for w in block.words} == {"agreed"} and not block.uncertain


def test_the_editors_do_not_see_each_others_answer_and_the_arbiter_is_not_asked_when_they_agree(tmp_path, monkeypatch):
    write, check, arb = Script(GOOD), Script(GOOD), Script({"words": []})
    run(maker(tmp_path, monkeypatch, write, check, arb))
    assert "problems" not in write.requests[0] and write.requests[0] == check.requests[0] and arb.requests == []
    assert write.requests[0]["english_source"] == ITEM.english and [w["text"] for w in write.requests[0]["words"]][:2] == ["او", "کتاب"]


def test_a_word_they_differ_on_goes_to_a_decision_by_the_sentence_and_the_choice_is_kept(tmp_path, monkeypatch):
    other = words((0, "او", ""), (1, "کِتاب", ""), (2, "را", ""), (3, "بَرد", ""), (4, "۹۱۱", "نُه یک یک"), (5, "بود", ""))
    arb = Script({"words": [{"n": 3, "choice": "B", "sure": True}]})
    block = run(maker(tmp_path, monkeypatch, Script(GOOD), Script(other), arb))
    word = block.words[3]
    assert (word.irab, word.status, word.other["irab"]) == ("بَرد", "resolved", "بُرد")
    assert arb.requests[0]["words"][0]["A"]["irab"] == "بُرد" and arb.requests[0]["words"][0]["B"]["irab"] == "بَرد"
    assert len(arb.requests[0]["words"]) == 1, "only the disputed word is asked"


def test_a_word_nobody_can_settle_keeps_the_first_answer_and_is_marked_uncertain_for_the_owner(tmp_path, monkeypatch):
    other = words((0, "او", ""), (1, "کِتاب", ""), (2, "را", ""), (3, "بَرد", ""), (4, "۹۱۱", "نُه یک یک"), (5, "بود", ""))
    block = run(maker(tmp_path, monkeypatch, Script(GOOD), Script(other), Script({"words": [{"n": 3, "choice": "A", "sure": False}]})))
    assert block.words[3].status == "uncertain" and block.words[3].irab == "بُرد" and [w.n for w in block.uncertain] == [3]


def test_an_answer_that_changes_a_letter_or_leaves_a_number_in_digits_is_refused_and_asked_again(tmp_path, monkeypatch):
    bad = words((0, "او", ""), (1, "کُتاب", ""), (2, "را", ""), (3, "بُردد", ""), (4, "۹۱۱", ""), (5, "بود", ""))
    write = Script(lambda req: GOOD if "problems" in req else bad)
    run(maker(tmp_path, monkeypatch, write, Script(GOOD)))
    asked = write.requests[1]["problems"]
    assert any("changes the letters" in p for p in asked) and any("number" in p for p in asked) and len(write.requests) == 2


def test_a_word_one_editor_cannot_get_right_takes_the_other_editors_answer_and_is_marked_single(tmp_path, monkeypatch):
    bad = words((0, "او", ""), (1, "کتابب", ""), (2, "را", ""), (3, "بُرد", ""), (4, "۹۱۱", "نُه یک یک"), (5, "بود", ""))
    block = run(maker(tmp_path, monkeypatch, Script(bad), Script(GOOD)))
    assert block.words[1].irab == "کِتاب" and block.words[1].status == "single" and [w.n for w in block.uncertain] == [1]


def test_a_word_neither_editor_gets_right_stays_as_written_and_is_marked_uncertain_instead_of_stopping_the_film(tmp_path, monkeypatch):
    bad = words((0, "او", ""), (1, "کتابب", ""), (2, "را", ""), (3, "بُرد", ""), (4, "۹۱۱", "نُه یک یک"), (5, "بود", ""))
    block = run(maker(tmp_path, monkeypatch, Script(bad), Script(bad)))
    assert block.words[1].irab == "کتاب" and block.words[1].status == "uncertain" and "changes the letters" in block.words[1].other["why"]
    assert block.second_text == "[calm] او کتاب را بُرد، نُه یک یک بود."


def test_only_the_words_that_broke_a_rule_are_asked_again_with_the_whole_block_for_context(tmp_path, monkeypatch):
    bad = words((0, "او", ""), (1, "کتابب", ""), (2, "را", ""), (3, "بُرد", ""), (4, "۹۱۱", ""), (5, "بود", ""))
    again = words((1, "کِتاب", ""), (4, "۹۱۱", "نُه یک یک"))
    write = Script(lambda req: again if "problems" in req else bad)
    block = run(maker(tmp_path, monkeypatch, write, Script(GOOD)))
    second = write.requests[1]
    assert [w["n"] for w in second["words"]] == [1, 4] and second["block_text"] == "او کتاب را برد، ۹۱۱ بود." and len(second["problems"]) == 2
    assert block.words[1].irab == "کِتاب" and block.words[4].said == "نُه یک یک" and {w.status for w in block.words} == {"agreed"}


def test_a_word_the_owner_approved_is_respelled_by_code_and_the_ezafe_of_the_irab_goes_with_it(tmp_path, monkeypatch):
    item = BlockInput("B1", "S1", "عهد او و عهد", english="")
    write = Script(words((0, "عَهدِ", ""), (1, "او", ""), (2, "و", ""), (3, "عَهد", "")))
    block = run(maker(tmp_path, monkeypatch, write, write, approved={"عهد": "'ahd"}), [item])
    assert [w.voiced for w in block.words] == ["اَهدِ", "او", "و", "اَهد"] and block.words[0].status == "approved"
    assert block.irab_text == "عَهدِ او و عَهد" and block.second_text == "اَهدِ او و اَهد"


def test_the_second_text_is_kept_and_not_asked_for_again_until_the_text_or_the_prompts_change(tmp_path, monkeypatch):
    write, check = Script(GOOD), Script(GOOD)
    first = maker(tmp_path, monkeypatch, write, check)
    run(first)
    again = maker(tmp_path, monkeypatch, write, check)
    block = run(again)
    assert len(write.requests) == 1 and block.second_text == "[calm] او کِتاب را بُرد، نُه یک یک بود."
    changed = BlockInput("B1", "S1", TEXT + " تمام", english=ITEM.english)
    other = Script(GOOD)
    run(maker(tmp_path, monkeypatch, other, Script(GOOD)), [changed])
    assert other.requests, "a changed text is asked for again"


@pytest.mark.parametrize("irab,said,ok", [
    ("کِتاب", "", True), ("کتاب", "", True), ("کِتابب", "", False), ("", "", False), ("کِتاب", "ketab", False), ("کِتاب", "کتاب ۱", False),
    ("کِتاب", "کِتاب", True), ("کَتاب", "", True),
])
def test_the_rules_for_one_answer(irab, said, ok):
    word = next(w for w in tokenize("کتاب") if w.n == 0)
    assert (R.problem_with(word, irab, said) == "") is ok


def test_the_voice_text_keeps_audio_tags_punctuation_and_spacing():
    assert substitute("[calm] الف، ب. [slowly] ج", {0: "x", 1: "y", 2: "z"}) == "[calm] x، y. [slowly] z"


def test_a_comma_the_editor_puts_inside_a_spoken_number_is_a_space(tmp_path, monkeypatch):
    spoken = words((0, "او", ""), (1, "کِتاب", ""), (2, "را", ""), (3, "بُرد", ""), (4, "۹۱۱", "نُه، یک، یک"), (5, "بود", ""))
    block = run(maker(tmp_path, monkeypatch, Script(spoken), Script(GOOD)))
    assert block.words[4].said == "نُه یک یک" and block.second_text == "[calm] او کِتاب را بُرد، نُه یک یک بود."


def test_a_reading_of_the_owner_that_already_ends_in_the_ezafe_does_not_get_a_second_one(tmp_path, monkeypatch):
    item = BlockInput("B1", "S1", "عهدهای او", english="")
    write = Script(words((0, "عَهدهایِ", ""), (1, "او", "")))
    block = run(maker(tmp_path, monkeypatch, write, write, approved={"عهدهای": "'ahdhāye"}), [item])
    assert block.words[0].voiced == "اَهدهایه", "the reading says ahdhaye once"
