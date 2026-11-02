import pytest

from app.documentary.reading.inspector import Finding, candidates, confirm, windows
from app.documentary.reading.phones import compare, expected_sounds, heard_sounds, same_heard
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


def test_a_word_the_blind_listener_heard_differently_is_a_finding_only_when_the_recogniser_hears_it_differently_too():
    words = tokenize("از عهد حرف زد")
    readings = ["az", "'ahd", "harf", "zad"]
    heard = {0: "az", 1: "ohd", 2: "harf", 3: "zad"}
    assert candidates(words, readings, heard) == [1]
    spoken = [{"word": w.surface, "start": float(i), "end": i + 0.5} for i, w in enumerate(words)]
    ohd = [("o", 1.0, 1.1), ("h", 1.1, 1.2), ("d", 1.2, 1.3)]
    ahd = [("a", 1.0, 1.1), ("h", 1.1, 1.2), ("d", 1.2, 1.3)]
    assert confirm("b", words, readings, heard, spoken, ohd) == ([Finding("b", 1, "عهد", "'ahd", "ohd", "o h d")], [])
    assert confirm("b", words, readings, heard, spoken, ahd) == ([], [Finding("b", 1, "عهد", "'ahd", "ohd", "a h d")])


def test_the_consonants_inside_a_word_are_compared_for_the_candidates_a_cluster_that_is_not_in_the_reading_is_a_fault():
    assert not compare("pat", "p r a t".split(), consonants=True).ok
    assert compare("prat", "p r a t".split(), consonants=True).ok
    assert compare("kard", "k a r d b".split(), consonants=True).ok


def test_what_a_listener_wrote_says_the_reading_apart_from_its_spelling():
    assert same_heard("baham", "baham.", "بهم") and not same_heard("baham", "beham", "بهم")
    assert same_heard("qāzi", "ghāzi,", "قاضی") and same_heard("o", "va", "و")
    assert not same_heard("bāz", "bāz-e", "باز") and not same_heard("khāne-ye", "khāne", "خانه")


def test_a_number_is_never_a_candidate_and_a_block_with_another_word_count_is_refused():
    assert candidates(tokenize("۹۱۱"), ["NUM"], {0: "nohsado yāzdah"}) == []
    with pytest.raises(ValueError):
        confirm("b", tokenize("از عهد"), ["az"], {}, [], [])


def test_a_consonant_fault_outside_the_cluster_rule_is_not_found_by_the_vowel_comparison():
    assert compare("pat", "p r a t".split()).ok
