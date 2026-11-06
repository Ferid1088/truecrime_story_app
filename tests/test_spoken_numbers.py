"""Numbers are settled in the spoken version of en, de and ar (written out in words by the writer, checked by code, repaired); Persian numbers stay
with the reading stage's code. Nothing after the spoken version has to read a digit."""

from __future__ import annotations

import pytest

from app.agents.spoken import SpokenNarrator
from app.documentary import spoken as SP


@pytest.mark.parametrize("language,text,left", [
    ("en", "He waited 21 seconds, then left in 1998 at 3:45 with 40% of it.", ["21", "1998", "3:45", "40%"]),
    ("de", "Er wartete 21 Sekunden und ging am 3. März.", ["21", "3"]),
    ("ar", "انتظر 21 ثانية ثم رحل عام ١٩٩٨.", ["21", "١٩٩٨"]),
])
def test_digits_left_finds_every_number_still_written_as_digits(language, text, left):
    assert SP.digits_left(text, language) == left


@pytest.mark.parametrize("language,text", [("en", "He waited twenty-one seconds."), ("de", "Er wartete einundzwanzig Sekunden."),
                                           ("ar", "انتظر إحدى وعشرين ثانية.")])
def test_a_text_with_its_numbers_in_words_is_clean(language, text):
    assert SP.digits_left(text, language) == []


def test_persian_numbers_are_not_this_stages_business():
    assert SP.digits_left("او ۲۱ ثانیه صبر کرد و 911 را گرفت", "fa") == []
    assert SP.numbers_rule("fa") == ""


@pytest.mark.parametrize("language,example", [("en", "twenty-one seconds"), ("de", "einundzwanzig Sekunden"), ("ar", "ثلاث ساعات")])
def test_the_writer_is_told_how_to_write_numbers_in_its_own_language(language, example):
    system = SP.writer_system_prompt(language)
    assert example in system and "NUMBERS" in system and "{numbers_rule}" not in system


def test_the_persian_writer_prompt_has_no_numbers_rule():
    assert "NUMBERS (this is where they are settled)" not in SP.writer_system_prompt("fa")


def test_a_beat_with_a_digit_goes_back_for_repair_and_the_metrics_name_it():
    ear = SP.read_aloud_metrics("He waited 21 seconds.", "en")
    assert ear["digits"] == ["21"]
    entry = {"meaning_ok": True, "length": {"too_long": False}, "verdict": "storyteller", "problems": [], "ear": ear}
    assert SpokenNarrator._needs_repair(entry)
    clean = {**entry, "ear": SP.read_aloud_metrics("He waited twenty-one seconds.", "en")}
    assert not SpokenNarrator._needs_repair(clean)


def test_a_tag_is_not_spoken_so_digits_inside_it_are_not_numbers():
    assert SP.digits_left("He left [calm] at once [marker:7f3a9]", "en") == []
