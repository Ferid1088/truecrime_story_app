"""The exact part of the reading loop: numbered words, the comparison of two readings, the film table built from the parts of a name, and the rules
code enforces on an agent's answer. The names are invented."""

from __future__ import annotations

import pytest

from app.documentary.reading import answer as ANS
from app.documentary.reading import scheme
from app.documentary.reading.table import FilmTable, FilmTableError
from app.documentary.reading.words import tokenize

READER = {"people": [
    {"id": "sara", "first": {"fa": "سارا", "latin": "sārā"}, "last": {"fa": "مرادی", "latin": "morādi"}},
    {"id": "reza", "first": {"fa": "رضا", "latin": "rezā"}, "last": {"fa": "کریمی", "latin": "karimi"}, "titles": [{"fa": "قاضی", "latin": "qāzi"}]}],
    "places": [{"fa": "تیپ‌سیتی", "latin": "tip siti"}], "foreign": []}


def test_words_are_numbered_once_and_punctuation_tags_and_numbers_are_told_apart():
    words = tokenize("[calm] بعد، سارا ۹۱۱ را «گفت». ...")
    assert [w.plain for w in words] == ["بعد", "سارا", "۹۱۱", "را", "گفت"]
    assert [w.n for w in words] == [0, 1, 2, 3, 4]
    assert words[0].after == "،" and words[4].after == "»...."
    assert [w.number for w in words] == [False, False, True, False, False]


def test_the_key_of_a_word_ignores_vowel_marks_the_half_space_and_the_arabic_forms():
    assert tokenize("درِ")[0].key == tokenize("در")[0].key and tokenize("تیپ‌سیتی")[0].key == tokenize("تیپسیتی")[0].key
    assert tokenize("كتاب")[0].key == tokenize("کتاب")[0].key and tokenize("درِ")[0].plain == "در" and tokenize("درِ")[0].surface == "درِ"


def test_two_readings_say_the_same_when_their_sounds_and_ezafe_are_the_same():
    assert scheme.same_reading("rad-e", "rad-e") and scheme.same_reading("khāne-ye", "khāne-e") and scheme.same_reading("Kāleb", "kāleb")
    assert scheme.same_reading("khashm", "xashm") and scheme.same_reading("bāze |", "bāze")
    assert not scheme.same_reading("rad-e", "rad"), "an ezafe that is not wanted is a difference"
    assert not scheme.same_reading("baham", "beham") and not scheme.same_reading("rad", "radd")
    assert not scheme.same_reading("dar-e", "dar-ye"), "after a consonant the ezafe is a short e"


def test_the_ezafe_of_a_reading_is_ye_after_a_vowel_and_e_after_a_consonant():
    assert scheme.ezafe_form("qāzi") == "qāzi-ye" and scheme.ezafe_form("dar") == "dar-e" and scheme.ezafe_form("khāne") == "khāne-ye"
    assert scheme.split_ezafe("dar-e") == ("dar", "e") and scheme.split_ezafe("dar-mi-umad") == ("dar-mi-umad", "")


def test_a_name_is_built_from_its_parts_a_title_takes_the_ezafe_and_a_first_and_last_name_do_not():
    table = FilmTable.build(READER)
    words = tokenize("سارا مرادی به قاضی رضا کریمی گفت")
    fixed = table.fixed_readings(words)
    assert fixed == {0: "sārā", 1: "morādi", 3: "qāzi-ye", 4: "rezā", 5: "karimi"}
    assert table.fixed_readings(tokenize("قاضی کریمی")) == {0: "qāzi-ye", 1: "karimi"}, "the title with the last name alone"
    assert table.fixed_readings(tokenize("تیپ‌سیتی")) == {0: "tip siti"}, "one written word may be read as several"


def test_a_name_of_several_words_whose_reading_has_another_number_of_words_is_refused():
    with pytest.raises(FilmTableError):
        FilmTable.build({"places": [{"fa": "دریای سیاه", "latin": "daryā-ye siyāh bozorg"}]})


def test_the_owners_approval_replaces_the_readers_reading_of_a_name():
    table = FilmTable.build(READER, [{"surface": "سارا", "latin": "sāra", "kind": "person"}])
    assert table.fixed_readings(tokenize("سارا آمد")) == {0: "sāra"}


def test_two_different_persons_side_by_side_are_separated_by_a_pause_but_a_full_name_or_punctuation_is_not():
    table = FilmTable.build(READER)
    assert table.pauses(tokenize("سارا رضا را دید")) == {0}
    assert table.pauses(tokenize("سارا مرادی آمد")) == set(), "one person's full name is one unit"
    assert table.pauses(tokenize("سارا، رضا را دید")) == set(), "a comma already is a break"


def block_answer(words, latins):
    return {"words": [{"n": w.n, "latin": latin, "why": "x", "confidence": "high"} for w, latin in zip(words, latins, strict=True)]}


def test_an_answer_must_have_the_words_of_the_block_once_in_their_order():
    words = tokenize("او رفت ۹۱۱")
    got = ANS.parse(block_answer(words, ["u", "raft", "x"]), words)
    assert [r.latin for r in got] == ["u", "raft", "NUM"], "a number is read by code"
    for bad in ({"words": [{"n": 0, "latin": "u"}, {"n": 1, "latin": "raft"}]},                   # a word dropped
                {"words": [{"n": 0, "latin": "u"}, {"n": 2, "latin": "raft"}, {"n": 1, "latin": "x"}]},   # out of order
                {"words": [{"n": 0, "latin": "u"}, {"n": 1, "latin": ""}, {"n": 2, "latin": "x"}]},       # no reading
                {"nothing": 1}):
        with pytest.raises(ANS.ReadingShapeError):
            ANS.parse(bad, words)


def test_code_overwrites_the_reading_of_a_name_with_the_tables_and_adds_the_pause_between_two_persons():
    table = FilmTable.build(READER)
    words = tokenize("سارا رضا را دید")
    readings = ANS.parse(block_answer(words, ["sara", "reza", "rā", "did"]), words)
    fixed, notes = ANS.enforce(readings, words, table, pause_between_names=True)
    assert [r.latin for r in fixed] == ["sārā", "rezā", "rā", "did"] and [r.pause_after for r in fixed] == [True, False, False, False]
    assert len(notes) == 3
    plain, _ = ANS.enforce(readings, words, table, pause_between_names=False)
    assert not any(r.pause_after for r in plain)
    again, notes = ANS.enforce(fixed, words, table, pause_between_names=True)
    assert again == fixed and notes == [], "an answer that already follows the table is not changed"
