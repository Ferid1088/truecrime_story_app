"""The number readers are driven by config voice.number_words: every language that needs one has its block, and every block is a valid set of options."""
import pytest

from app.core.ai_config import ai_config
from app.documentary.spoken_numbers import reader_for


def test_every_new_film_language_and_german_has_a_number_reader_block():
    assert set(ai_config.voice.number_words) >= {*ai_config.multilingual.optional_production_languages, "de"}


@pytest.mark.parametrize("language", sorted(ai_config.voice.number_words))
def test_the_block_of_a_language_builds_its_reader_and_it_says_the_year_in_words(language):
    reader = reader_for(language, ai_config.voice.number_words[language], ai_config.voice.number_max)
    assert not any(c.isdigit() for c in reader.text("2007"))


def test_a_misspelled_option_is_refused_and_not_ignored():
    with pytest.raises(Exception):
        reader_for("es", {"emergency_number": "number_always"}, ai_config.voice.number_max)
