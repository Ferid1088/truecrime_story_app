"""What a correction does to the text of a block and to the words it leaves marked."""

from __future__ import annotations

import pytest

from app.documentary.block_review import edits as E

TEXT = "[serious] ولی دورِ تخت نشونه‌ای نبود. بعد رفت."


def test_a_corrected_word_is_written_in_its_place_and_the_tags_and_punctuation_stay():
    out = E.apply_words(TEXT, [E.WordEdit(1, "دُورِ"), E.WordEdit(6, "رفتش")])
    assert out == "[serious] ولی دُورِ تخت نشونه‌ای نبود. بعد رفتش."
    assert E.apply_words(TEXT, [E.WordEdit(0, None, "vali")]) == TEXT, "a finglish alone does not change the text"
    with pytest.raises(ValueError, match="no word 99"):
        E.apply_words(TEXT, [E.WordEdit(99, "x")])


def test_a_replaced_word_is_marked_and_remembers_what_it_was_and_marks_move_with_their_words():
    first = E.apply_words(TEXT, [E.WordEdit(1, "دُور")])
    marks, changed = E.remap(TEXT, first, {})
    assert changed == [1] and marks[1] == E.Mark(1, "دورِ", "دُور")
    longer = "کاش " + first                                       # a word before: the marked word moves, still marked, still remembering its first form
    marks2, changed2 = E.remap(first, longer, marks)
    assert changed2 == [0] and marks2[2] == E.Mark(2, "دورِ", "دُور") and 1 not in marks2
    again = E.apply_words(first, [E.WordEdit(1, "دُوْر")])      # corrected a second time: it still remembers the first form
    marks3, _ = E.remap(first, again, marks)
    assert marks3[1].was == "دورِ" and marks3[1].surface == "دُوْر"


def test_an_inserted_word_is_marked_and_a_removed_word_marks_nothing():
    marks, changed = E.remap("او گفت نه", "او گفت که نه", {})
    assert changed == [2] and marks[2].surface == "که" and marks[2].was == ""
    marks, changed = E.remap("او گفت که نه", "او نه", {})
    assert changed == [] and marks == {}
