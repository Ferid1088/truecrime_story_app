import pytest

from app.documentary.reading.inspector import Finding, inspect_block, windows
from app.documentary.reading.phones import compare, expected_sounds, heard_sounds
from app.documentary.reading.words import tokenize

# the phones are what the local recogniser wrote for the takes of film 15 (v289), words cut by the voice's own word times
FAULTS = [
    ("baham", "b e h ɛ m"), ("zhānin", "ʒ ɔ n i n ə"), ("bebakhshadesh", "b e b a x ʃ t a ʃ"), ("'ahdhā", "o h t o v"), ("'ahd", "o h d"),
    ("gurestun", "ɡ uː r s t oː n"), ("khāne-ye", "x ɔ n e"), ("dar-e", "d æ r"),
]
SAYS_IT = [
    ("baham", "b a h a m"), ("zhānin", "ʒ ɔ n i n"), ("'ahdhā", "a h d ɔ"), ("'ahd", "a h d"), ("gurestun", "ɡ uː r e s t u n"), ("prat", "p r a t"),
    ("khāne-ye", "x ɔ n e j e"), ("dar-e", "d æ r e"), ("bud", "u t"), ("budan", "b u d a n a"),
]


@pytest.mark.parametrize("reading,phones", FAULTS)
def test_a_vowel_that_is_another_a_syllable_more_or_less_and_an_ezafe_missing_or_added_are_faults(reading, phones):
    assert not compare(reading, phones.split()).ok


@pytest.mark.parametrize("reading,phones", SAYS_IT)
def test_what_the_reading_says_is_not_a_fault_even_with_the_neighbours_sounds_at_the_edges(reading, phones):
    assert compare(reading, phones.split()).ok


def test_the_sounds_of_a_reading_and_of_the_phones_are_in_one_alphabet():
    assert expected_sounds("khāne-ye") == ["kh", "ā", "n", "e", "y", "e"]
    assert expected_sounds("'ahd|") == ["a", "h", "d"]
    assert heard_sounds("x ɔ n e j e".split()) == ["kh", "ā", "n", "e", "y", "e"]


def test_the_window_of_a_word_follows_the_voices_words_by_place_or_by_the_words_that_match():
    words = tokenize("از عهد حرف زد")
    spoken = [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate(["از", "/ʔæhd/", "حرف", "زد"])]
    assert windows(spoken, words) == {0: (0, 0.5), 1: (1, 1.5), 2: (2, 2.5), 3: (3, 3.5)}
    assert windows(spoken[:1] + spoken[2:], words) == {0: (0, 0.5), 2: (2, 2.5), 3: (3, 3.5)}


def test_a_block_is_inspected_word_by_word_in_the_window_of_each_word():
    frames = [("a", 0.0, 0.1), ("z", 0.1, 0.2), ("o", 1.0, 1.1), ("h", 1.1, 1.2), ("d", 1.2, 1.3), ("h", 2.0, 2.1), ("a", 2.1, 2.2), ("r", 2.2, 2.3), ("f", 2.3, 2.4)]
    spoken = [{"word": "از", "start": 0.0, "end": 0.3}, {"word": "عهد", "start": 0.95, "end": 1.35}, {"word": "حرف", "start": 1.95, "end": 2.45}]
    found = inspect_block("b", "از عهد حرف", ["az", "'ahd", "harf"], spoken, frames)
    assert found == [Finding("b", 1, "عهد", "a h d", "o h d")]


def test_a_number_is_never_inspected_and_a_block_with_another_word_count_is_refused():
    assert inspect_block("b", "۹۱۱", ["NUM"], [{"word": "۹۱۱", "start": 0, "end": 1}], [("a", 0, 1)]) == []
    with pytest.raises(ValueError):
        inspect_block("b", "از عهد", ["az"], [], [])


def test_a_consonant_fault_is_not_found_because_only_the_vowels_are_compared():
    assert compare("pat", "p r a t".split()).ok
